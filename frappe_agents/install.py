# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import os

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
	"""This app's public sidebar here, under whatever name it now carries.

	Resolved **by module**, because that is how every consumer resolves it —
	`add_access_sidebar_links.app_sidebars` filters on `module` and `for_user`,
	and `adopt_sidebar_for_app` loops over what it returns. A guard that asked a
	different question could say "nothing here" about a sidebar they can all see.

	It could, and it did. `Workspace Sidebar` is `allow_rename: 1` with
	`autoname: field:title` and an `after_rename` of its own, so a Workspace
	Manager renaming "Agents" to "AI Agents" in the desk is a supported action —
	and the renamed row keeps `module`. Against the old two-name guard both
	literals were then False, so the after_migrate self-heal inserted a SECOND
	public sidebar called "Agents": two candidates for every list view, both
	sidebar patches appending into both, and — because the self-heal runs on
	every migrate rather than once — another one waiting on the next migrate.

	`for_user` is excluded. A sidebar with it set is somebody's personal copy,
	which is never the app's public sidebar, and counting one would make the
	build refuse on a site that has no public sidebar at all.

	The two names are still asked about, for what is now a different question.
	The build inserts under `WORKSPACE`, so a row already sitting on either name
	is a name collision whatever module it carries — a sidebar built before
	`module` was said out loud carries none, and the module query cannot see it.
	"""
	by_module = frappe.get_all(
		SIDEBAR_DOCTYPE,
		filters={"module": APP_MODULE, "for_user": ("in", ("", None))},
		pluck="name",
		order_by="creation asc",
	)
	if by_module:
		return by_module[0]

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


def desktop_icon_path() -> str:
	"""Where the shipped tile lives, derived the way frappe looks for it.

	The name is load-bearing: `remove_orphan_entities` deletes a standard Desktop
	Icon whose file is not `desktop_icon/<scrub(name)>.json`, which is what
	`tests/test_workspace_naming.py` pins. Deriving the path the same way keeps
	the two from drifting apart.
	"""
	return os.path.join(
		frappe.get_app_path("frappe_agents"), "desktop_icon", f"{frappe.scrub(WORKSPACE)}.json"
	)


def desktop_icon_fields() -> dict:
	"""The shipped tile transcribed as fields, for when its file is not there.

	A faithful copy of `desktop_icon/agents.json` minus the sync bookkeeping
	(`doctype`, `modified`, the empty `roles` table). Written out literally rather
	than assembled from other constants so there is one obvious place to look when
	the JSON changes — and `tests/test_workspace_naming.py` compares the two field
	by field, so they cannot drift apart quietly.

	Built on each call, not frozen at import, so `WORKSPACE` is read now.
	"""
	return {
		"label": WORKSPACE,
		"link_to": WORKSPACE,
		"link_type": "Workspace Sidebar",
		"icon_type": "App",
		"icon": "frappe_agents",
		"app": "frappe_agents",
		# the tile's own artwork, not the apps-screen logo in hooks.py
		"logo_url": "/assets/frappe_agents/icons/desktop_icons/solid/frappe_agents.svg",
		"idx": 0,
		"hidden": 0,
		"standard": 1,
		"restrict_removal": 1,
	}


def ensure_desktop_icon() -> bool:
	"""Put the desk tile back when its row has gone missing.

	The tile is shipped as `desktop_icon/<scrub(WORKSPACE)>.json` and model sync
	imports it early in every migrate, so a row missing at the end of one means
	something removed it in between. Two things remove it, and neither needs
	developer mode to be the cause:

	* `frappe.model.sync.remove_orphan_entities` deletes every standard Desktop
	  Icon whose `desktop_icon/<scrub(name)>.json` it cannot find, with
	  `force=True`. `restrict_removal` does not stop it — that flag is only read
	  by the desk's own JS, `check_for_restrict_removal` is called from nowhere
	  in frappe, so nothing enforces it server-side.
	* `DesktopIcon.after_rename` deletes `desktop_icon/<old name>.json` from the
	  app tree unconditionally, and only writes the new name's file back in
	  developer mode. `on_trash` deletes the file too, that one in developer mode.

	So losing the file is what makes losing the row permanent: with no file, model
	sync has nothing to import, and every later migrate deletes the row again. That
	is the loop the tile has fallen into four times, and it is the one state that
	cannot recover on its own — which is exactly why this refuses to give up in it.

	The shipped file stays the source of truth and is imported whenever it is
	there. Only when it is gone is the row rebuilt from `desktop_icon_fields()`,
	and even then NOTHING is written into the app tree: a migrate has no business
	authoring the app's released artifacts, and a regenerated file could differ
	from the one that shipped. What has to come back is the row — the desk renders
	the row; the file is only its usual carrier. A rebuilt row is also
	self-correcting: the next migrate that runs against an intact tree re-imports
	the file over it.

	`after_migrate` runs after `remove_orphan_entities`, so the rebuilt row is the
	last word in the migrate and the tile is on the desk when it ends.
	"""
	if frappe.db.exists("Desktop Icon", WORKSPACE):
		return False

	path = desktop_icon_path()
	shipped = os.path.exists(path)
	if not shipped:
		# Worth saying out loud: the app's own tree is missing a released file,
		# and a redeploy is the real fix. The rebuild below only keeps the desk
		# usable until then.
		print(f"frappe_agents: {path} is missing — rebuilding the {WORKSPACE} desk tile without it")

	# Same trade as the sidebar above, and it matters more here: after_migrate
	# hooks run for every app on the site, so an exception raised here fails
	# somebody else's migrate too.
	save_point = "frappe_agents_desktop_icon"
	frappe.db.savepoint(save_point)
	try:
		if shipped:
			from frappe.modules.import_file import import_file_by_path

			import_file_by_path(path, force=True, ignore_version=True)
		else:
			rebuild_desktop_icon()
	except Exception as exc:
		frappe.db.rollback(save_point=save_point)
		print(f"frappe_agents: could not restore the {WORKSPACE} desk tile — {exc}")
		return False

	frappe.db.release_savepoint(save_point)
	# import_file_by_path reports a skipped import by returning False rather than
	# raising, so the row itself is the only honest answer to "did that work".
	if not frappe.db.exists("Desktop Icon", WORKSPACE):
		print(f"frappe_agents: the {WORKSPACE} desk tile is still missing")
		return False

	print(f"frappe_agents: restored the {WORKSPACE} desk tile")
	return True


def rebuild_desktop_icon() -> None:
	"""Insert the tile from `desktop_icon_fields()`, touching no file."""
	doc = frappe.new_doc("Desktop Icon")
	doc.update(desktop_icon_fields())
	doc.flags.ignore_permissions = True
	# `link_to` is a DynamicLink into Workspace Sidebar, and `import_file` sets
	# this same flag before inserting the shipped file. Without it the two paths
	# disagree exactly where it hurts: on a site whose sidebar is also missing,
	# the file would still produce a tile and this would refuse to. The sidebar is
	# normally there — `ensure_workspace_sidebar` is the after_migrate hook right
	# before this one — but it swallows its own failures, so that site is real.
	doc.flags.ignore_links = True

	# `DesktopIcon.on_update` exports a standard icon back into the app tree on a
	# developer-mode site, and its guard is `frappe.flags.in_import`. Setting the
	# flag is not a trick: it is the same flag `frappe.modules.import_file` sets
	# around its own insert, so this rebuild lands with exactly the semantics the
	# shipped-file path has — and the app tree stays untouched in either mode.
	was_importing = frappe.flags.in_import
	frappe.flags.in_import = True
	try:
		doc.insert(ignore_permissions=True, set_name=WORKSPACE)
	finally:
		frappe.flags.in_import = was_importing
