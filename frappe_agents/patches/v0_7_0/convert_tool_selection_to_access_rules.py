# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Move existing agents from tool selection to the access matrix.

Before the matrix, an agent's access was the list of tools it held: selecting
`search_documents` meant reading every doctype its user could read, and nothing
narrowed that further. The matrix replaces the selection for the generic tool
family, so every agent already on a site needs its selection written down as
rules — otherwise it runs forever on the legacy shim in `access.grants`.

**What a selection can and cannot say.** A selected tool names a verb, so the
verbs convert exactly. It does not name a target, and this app never had a
tool-scope table to read one from: `Agent DocType Scope` rows live on
`form_doctypes` (which forms show the Ask Agent button) and on a skill's
`applies_to_doctypes`, and neither ever narrowed a tool. So those two tables are
the only place an agent says which documents it is about, and they are what this
patch converts targets from. They stay where they are — dropping them would take
the Ask Agent button off the form with them.

That leaves the honest limit, and this patch refuses to guess past it. An agent
is converted only when its reach can be written down exactly:

* it names at least one doctype of its own, and
* it holds no `run_report` — a selection does not say which reports, and
  granting every report on the site to preserve one selection is not a
  conversion, it is a widening.

Anything else is left exactly as it is, still on the shim, still behaving the
way it did yesterday, and printed at migrate time so a human can write its rules
deliberately. That is the safe half of behaviour preservation: an agent whose
reach we cannot name keeps the reach it had.

Two changes ride along, both to keep behaviour identical rather than tidy:

* `update_any_draft` is set on converted update rows, because `update_draft`
  never had an owner test — any draft the user could write, the agent could
  edit. The matrix default is the narrow one, so preserving the old behaviour
  takes saying so.
* Converted rows carry no row cap. Caps are new; the tools' own maximums were
  the only limit before, and they still are.

The conversion widens the offered tool list *within a verb*: a rule granting
read offers every read tool, not only the read tool that was selected. That is
the matrix's own shape — it grants verbs on targets, not tools — and it adds no
capability the agent did not already hold.
"""

from typing import Any

import frappe
from frappe.utils import cint

from frappe_agents.access.exclusions import is_excluded
from frappe_agents.access.grants import (
	DOCTYPE_TOOL_VERBS,
	GENERIC_TOOLS,
	REPORT_TOOLS,
	TARGET_DOCTYPE,
	VERB_READ,
	VERB_UPDATE_DRAFT,
	VERBS,
)

READ_DOCUMENT = "read_document"


def execute() -> None:
	"""Convert every agent that can be converted, and say what was left behind."""
	for name in frappe.get_all("Agent", pluck="name"):
		convert_agent(frappe.get_doc("Agent", name))


def convert_agent(agent: Any) -> bool:
	"""Rewrite one agent's selection as rules. Returns whether it was converted."""
	selection = generic_selection(agent)
	if not selection:
		return False

	reason = skip_reason(agent, selection)
	rules = [] if reason else rule_rows(agent, selection)
	if not reason and not rules and selection & set(DOCTYPE_TOOL_VERBS):
		reason = "none of the doctypes it names can carry the verbs it holds"

	if reason:
		print(f"frappe_agents: {agent.name} keeps its tool selection — {reason}")
		return False

	kept = [row.tool for row in agent.get("tools") or [] if row.tool not in GENERIC_TOOLS]
	agent.set("access_rules", rules)
	agent.set("tools", [{"tool": tool} for tool in kept])
	if READ_DOCUMENT in selection:
		agent.may_read_files = 1

	agent.flags.ignore_permissions = True
	agent.save(ignore_permissions=True)
	frappe.clear_document_cache("Agent", agent.name)
	print(f"frappe_agents: {agent.name} converted to {len(rules)} access rule(s)")
	return True


def skip_reason(agent: Any, selection: set[str]) -> str | None:
	"""Why this agent must stay on the selection, or None when it can move."""
	if agent.get("access_rules"):
		return "it already carries access rules"

	if selection & REPORT_TOOLS:
		return "it may run reports, and a tool selection does not say which reports"

	doctype_tools = selection & set(DOCTYPE_TOOL_VERBS)
	if not doctype_tools:
		# Only file reading is selected. Converting it would empty the rules table
		# while leaving other apps' tools selected, which is the shim again — with
		# file access dropped on the way through.
		if any(row.tool not in GENERIC_TOOLS for row in agent.get("tools") or []):
			return "its only matrix tool reads files, and it would be left with an empty rules table"
		return None

	if not declared_doctypes(agent):
		return "it names no doctypes of its own, so its reach cannot be written down"

	return None


def generic_selection(agent: Any) -> set[str]:
	"""The selected tools whose access the matrix now decides."""
	return {row.tool for row in agent.get("tools") or [] if row.get("tool")} & GENERIC_TOOLS


def declared_doctypes(agent: Any) -> list[str]:
	"""Every doctype this agent already says it is about, in the order it says it.

	Its form scope first, then the doctypes its skills apply to. Excluded
	doctypes and doctypes that no longer exist are dropped: a rule naming either
	would be refused at validation and take the whole migration down with it.
	"""
	names: list[str] = []
	for row in agent.get("form_doctypes") or []:
		_add(names, row.get("document_type"))

	for row in agent.get("skills") or []:
		skill = _skill(row.get("skill"))
		for scope in (skill.get("applies_to_doctypes") if skill else None) or []:
			_add(names, scope.get("document_type"))

	return names


def rule_rows(agent: Any, selection: set[str]) -> list[dict]:
	"""One rule row per doctype the agent names, carrying the verbs it selected."""
	verbs = {DOCTYPE_TOOL_VERBS[tool] for tool in selection & set(DOCTYPE_TOOL_VERBS)}
	if not verbs:
		return []

	rows = []
	for doctype in declared_doctypes(agent):
		row = _rule_row(doctype, verbs)
		if row:
			rows.append(row)
	return rows


def _rule_row(doctype: str, verbs: set[str]) -> dict | None:
	"""The row for one target, minus the verbs that target cannot carry."""
	meta = frappe.get_meta(doctype)
	allowed = verbs & {VERB_READ} if cint(meta.issingle) or cint(meta.istable) else set(verbs)
	if not allowed:
		return None

	row = {"target_type": TARGET_DOCTYPE, "target": doctype}
	for verb in allowed:
		row[VERBS[verb][0]] = 1
	if VERB_UPDATE_DRAFT in allowed:
		row["update_any_draft"] = 1
	return row


def _add(names: list[str], doctype: str | None) -> None:
	doctype = (doctype or "").strip()
	if not doctype or doctype in names:
		return
	if is_excluded(doctype) or not frappe.db.exists("DocType", doctype):
		return
	names.append(doctype)


def _skill(name: str | None) -> Any:
	if not name:
		return None
	try:
		return frappe.get_cached_doc("Agent Skill", name)
	except frappe.DoesNotExistError:
		return None
