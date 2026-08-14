# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe

from frappe_agents.default_catalog import seed_default_catalog

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
	build_workspace_sidebar()
	seed_default_catalog()


def create_roles() -> None:
	"""Create the four agent roles if they are missing. Safe to run again."""
	for role_name in AGENT_ROLES:
		if frappe.db.exists("Role", role_name):
			continue

		role = frappe.new_doc("Role")
		role.role_name = role_name
		role.desk_access = 1
		role.insert(ignore_permissions=True)


# The one display name, everywhere a route or a lookup derives from it. The desk
# resolves the desktop tile by matching Desktop Icon.label against the Workspace
# Sidebar's name (lowercased), and routes the workspace by slugging its record
# name — so the workspace name, its title, the sidebar name, and the tile label
# must all be this same word or the tile 404s (the v0.5.0 regression).
WORKSPACE = "Agents"

SIDEBAR = (
	("Link", "Home", "Workspace", WORKSPACE, "home", 0),
	("Link", "Agent Chat", "Page", "agent-chat", "messages-square", 0),
	("Section Break", "Review", None, None, "check-check", 0),
	("Link", "Pending Actions", "DocType", "Agent Action", None, 1),
	("Link", "Needs Review", "DocType", "Document Extraction", None, 1),
	("Link", "Review Quality", "Report", "Agent Action Review Quality", None, 1),
	("Section Break", "Build", None, None, "bot", 0),
	("Link", "Agents", "DocType", "Agent", None, 1),
	("Link", "Skills", "DocType", "Agent Skill", None, 1),
	("Link", "Tools", "DocType", "Agent Tool", None, 1),
	("Link", "Settings", "DocType", "Agent Settings", None, 1),
	("Section Break", "Models", None, None, "plug", 0),
	("Link", "LLM Providers", "DocType", "LLM Provider", None, 1),
	("Link", "Model Profiles", "DocType", "LLM Model Profile", None, 1),
	("Section Break", "Activity", None, None, "activity", 0),
	("Link", "Conversations", "DocType", "Agent Conversation", None, 1),
	("Link", "Runs", "DocType", "Agent Run", None, 1),
)


def build_workspace_sidebar():
	"""The app's sidebar, in the desk's own section pattern.

	Only when none exists yet: the flat auto-seeded sidebar appears the first
	time someone opens the workspace, so building here, at install time, wins
	the race — and an existing sidebar is the user's to keep, never stomped.
	"""
	if frappe.db.exists("Workspace Sidebar", WORKSPACE):
		return
	doc = frappe.new_doc("Workspace Sidebar")
	doc.title = WORKSPACE
	for type_, label, link_type, link_to, icon, child in SIDEBAR:
		row = {"type": type_, "label": label, "child": child, "collapsible": 1}
		if type_ == "Section Break":
			row.update({"icon": icon, "indent": 1})
		else:
			row.update({"link_type": link_type, "link_to": link_to})
			if icon:
				row["icon"] = icon
		doc.append("items", row)
	doc.flags.ignore_permissions = True
	doc.insert(ignore_permissions=True, set_name=WORKSPACE)
