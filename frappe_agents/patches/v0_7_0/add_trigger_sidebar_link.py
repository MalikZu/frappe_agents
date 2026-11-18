# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Put Triggers in the sidebar of sites that already have one.

A fresh install gets the link from `install.build_workspace_sidebar`. An older
site's sidebar is the user's, so this appends one link into the Build section,
after Agents, and touches nothing else. Running it twice adds nothing.
"""

from frappe_agents.install import sidebars_supported
from frappe_agents.patches.v0_6_0.add_access_sidebar_links import _add_links, app_sidebars

NEW_LINKS = (("Triggers", "Agent Trigger"),)


def execute() -> None:
	# frappe_agents patch (version-15): no sidebar doctype, nothing to link.
	if not sidebars_supported():
		return
	for name in app_sidebars():
		_add_links(name, NEW_LINKS)
