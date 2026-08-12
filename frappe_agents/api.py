"""Whitelisted endpoints for the Desk chat surface.

Nothing here runs as anyone but the session user. An Agent Run is queued, never
executed inline, and the run carries the identity it must be executed under.
"""

from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, get_datetime, now_datetime, today

from frappe_agents.actions import APPROVER_ROLE, separation_of_duties_block

MAX_MESSAGE_CHARS = 20_000
TITLE_CHARS = 140
DESCRIPTION_CHARS = 200

DEFAULT_PENDING_LIMIT = 50
MAX_PENDING_LIMIT = 200


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
def start_run(
	agent: str,
	message: str,
	conversation: str | None = None,
	context_doctype: str | None = None,
	context_name: str | None = None,
) -> dict:
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

	context_doctype, context_name = _validate_context(context_doctype, context_name)

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
			"context_doctype": context_doctype,
			"context_name": context_name,
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


@frappe.whitelist()
def list_pending_actions(limit: int | None = None) -> list[dict]:
	"""The proposals waiting on this approver, oldest first.

	Reads through `frappe.get_list`, so an approver only sees the rows their
	permissions already allow. Each row carries whether *this* user may decide it —
	the separation of duties is a property of the pair, not of the row, and the
	queue should say so before the approver opens anything.
	"""
	if APPROVER_ROLE not in set(frappe.get_roles()):
		raise frappe.PermissionError(
			_("You need the {0} role to review agent proposals.").format(APPROVER_ROLE)
		)

	rows = frappe.get_list(
		"Agent Action",
		filters={"status": "Pending"},
		fields=[
			"name",
			"action_type",
			"target_doctype",
			"target_name",
			"reason",
			"agent",
			"run",
			"requested_by",
			"proposal_modified",
			"creation",
		],
		order_by="creation asc",
		limit_page_length=_pending_limit(limit),
	)

	for row in rows:
		block = separation_of_duties_block(frappe._dict(row))
		row["can_decide"] = 0 if block else 1
		row["blocked_because"] = block
		# What the approver will be asked to send back as expected_modified, and the
		# first hint that the document moved since the agent proposed it.
		row["target_modified"] = _target_modified(row)
		row["edited_since_proposal"] = (
			0 if _same_instant(row["target_modified"], row.get("proposal_modified")) else 1
		)

	return rows


def _pending_limit(limit: Any) -> int:
	limit = cint(limit) or DEFAULT_PENDING_LIMIT
	return max(1, min(limit, MAX_PENDING_LIMIT))


def _target_modified(row: dict) -> Any:
	doctype, name = row.get("target_doctype"), row.get("target_name")
	if not doctype or not name:
		return None
	if not frappe.has_permission(doctype, "read", doc=name):
		return None
	return frappe.db.get_value(doctype, name, "modified")


def _same_instant(left: Any, right: Any) -> bool:
	left, right = get_datetime(left), get_datetime(right)
	return bool(left) and bool(right) and left == right


def _validate_context(doctype: Any, name: Any) -> tuple[str | None, str | None]:
	"""A run may only be opened on a document the user can already read.

	The check happens here, before anything is written: a queued run seeded with a
	document its user cannot read would hand that document to the agent under the
	user's own identity.
	"""
	doctype = (doctype or "").strip() if isinstance(doctype, str) else ""
	name = str(name).strip() if name not in (None, "") else ""

	if not doctype and not name:
		return None, None
	if not doctype or not name:
		frappe.throw(_("A run on a document needs both context_doctype and context_name."))

	if not frappe.db.exists("DocType", doctype):
		frappe.throw(_("No such DocType: {0}").format(doctype))
	if not frappe.db.exists(doctype, name):
		frappe.throw(_("No such document: {0} {1}").format(doctype, name))

	frappe.has_permission(doctype, "read", doc=name, throw=True)
	return doctype, name


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
