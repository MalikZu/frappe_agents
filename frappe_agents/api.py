"""Whitelisted endpoints for the Desk chat surface.

Nothing here runs as anyone but the session user. An Agent Run is queued, never
executed inline, and the run carries the identity it must be executed under.
"""

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, now_datetime, today

MAX_MESSAGE_CHARS = 20_000
TITLE_CHARS = 140
DESCRIPTION_CHARS = 200


@frappe.whitelist()
def list_agents() -> list[dict]:
	"""Enabled agents the session user is allowed to talk to."""
	roles = set(frappe.get_roles())
	agents = frappe.get_list(
		"Agent",
		filters={"enabled": 1},
		fields=["name", "agent_name"],
		order_by="agent_name asc",
		limit_page_length=0,
	)

	allowed = []
	for row in agents:
		agent = frappe.get_cached_doc("Agent", row.name)
		if not _user_may_use(agent, roles):
			continue
		allowed.append(
			{
				"name": agent.name,
				"agent_name": agent.agent_name,
				"autonomy": agent.autonomy,
				"description": _description(agent),
			}
		)
	return allowed


@frappe.whitelist(methods=["POST"])
def start_run(agent: str, message: str, conversation: str | None = None) -> dict:
	"""Queue one turn of a conversation with an agent. Returns the run and conversation."""
	message = (message or "").strip()
	if not message:
		frappe.throw(_("Message cannot be empty."))
	if len(message) > MAX_MESSAGE_CHARS:
		frappe.throw(
			_("Message is too long: {0} characters, limit is {1}.").format(len(message), MAX_MESSAGE_CHARS)
		)

	settings = frappe.get_cached_doc("Agent Settings")
	if not cint(settings.global_enabled):
		frappe.throw(_("The agent runtime is switched off."))

	frappe.has_permission("Agent", "read", doc=agent, throw=True)
	agent_doc = frappe.get_doc("Agent", agent)

	if not cint(agent_doc.enabled):
		frappe.throw(_("Agent {0} is disabled.").format(agent_doc.name))

	if not _user_may_use(agent_doc, set(frappe.get_roles())):
		raise frappe.PermissionError(_("You are not allowed to use the agent {0}.").format(agent_doc.name))

	if agent_doc.run_as != "Session User":
		frappe.throw(
			_("Agent {0} runs as a service user and cannot be started from chat.").format(agent_doc.name)
		)

	_check_budget(agent_doc, settings)

	conversation_doc = _get_conversation(agent_doc, conversation, message)

	run = frappe.get_doc(
		{
			"doctype": "Agent Run",
			"agent": agent_doc.name,
			"conversation": conversation_doc.name,
			"effective_user": frappe.session.user,
			"run_as_mode": "Session User",
			"surface": "Desk Chat",
			"status": "Queued",
			"depth": 0,
			"input_message": message,
		}
	)
	run.flags.ignore_permissions = True
	run.insert(ignore_permissions=True)

	conversation_doc.flags.ignore_permissions = True
	conversation_doc.db_set("last_activity", now_datetime(), update_modified=False)

	frappe.enqueue(
		"frappe_agents.runner.run.execute_run",
		queue="long",
		enqueue_after_commit=True,
		run_name=run.name,
	)

	return {"run": run.name, "conversation": conversation_doc.name}


@frappe.whitelist()
def get_conversation(conversation: str) -> dict:
	"""A conversation and its runs, as the session user is permitted to see them."""
	frappe.has_permission("Agent Conversation", "read", doc=conversation, throw=True)
	doc = frappe.get_doc("Agent Conversation", conversation)

	runs = frappe.get_list(
		"Agent Run",
		filters={"conversation": doc.name},
		fields=["name", "status", "input_message", "output_message", "error", "creation"],
		order_by="creation asc",
		limit_page_length=0,
	)

	return {
		"name": doc.name,
		"agent": doc.agent,
		"title": doc.title,
		"last_activity": doc.last_activity,
		"runs": runs,
	}


def _user_may_use(agent: Any, roles: set) -> bool:
	# Agent User is the baseline: it carries the doctype permissions that make
	# conversations and runs readable. allowed_roles only narrows WHICH agents.
	if "Agent User" not in roles:
		return False
	allowed_roles = {row.role for row in (agent.get("allowed_roles") or []) if row.role}
	return not allowed_roles or bool(allowed_roles & roles)


def _description(agent: Any) -> str:
	instructions = (agent.instructions or "").strip()
	if not instructions:
		return ""
	first_line = instructions.splitlines()[0].strip()
	return first_line[:DESCRIPTION_CHARS]


def _check_budget(agent: Any, settings: Any) -> None:
	budget = cint(agent.daily_token_budget) or cint(settings.default_daily_token_budget)
	if not budget:
		return

	# Budget is per agent per day across all its users, so this counts every run.
	rows = frappe.get_all(
		"Agent Run",
		filters={"agent": agent.name, "creation": (">=", today())},
		fields=["tokens_in", "tokens_out"],
		limit_page_length=0,
	)
	used = sum(cint(row.tokens_in) + cint(row.tokens_out) for row in rows)
	if used >= budget:
		frappe.throw(
			_("Agent {0} has used its daily token budget ({1} of {2}). Try again tomorrow.").format(
				agent.name, used, budget
			)
		)


def _get_conversation(agent: Any, conversation: str | None, message: str) -> Any:
	if conversation:
		frappe.has_permission("Agent Conversation", "write", doc=conversation, throw=True)
		doc = frappe.get_doc("Agent Conversation", conversation)
		if doc.user != frappe.session.user:
			raise frappe.PermissionError(_("This conversation belongs to another user."))
		if doc.agent != agent.name:
			frappe.throw(_("This conversation belongs to agent {0}.").format(doc.agent))
		return doc

	doc = frappe.get_doc(
		{
			"doctype": "Agent Conversation",
			"agent": agent.name,
			"user": frappe.session.user,
			"title": message[:TITLE_CHARS],
			"last_activity": now_datetime(),
		}
	)
	doc.insert()
	return doc
