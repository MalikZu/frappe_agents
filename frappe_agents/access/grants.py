# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The compiled grant: what an agent may do, to which target, with which verb.

An agent's access is a permission matrix, the way frappe's own Role Permissions
are. Attached Agent Access Profiles and the agent's own rows compile into one
mapping of target to verbs, and every generic tool derives both its scope and
its *visibility* from that mapping.

Three rules hold everywhere in here:

* **The rules only ever narrow.** Effective access is the user's frappe
  permissions ∩ the compiled grant. Every tool keeps its `has_permission` call
  exactly as it was; the grant is intersected with it, never substituted for it.
* **Most permissive wins inside the matrix.** Two profiles that disagree about a
  verb resolve to the granted one — attaching a profile adds access, it does not
  take any away. Caps are the exception and resolve the other way: the smallest
  nonzero cap wins, because a cap is a narrowing.
* **Excluded doctypes deny at run time too.** A row that names one is refused
  when it is saved, and refused again here, so a row smuggled past validation by
  direct SQL still denies.

## The legacy shim

Before the matrix, an agent's access was its Agent Selected Tool rows: holding
`search_documents` meant reading any doctype the user could read. Sites still on
that shape must not change behaviour the moment this code lands, so an agent
with **no matrix rows and at least one selected tool** runs in legacy mode —
`require_grant` passes everything and exposure is the selection, exactly as
before. An agent with nothing at all is in matrix mode and holds no generic
tools, which is the deny-by-default the matrix is for.

The migration patch converts selections into rules; after it, no agent should be
in legacy mode. The shim then stays as a pure fallback for rows created outside
the patch, until its removal is decided.
"""

from typing import Any

import frappe
from frappe.utils import cint

from frappe_agents.access.exclusions import exclusion_reason, is_excluded
from frappe_agents.tools.base import (
	AUTONOMY_CAPABILITIES,
	CAPABILITY_DRAFT,
	ToolDenied,
	current_run,
)

TARGET_DOCTYPE = "DocType"
TARGET_REPORT = "Report"

VERB_READ = "read"
VERB_CREATE_DRAFT = "create_draft"
VERB_UPDATE_DRAFT = "update_draft"
VERB_PROPOSE = "propose"
VERB_EXTRACT = "extract"

# The verb, the check that grants it, and the words a refusal uses.
VERBS = {
	VERB_READ: ("can_read", "read"),
	VERB_CREATE_DRAFT: ("can_create_draft", "create drafts of"),
	VERB_UPDATE_DRAFT: ("can_update_draft", "edit drafts of"),
	VERB_PROPOSE: ("can_propose", "propose submit or cancel on"),
	VERB_EXTRACT: ("can_extract", "extract into"),
}

# Reports have one verb: may run it.
REPORT_VERBS = (VERB_READ,)

# Which grant each generic tool needs before the model is even told it exists.
# A tool the compiled grant could never let the agent use is not offered: an
# offered tool is a promise, and one that always refuses is a broken one.
DOCTYPE_TOOL_VERBS = {
	"search_documents": VERB_READ,
	"get_doctype_meta": VERB_READ,
	"get_document_context": VERB_READ,
	"get_document_slice": VERB_READ,
	"find_doctypes": VERB_READ,
	"create_draft": VERB_CREATE_DRAFT,
	"create_drafts": VERB_CREATE_DRAFT,
	"update_draft": VERB_UPDATE_DRAFT,
	"propose_submit": VERB_PROPOSE,
	"propose_cancel": VERB_PROPOSE,
	"extract_document": VERB_EXTRACT,
}

PROPOSE_TOOLS = frozenset({"propose_submit", "propose_cancel"})
REPORT_TOOLS = frozenset({"run_report"})
# File reading is chat-shaped, not doctype-shaped: it rides one flag on the
# agent rather than a row per doctype.
FILE_TOOLS = frozenset({"read_document"})

# Every tool whose exposure the matrix decides. Anything else an agent holds —
# a tool another app registered — keeps riding the selection table.
GENERIC_TOOLS = frozenset(DOCTYPE_TOOL_VERBS) | REPORT_TOOLS | FILE_TOOLS


def compiled_grants(agent: Any) -> dict:
	"""Every rule attached to this agent, compiled into one mapping.

	`{"DocType": {name: verbs}, "Report": {name: verbs}}`, where verbs is a dict
	of verb to 0/1 plus the caps. Profiles first, then the agent's own rows;
	order does not matter because the merge is most-permissive-wins.

	Compiled on demand from cached documents rather than memoised: a run reads it
	a handful of times, and a stale grant is the one kind of wrong answer this
	module must not give.
	"""
	agent = _agent_doc(agent)
	grants: dict = {TARGET_DOCTYPE: {}, TARGET_REPORT: {}}
	for row in rule_rows(agent):
		_merge(grants, row)
	return grants


def rule_rows(agent: Any) -> list:
	"""The agent's rule rows: every attached profile's, then its own."""
	agent = _agent_doc(agent)
	rows = []
	for link in agent.get("access_profiles") or []:
		profile = _profile(link.get("access_profile"))
		if profile is not None:
			rows.extend(profile.get("rules") or [])
	rows.extend(agent.get("access_rules") or [])
	return rows


def in_legacy_mode(agent: Any) -> bool:
	"""Whether this agent still runs on tool selection instead of the matrix."""
	agent = _agent_doc(agent)
	return not rule_rows(agent) and bool(agent.get("tools"))


def require_grant(target: str, verb: str, target_type: str = TARGET_DOCTYPE) -> None:
	"""Refuse unless the running agent's matrix allows this verb on this target.

	The one door. Tools call it through `frappe_agents.tools.base`, never with
	logic of their own, and it is checked *beside* the frappe permission check
	each tool already makes — an intersection, not a replacement.

	Outside an agent run there is no agent to check, so this passes and the
	frappe permission check is the only gate. That is what a direct handler call
	in a test, or a tool run from the console, has always been.
	"""
	if target_type == TARGET_DOCTYPE and is_excluded(target):
		raise ToolDenied(exclusion_reason(target))

	agent = current_agent()
	if agent is None or in_legacy_mode(agent):
		return

	if not grant_for(agent, target, target_type).get(verb):
		raise ToolDenied(_refusal(agent, target, verb, target_type))


def grant_for(agent: Any, target: str, target_type: str = TARGET_DOCTYPE) -> dict:
	"""The compiled verbs for one target. An empty dict means no rule names it."""
	return compiled_grants(agent).get(target_type, {}).get(target) or {}


def granted_targets(agent: Any, verb: str = VERB_READ, target_type: str = TARGET_DOCTYPE) -> list[str]:
	"""Targets this agent's matrix allows the verb on, sorted."""
	grants = compiled_grants(agent).get(target_type, {})
	return sorted(name for name, verbs in grants.items() if verbs.get(verb))


def require_draft_ownership(doctype: str, doc: Any) -> None:
	"""Refuse another user's draft unless the rule says any draft is fair game.

	`update_any_draft` is frappe's If Owner, inverted: the default is the narrow
	one. An agent that may edit drafts edits the drafts its user made, and
	touching a colleague's half-finished record takes saying so.
	"""
	agent = current_agent()
	if agent is None or in_legacy_mode(agent):
		return

	if cint(grant_for(agent, doctype).get("update_any_draft")):
		return

	if (doc.get("owner") or "") != frappe.session.user:
		raise ToolDenied(
			f"{doctype} {doc.get('name')} was created by {doc.get('owner')}, and this agent may only "
			"edit drafts its own user created."
		)


def row_cap(target: str, tool_max: int, target_type: str = TARGET_DOCTYPE) -> int:
	"""The tool's row cap narrowed by the rule's, if the rule sets one.

	Effective cap = min(rule cap, tool max). A rule can only ever make a tool
	take less; 0 on the rule means "whatever the tool says".
	"""
	agent = current_agent()
	if agent is None or in_legacy_mode(agent):
		return tool_max

	cap = cint(grant_for(agent, target, target_type).get("max_rows_per_call"))
	return min(cap, tool_max) if cap > 0 else tool_max


def require_file_access() -> None:
	"""Refuse file reading unless the agent carries `may_read_files`."""
	agent = current_agent()
	if agent is None or in_legacy_mode(agent):
		return

	if not cint(agent.get("may_read_files")):
		raise ToolDenied(
			f"{agent.name} is not allowed to read attached files. "
			"Turn on Read Files on the agent to grant it."
		)


def exposed_tool_names(agent: Any) -> set[str]:
	"""The tools this agent is offered: the matrix decides the generic family.

	A generic tool appears iff some row makes it usable. Everything else the
	agent holds — a tool another app registered — is still selected by hand and
	passes through untouched.
	"""
	agent = _agent_doc(agent)
	selected = {row.tool for row in agent.get("tools") or [] if row.get("tool")}
	if in_legacy_mode(agent):
		return selected

	grants = compiled_grants(agent)
	doctypes = grants[TARGET_DOCTYPE].values()

	names = {tool for tool, verb in DOCTYPE_TOOL_VERBS.items() if any(verbs.get(verb) for verbs in doctypes)}
	if not proposals_allowed(agent):
		names -= PROPOSE_TOOLS
	if any(verbs.get(VERB_READ) for verbs in grants[TARGET_REPORT].values()):
		names |= REPORT_TOOLS
	if cint(agent.get("may_read_files")):
		names |= FILE_TOOLS

	return names | (selected - GENERIC_TOOLS)


def proposals_allowed(agent: Any) -> bool:
	"""Whether this agent's autonomy reaches the proposal tools at all."""
	agent = _agent_doc(agent)
	return CAPABILITY_DRAFT in AUTONOMY_CAPABILITIES.get(agent.get("autonomy"), set())


def current_agent() -> Any:
	"""The agent of the run this call belongs to, or None outside a run."""
	run = current_run()
	if run is None or not run.get("agent"):
		return None
	return frappe.get_cached_doc("Agent", run.agent)


def _merge(grants: dict, row: Any) -> None:
	"""Fold one rule row into the compiled mapping.

	Verbs union; a cap narrows. An excluded target is dropped here as well as at
	validation, so a smuggled row never even reaches the compiled grant.
	"""
	target_type = (row.get("target_type") or "").strip()
	target = (row.get("target") or "").strip()
	if target_type not in (TARGET_DOCTYPE, TARGET_REPORT) or not target:
		return
	if target_type == TARGET_DOCTYPE and is_excluded(target):
		return

	verbs = REPORT_VERBS if target_type == TARGET_REPORT else tuple(VERBS)
	compiled = grants[target_type].setdefault(target, {})
	for verb in verbs:
		fieldname = VERBS[verb][0]
		compiled[verb] = cint(compiled.get(verb)) or cint(row.get(fieldname))

	compiled["update_any_draft"] = cint(compiled.get("update_any_draft")) or cint(row.get("update_any_draft"))
	compiled["max_rows_per_call"] = _narrowest(
		compiled.get("max_rows_per_call"), row.get("max_rows_per_call")
	)


def _narrowest(current: Any, new: Any) -> int:
	"""The smaller of two caps, where 0 means no cap at all."""
	current, new = cint(current), cint(new)
	if not current:
		return new
	if not new:
		return current
	return min(current, new)


def _refusal(agent: Any, target: str, verb: str, target_type: str) -> str:
	"""The one refusal every tool gives when the matrix does not allow something."""
	if target_type == TARGET_REPORT:
		return f"{agent.name} has no access rule that lets it run the report {target}."
	words = VERBS.get(verb, (None, verb))[1]
	return f"{agent.name} has no access rule that lets it {words} {target}."


def _agent_doc(agent: Any) -> Any:
	return frappe.get_cached_doc("Agent", agent) if isinstance(agent, str) else agent


def _profile(name: str | None) -> Any:
	if not name:
		return None
	try:
		return frappe.get_cached_doc("Agent Access Profile", name)
	except frappe.DoesNotExistError:
		return None
