"""Small helpers the desk asks for on every page load.

Everything here runs on the apps screen, so it stays free of queries: the roles
are already on the session cache by the time we are asked.
"""

import frappe

# Holding any one of these means the app has something to show you.
APP_ROLES = frozenset(
	{
		"Agent User",
		"Agent Manager",
		"Agent Approver",
		"Agent Auditor",
	}
)


def has_app_permission() -> bool:
	"""Return whether the session user should see the Frappe Agents tile."""
	return not APP_ROLES.isdisjoint(frappe.get_roles())
