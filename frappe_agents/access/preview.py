# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The answer to "why can't my agent see X", worked out once for the form.

This module explains; it never decides. Nothing here is called from a tool and
no call is allowed or refused by it — `access/grants.py` is still the one door.
It reads the compiled grant, adds back the two things the grant deliberately
forgets, and hands the pair to the Agent form:

* **where each grant came from** — which profile, or the agent's own rows, so a
  rule nobody remembers writing has a name attached to it;
* **whether the person looking can actually use it** — the grant narrows the
  user's own frappe permissions, so a rule the current user's roles do not reach
  grants that user nothing. Those come back flagged `nullified_for_user`, which
  is the honest reading of a matrix that only ever narrows.

The flag is answered per verb, against the permission the tool itself checks:
`propose_submit` asks for read on the target and puts submitting in front of a
human, so proposing is nullified by missing read, not by missing submit.
"""

from typing import Any

import frappe
from frappe.utils import cint

from frappe_agents.access.exclusions import is_excluded
from frappe_agents.access.grants import (
	REPORT_VERBS,
	TARGET_DOCTYPE,
	TARGET_REPORT,
	VERB_CREATE_DRAFT,
	VERB_EXTRACT,
	VERB_PROPOSE,
	VERB_READ,
	VERB_UPDATE_DRAFT,
	VERBS,
	compiled_grants,
	exposed_tool_names,
	in_legacy_mode,
	proposals_allowed,
	rule_rows_with_source,
)

# The words the form puts on a granted verb.
VERB_TITLES = {
	VERB_READ: "Read",
	VERB_CREATE_DRAFT: "Create drafts",
	VERB_UPDATE_DRAFT: "Edit drafts",
	VERB_PROPOSE: "Propose submit or cancel",
	VERB_EXTRACT: "Extract into",
}
REPORT_TITLE = "Run"

# The frappe permission each verb rides on — what the tool holding that verb
# checks beside the grant. Extraction ends as a draft somebody creates, so it
# rides create; proposing rides read, because the submit is the approver's.
VERB_PTYPE = {
	VERB_READ: "read",
	VERB_CREATE_DRAFT: "create",
	VERB_UPDATE_DRAFT: "write",
	VERB_PROPOSE: "read",
	VERB_EXTRACT: "create",
}


def effective_access(agent: Any) -> dict:
	"""What this agent's matrix comes to for the user asking, row by row.

	Answered for `frappe.session.user`: two managers can get two different
	answers from the same agent, and that is the point of the flag.
	"""
	agent = frappe.get_cached_doc("Agent", agent) if isinstance(agent, str) else agent
	compiled = compiled_grants(agent)
	sources = _sources(agent)

	rows = []
	for target_type in (TARGET_DOCTYPE, TARGET_REPORT):
		for target in sorted(compiled.get(target_type) or {}):
			verbs = compiled[target_type][target]
			rows.append(_row(target_type, target, verbs, sources.get((target_type, target)) or []))

	return {
		"agent": agent.name,
		"agent_name": agent.get("agent_name") or agent.name,
		"user": frappe.session.user,
		"legacy": in_legacy_mode(agent),
		"autonomy": agent.get("autonomy"),
		"proposals_allowed": proposals_allowed(agent),
		"may_read_files": cint(agent.get("may_read_files")),
		"rows": rows,
		"tools": sorted(exposed_tool_names(agent)),
	}


def _row(target_type: str, target: str, verbs: dict, sources: list[str]) -> dict:
	"""One line of the preview: a target, what is granted on it, and by whom."""
	exists = _target_exists(target_type, target)
	names = REPORT_VERBS if target_type == TARGET_REPORT else tuple(VERBS)

	granted = []
	for verb in names:
		if not cint(verbs.get(verb)):
			continue
		granted.append(
			{
				"verb": verb,
				"label": REPORT_TITLE if target_type == TARGET_REPORT else VERB_TITLES[verb],
				"nullified_for_user": not (exists and _user_may(target_type, target, verb)),
			}
		)

	nullified = not exists or all(verb["nullified_for_user"] for verb in granted)

	return {
		"target_type": target_type,
		"target": target,
		"exists": exists,
		"verbs": granted,
		"update_any_draft": cint(verbs.get("update_any_draft")),
		"max_rows_per_call": cint(verbs.get("max_rows_per_call")),
		"sources": sources,
		"nullified_for_user": bool(granted) and nullified,
	}


def _sources(agent: Any) -> dict:
	"""Which profile — or the agent itself — put each target on the matrix.

	A row is only credited when it grants something on a target the grant keeps:
	a dead row and an excluded target are both dropped when the grant compiles,
	and crediting a profile for either would explain access nobody has.
	"""
	sources: dict = {}
	for source, row in rule_rows_with_source(agent):
		target_type = (row.get("target_type") or "").strip()
		target = (row.get("target") or "").strip()
		if target_type not in (TARGET_DOCTYPE, TARGET_REPORT) or not target:
			continue
		if target_type == TARGET_DOCTYPE and is_excluded(target):
			continue

		names = REPORT_VERBS if target_type == TARGET_REPORT else tuple(VERBS)
		if not any(cint(row.get(VERBS[verb][0])) for verb in names):
			continue

		credited = sources.setdefault((target_type, target), [])
		if source not in credited:
			credited.append(source)
	return sources


def _target_exists(target_type: str, target: str) -> bool:
	"""Whether the rule still names something. A renamed doctype grants nothing."""
	return bool(frappe.db.exists("Report" if target_type == TARGET_REPORT else "DocType", target))


def _user_may(target_type: str, target: str, verb: str) -> bool:
	"""Whether the session user's own permissions reach this verb on this target."""
	if target_type == TARGET_REPORT:
		return _may_run_report(target)
	return bool(frappe.has_permission(target, VERB_PTYPE[verb]))


def _may_run_report(name: str) -> bool:
	"""The same three questions `run_report` asks before it runs anything."""
	if not frappe.has_permission("Report", "read"):
		return False

	report = frappe.get_cached_doc("Report", name)
	if cint(report.get("disabled")):
		return False
	if not frappe.has_permission("Report", "read", doc=report):
		return False
	if not report.is_permitted():
		return False
	if report.ref_doctype and not frappe.has_permission(report.ref_doctype, "report"):
		return False
	return True
