# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Carry the Patch Log across the patch modules that were renamed.

Six patches shipped inside v0.6.0 from a package called `v0_7_0` — named for the
release the work was expected to land in, not the one it landed in. They now sit
in `v0_6_0`, which is the truth. Frappe records an executed patch by its dotted
module string and skips a patch whose string is already logged, so the rename on
its own makes all six look unrun and executes them again on every site that has
already taken them.

Two of the six have teeth on a second run. `flip_openai_to_responses_wire` is the
app's one sanctioned exception to never clobbering a row a human typed, so it
would silently undo an admin who had deliberately put a provider back on the
compat wire; `convert_tool_selection_to_access_rules` would convert a legacy-mode
agent created since the upgrade and narrow its reach to the doctypes it names.
Neither belongs in a patch release.

So: for every old string this site carries, record the new one. Nothing runs, and
nothing is undone. On a fresh install there is no old string to find — install
marks the shipped patch list as executed under the new names already — so this
does nothing there.

Runs pre_model_sync, and first, because the six it covers are post_model_sync:
the Patch Log has to say "executed" before frappe reads it to decide what to run.
"""

import frappe

# Where each of the six used to be listed, and where it is listed now. Same file,
# same behaviour, same release — only the package name changed.
MOVED = (
	"seed_default_access_profiles",
	"convert_tool_selection_to_access_rules",
	"seed_agents_builder",
	"add_access_sidebar_links",
	"adopt_sidebar_for_app",
	"flip_openai_to_responses_wire",
)

OLD_PACKAGE = "frappe_agents.patches.v0_7_0"
NEW_PACKAGE = "frappe_agents.patches.v0_6_0"

RENAMED = {f"{OLD_PACKAGE}.{name}": f"{NEW_PACKAGE}.{name}" for name in MOVED}


def execute() -> None:
	# A patch log row is bookkeeping and a migrate is not, so anything this
	# dislikes rolls back to the savepoint and prints. The cost of failing here
	# is that the six re-run — today's behaviour without this patch at all — and
	# that is a far better outcome than a migrate that stops.
	save_point = "frappe_agents_patch_log_backfill"
	frappe.db.savepoint(save_point)
	try:
		recorded = _backfill()
	except Exception as exc:
		frappe.db.rollback(save_point=save_point)
		print(f"frappe_agents: could not carry the patch log over to the renamed patches — {exc}")
		return

	frappe.db.release_savepoint(save_point)
	if recorded:
		print(f"frappe_agents: recorded {recorded} renamed patch(es) as already executed")


def _backfill() -> int:
	from frappe.modules.patch_handler import update_patch_log

	recorded = 0
	for old, new in RENAMED.items():
		# `skipped` matters: frappe's own `executed()` ignores a skipped row, so a
		# patch that failed the first time must still be allowed to run again.
		if not frappe.db.exists("Patch Log", {"patch": old, "skipped": 0}):
			continue
		if frappe.db.exists("Patch Log", {"patch": new}):
			continue
		update_patch_log(new)
		recorded += 1
	return recorded
