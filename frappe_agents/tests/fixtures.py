# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Fixtures for the permission tests.

The gate these tests guard is "who may see what", so they need real doctypes,
real roles, real users and real User Permissions to check against — a mocked
permission check would only prove the mock works.

Everything here is get-or-create and is rebuilt in `setUp` for every test, so it
does not matter whether the previous test's transaction rolled back or not.

Two throwaway users carry the whole permission story:

* `RESTRICTED_USER` — a User Permission pins them to one project, and they hold
  no role with permlevel 1 access.
* `OPEN_USER` — no User Permission, and a role that reads permlevel 1.
"""

from contextlib import contextmanager
from typing import Any

import frappe
from frappe.tests import IntegrationTestCase
from frappe.utils import cint

MODULE = "Frappe Agents"

READER_ROLE = "FA Test Reader"
PERMLEVEL_ROLE = "FA Test Permlevel Reader"

PROJECT_DT = "FA Test Project"
TICKET_DT = "FA Test Ticket"
VAULT_DT = "FA Test Vault"

PROJECT_ALPHA = "FA Alpha"
PROJECT_BETA = "FA Beta"
TICKET_ALPHA = "FA Ticket Alpha"
TICKET_BETA = "FA Ticket Beta"
ALPHA_SECRET = "alpha-secret"
BETA_SECRET = "beta-secret"
VAULT_RECORD = "FA Vault One"

RESTRICTED_USER = "fa-restricted@example.com"
OPEN_USER = "fa-open@example.com"
DISABLED_USER = "fa-disabled@example.com"

PROVIDER = "FA Test Provider"
PROFILE = "FA Test Profile"
AGENT = "FA Test Agent"

TOOL_NAMES = ("search_documents", "get_doctype_meta", "run_report")


class AgentTestCase(IntegrationTestCase):
	"""Base case: fixtures present, session on Administrator, kill switch on."""

	def setUp(self) -> None:
		super().setUp()
		frappe.set_user("Administrator")
		ensure_fixtures()
		self.addCleanup(frappe.set_user, "Administrator")


@contextmanager
def as_user(user: str):
	"""Run a block as `user` and put the session back afterwards."""
	original = frappe.session.user
	frappe.set_user(user)
	try:
		yield
	finally:
		frappe.set_user(original)


def ensure_fixtures() -> None:
	"""Create every fixture the tests need. Safe to call again."""
	_ensure_roles()
	_ensure_doctypes()
	_ensure_users()
	_ensure_records()
	_ensure_user_permission()
	_ensure_tools()
	_ensure_provider()
	_ensure_agent()
	set_kill_switch(1)


def set_kill_switch(enabled: int) -> None:
	"""Set Agent Settings.global_enabled and drop the cached Single.

	A Single with no stored row falls back to field defaults, which is a state the
	tests should never depend on — the first call here writes the row.
	"""
	stored = frappe.db.get_single_value("Agent Settings", "global_enabled")
	if stored is None or cint(stored) != cint(enabled):
		settings = frappe.get_doc("Agent Settings")
		settings.global_enabled = cint(enabled)
		settings.flags.ignore_permissions = True
		settings.save(ignore_permissions=True)
	frappe.clear_document_cache("Agent Settings", "Agent Settings")


def make_run(
	effective_user: str,
	agent: str = AGENT,
	conversation: str | None = None,
	message: str = "How many tickets are there?",
	depth: int = 0,
) -> Any:
	"""Insert one Agent Run. The doctype has no create permission by design."""
	run = frappe.get_doc(
		{
			"doctype": "Agent Run",
			"agent": agent,
			"conversation": conversation,
			"effective_user": effective_user,
			"run_as_mode": "Session User",
			"surface": "Desk Chat",
			"status": "Queued",
			"depth": depth,
			"input_message": message,
		}
	)
	run.flags.ignore_permissions = True
	run.insert(ignore_permissions=True)
	return run


def make_conversation(user: str, agent: str = AGENT, title: str = "FA test conversation") -> Any:
	conversation = frappe.get_doc(
		{
			"doctype": "Agent Conversation",
			"agent": agent,
			"user": user,
			"title": title,
		}
	)
	conversation.flags.ignore_permissions = True
	conversation.insert(ignore_permissions=True)
	return conversation


def tool_calls_for(run_name: str) -> list[dict]:
	"""Audit rows for one run, oldest first."""
	return frappe.get_all(
		"Agent Tool Call",
		filters={"run": run_name},
		fields=[
			"name",
			"tool",
			"outcome",
			"args_json",
			"result_summary",
			"docs_touched",
			"duration_ms",
			"error",
		],
		order_by="creation asc",
		limit_page_length=0,
	)


def _ensure_roles() -> None:
	for role_name in (READER_ROLE, PERMLEVEL_ROLE):
		if frappe.db.exists("Role", role_name):
			continue
		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.insert(ignore_permissions=True)


def _ensure_doctypes() -> None:
	_make_doctype(
		PROJECT_DT,
		"project_name",
		fields=[
			{
				"fieldname": "project_name",
				"fieldtype": "Data",
				"label": "Project Name",
				"reqd": 1,
				"unique": 1,
				"in_list_view": 1,
			}
		],
		permissions=[
			{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
			{"role": READER_ROLE, "read": 1},
		],
	)

	_make_doctype(
		TICKET_DT,
		"subject",
		fields=[
			{
				"fieldname": "subject",
				"fieldtype": "Data",
				"label": "Subject",
				"reqd": 1,
				"unique": 1,
				"in_list_view": 1,
			},
			{
				"fieldname": "project",
				"fieldtype": "Link",
				"label": "Project",
				"options": PROJECT_DT,
				"in_list_view": 1,
			},
			{
				"fieldname": "secret_note",
				"fieldtype": "Data",
				"label": "Secret Note",
				"permlevel": 1,
			},
		],
		permissions=[
			{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1},
			{"role": READER_ROLE, "read": 1},
			{"role": PERMLEVEL_ROLE, "read": 1, "permlevel": 1},
		],
	)

	# Nobody in the test cast may read this one.
	_make_doctype(
		VAULT_DT,
		"label",
		fields=[
			{
				"fieldname": "label",
				"fieldtype": "Data",
				"label": "Label",
				"reqd": 1,
				"unique": 1,
				"in_list_view": 1,
			}
		],
		permissions=[{"role": "System Manager", "read": 1, "write": 1, "create": 1, "delete": 1}],
	)


def _make_doctype(name: str, autoname_field: str, fields: list[dict], permissions: list[dict]) -> None:
	if frappe.db.exists("DocType", name):
		return
	doctype = frappe.get_doc(
		{
			"doctype": "DocType",
			"name": name,
			"module": MODULE,
			"custom": 1,
			"engine": "InnoDB",
			"autoname": f"field:{autoname_field}",
			"naming_rule": "By fieldname",
			"sort_field": "modified",
			"sort_order": "DESC",
			"fields": fields,
			"permissions": permissions,
		}
	)
	doctype.insert(ignore_permissions=True)


def _ensure_users() -> None:
	_make_user(RESTRICTED_USER, "Restricted", ["Agent User", READER_ROLE])
	_make_user(OPEN_USER, "Open", ["Agent User", READER_ROLE, PERMLEVEL_ROLE])
	_make_user(DISABLED_USER, "Disabled", ["Agent User", READER_ROLE], enabled=0)


def _make_user(email: str, first_name: str, roles: list[str], enabled: int = 1) -> None:
	if frappe.db.exists("User", email):
		return
	user = frappe.get_doc(
		{
			"doctype": "User",
			"email": email,
			"first_name": f"FA {first_name}",
			"user_type": "System User",
			"enabled": enabled,
			"send_welcome_email": 0,
			"roles": [{"role": role} for role in roles],
		}
	)
	user.flags.ignore_permissions = True
	user.insert(ignore_permissions=True)
	frappe.clear_cache(user=email)


def _ensure_records() -> None:
	for project in (PROJECT_ALPHA, PROJECT_BETA):
		if not frappe.db.exists(PROJECT_DT, project):
			frappe.get_doc({"doctype": PROJECT_DT, "project_name": project}).insert(ignore_permissions=True)

	tickets = (
		(TICKET_ALPHA, PROJECT_ALPHA, ALPHA_SECRET),
		(TICKET_BETA, PROJECT_BETA, BETA_SECRET),
	)
	for subject, project, secret in tickets:
		if frappe.db.exists(TICKET_DT, subject):
			continue
		frappe.get_doc(
			{"doctype": TICKET_DT, "subject": subject, "project": project, "secret_note": secret}
		).insert(ignore_permissions=True)

	if not frappe.db.exists(VAULT_DT, VAULT_RECORD):
		frappe.get_doc({"doctype": VAULT_DT, "label": VAULT_RECORD}).insert(ignore_permissions=True)


def _ensure_user_permission() -> None:
	existing = frappe.db.exists(
		"User Permission",
		{"user": RESTRICTED_USER, "allow": PROJECT_DT, "for_value": PROJECT_ALPHA},
	)
	if not existing:
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": RESTRICTED_USER,
				"allow": PROJECT_DT,
				"for_value": PROJECT_ALPHA,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
	frappe.clear_cache(user=RESTRICTED_USER)


def _ensure_tools() -> None:
	from frappe_agents.tools.registry import sync_tools

	if all(frappe.db.exists("Agent Tool", name) for name in TOOL_NAMES):
		return
	sync_tools()


def _ensure_provider() -> None:
	if not frappe.db.exists("LLM Provider", PROVIDER):
		frappe.get_doc(
			{
				"doctype": "LLM Provider",
				"provider_name": PROVIDER,
				"provider_type": "OpenAI Compatible",
				"base_url": "http://localhost:1/v1",
				"api_key": "fa-test-key",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)

	if not frappe.db.exists("LLM Model Profile", PROFILE):
		frappe.get_doc(
			{
				"doctype": "LLM Model Profile",
				"profile_name": PROFILE,
				"provider": PROVIDER,
				"model_id": "fa-test-model",
				"enabled": 1,
			}
		).insert(ignore_permissions=True)


def _ensure_agent() -> None:
	if frappe.db.exists("Agent", AGENT):
		# Tests that flip enabled or max_steps put them back, but a test that errors
		# out mid-way must not poison the next one.
		frappe.db.set_value(
			"Agent",
			AGENT,
			{"enabled": 1, "max_steps": 5, "autonomy": "Suggest"},
			update_modified=False,
		)
		frappe.clear_document_cache("Agent", AGENT)
		return
	agent = frappe.get_doc(
		{
			"doctype": "Agent",
			"agent_name": AGENT,
			"enabled": 1,
			"run_as": "Session User",
			"model_profile": PROFILE,
			"autonomy": "Suggest",
			"instructions": "Answer questions about tickets.",
			"max_steps": 5,
			"tools": [{"tool": name} for name in TOOL_NAMES],
		}
	)
	agent.flags.ignore_permissions = True
	agent.insert(ignore_permissions=True)
