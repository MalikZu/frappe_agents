# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe

# Every role needs the Desk: managers configure agents, users chat, approvers review
# drafts, auditors read the tool-call trail. None of them is a portal-only role.
AGENT_ROLES = (
	"Agent Manager",
	"Agent User",
	"Agent Approver",
	"Agent Auditor",
)


def after_install() -> None:
	create_roles()


def create_roles() -> None:
	"""Create the four agent roles if they are missing. Safe to run again."""
	for role_name in AGENT_ROLES:
		if frappe.db.exists("Role", role_name):
			continue

		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.insert(ignore_permissions=True)
