# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Give the workspace sidebar to sites that only ever migrated.

`build_workspace_sidebar` runs from `after_install`, which fires on
`bench install-app` and never on an upgrade. A site installed before it landed
and only migrated since arrives here with no Workspace Sidebar row at all — and
the desk tile, which resolves `Desktop Icon.link_to` against that row, throws
"Icon is not correctly configured" while /app/agents, the list views and the
cards all work. That asymmetry is why nothing caught it. The two sidebar patches
that shipped in v0.6.0 only *adopt*: both loop over the sidebars this app owns,
which on such a site is an empty list, so they do nothing and still print
Success.

post_model_sync on purpose. The sidebar links twelve doctypes, a page, a report
and the workspace, and the insert runs full link validation — pre_model_sync it
would raise on the first link the sync has not created yet and take the migrate
down with it.

The sidebar is not visible to anyone still holding a browser tab until
`bench --site <site> clear-cache` and a hard reload: both the sidebar and the
boot payload are cached.
"""

from frappe_agents.install import ensure_workspace_sidebar


def execute() -> None:
	ensure_workspace_sidebar()
