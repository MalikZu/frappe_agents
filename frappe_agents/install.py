# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe

from frappe_agents.access.builder import seed_agents_builder
from frappe_agents.access.default_profiles import seed_default_profiles
from frappe_agents.access.exclusions import APP_MODULE
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
	seed_default_profiles()
	seed_agents_builder()
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

# What the installer called the sidebar before the rename above. Nothing writes
# it any more; the rename patch converges old sites onto WORKSPACE, and the
# sidebar patches keep this as the fallback lookup for a site whose rename has
# not run yet.
SIDEBAR_NAME = "Frappe Agents"

# The app the sidebar belongs to, and the reason every list view in this module
# shows it. The desk narrows the sidebars that link a doctype down to the ones
# whose `app` matches `frappe.boot.module_app[module]`, and falls back to the
# auto-generated module sidebar — the flat, hammer-icon one — when nothing
# survives. A sidebar without `app` never survives, so this is what keeps the
# curated sidebar on screen.
SIDEBAR_APP = "frappe_agents"
# The section the agent, its access and the things it is made of live under. The
# sidebar patch appends into it and needs to find it by the name shipped here.
BUILD_SECTION = "Build"
# The section the records an agent leaves behind live under. Same contract as
# BUILD_SECTION: a patch appends into it by this name.
ACTIVITY_SECTION = "Activity"

SIDEBAR = (
	("Link", "Home", "Workspace", WORKSPACE, "home", 0),
	("Link", "Agent Chat", "Page", "agent-chat", "messages-square", 0),
	("Section Break", "Review", None, None, "check-check", 0),
	("Link", "Pending Actions", "DocType", "Agent Action", None, 1),
	("Link", "Needs Review", "DocType", "Document Extraction", None, 1),
	("Link", "Review Quality", "Report", "Agent Action Review Quality", None, 1),
	("Section Break", BUILD_SECTION, None, None, "bot", 0),
	("Link", "Agents", "DocType", "Agent", None, 1),
	("Link", "Access Profiles", "DocType", "Agent Access Profile", None, 1),
	("Link", "Blueprints", "DocType", "Agent Blueprint", None, 1),
	("Link", "Skills", "DocType", "Agent Skill", None, 1),
	("Link", "Tools", "DocType", "Agent Tool", None, 1),
	("Link", "Settings", "DocType", "Agent Settings", None, 1),
	("Section Break", "Models", None, None, "plug", 0),
	("Link", "LLM Providers", "DocType", "LLM Provider", None, 1),
	("Link", "Model Profiles", "DocType", "LLM Model Profile", None, 1),
	("Section Break", ACTIVITY_SECTION, None, None, "activity", 0),
	("Link", "Conversations", "DocType", "Agent Conversation", None, 1),
	("Link", "Runs", "DocType", "Agent Run", None, 1),
	("Link", "Tool Calls", "DocType", "Agent Tool Call", None, 1),
)


SIDEBAR_DOCTYPE = "Workspace Sidebar"


def existing_sidebar() -> str | None:
	"""Whichever of the two names this app's sidebar carries here, or None.

	Both names have to be asked about. A site whose rename patch has not run —
	or errored — still carries "Frappe Agents", and building on top of that gives
	the module a *second* public sidebar: two candidates for every list view, and
	both sidebar patches appending into both.
	"""
	for name in (WORKSPACE, SIDEBAR_NAME):
		if frappe.db.exists(SIDEBAR_DOCTYPE, name):
			return name
	return None


def build_workspace_sidebar() -> str | None:
	"""The app's sidebar, in the desk's own section pattern.

	Only when none exists yet: the flat auto-seeded sidebar appears the first
	time someone opens the workspace, so building here, at install time, wins
	the race — and an existing sidebar is the user's to keep, never stomped.
	Returns the name it built, or None when it left one alone.

	Every non-child doctype in the module is linked, on purpose. The desk picks a
	sidebar for a list view from the sidebars that link that doctype, so one that
	is missing sends its own list view to the auto-generated module sidebar.
	`tests/test_sidebar_coverage.py` fails when a new doctype arrives without one.
	"""
	if existing_sidebar():
		return None
	doc = frappe.new_doc(SIDEBAR_DOCTYPE)
	doc.title = WORKSPACE
	doc.app = SIDEBAR_APP
	# Said out loud rather than left to the framework. `Workspace Sidebar` fills
	# `module` in before_save from whatever module most of its links belong to,
	# which happens to be right — but the sidebar patches find this app's sidebar
	# BY module, so the one field that decides whether they can ever see it again
	# is not a field to infer.
	doc.module = APP_MODULE
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
	return WORKSPACE


def ensure_workspace_sidebar() -> str | None:
	"""Build the sidebar on a site that never had one, without risking a migrate.

	`build_workspace_sidebar` only ever ran from `after_install`, which fires on
	`bench install-app` and never on an upgrade. A site installed before it landed
	and only migrated since has no Workspace Sidebar row at all — and the desk tile,
	which resolves `Desktop Icon.link_to` against that row, throws "Icon is not
	correctly configured" while `/app/agents`, every list view and every card go on
	working. The two sidebar patches only adopt an existing sidebar, so on that site
	they iterate over nothing and still report success.

	A sidebar is cosmetic and a migrate is not, so anything the insert dislikes
	rolls back to the savepoint and prints — the same trade the sidebar patches
	make. Returns the name it built, or None.
	"""
	if existing_sidebar():
		return None

	save_point = "frappe_agents_build_sidebar"
	frappe.db.savepoint(save_point)
	try:
		built = build_workspace_sidebar()
	except Exception as exc:
		frappe.db.rollback(save_point=save_point)
		print(f"frappe_agents: could not build the {WORKSPACE} workspace sidebar — {exc}")
		return None

	frappe.db.release_savepoint(save_point)
	if built:
		print(f"frappe_agents: built the {built} workspace sidebar")
	return built
