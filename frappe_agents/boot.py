"""Desk boot payload.

The form button has to decide whether to appear before it can afford a server
call, so the list of agents that may show on forms rides along with the boot.
This hook runs on every desk load — outside the bootinfo cache — so it must stay
cheap and it must never raise: an exception here breaks the desk for that user.
"""

import frappe


def boot_form_agents(bootinfo) -> None:
	"""Attach the agents this user may open from a form, with the doctypes they cover."""
	try:
		bootinfo["frappe_agents"] = {"form_agents": _form_agents()}
	except Exception:
		bootinfo["frappe_agents"] = {"form_agents": []}
		frappe.log_error(title="frappe_agents: form agent boot failed")


def _form_agents() -> list[dict]:
	if frappe.session.user == "Guest":
		return []

	roles = set(frappe.get_roles())
	if "Agent User" not in roles:
		return []

	# The fields arrive with the app's own migration; a site mid-upgrade has none.
	if not frappe.get_meta("Agent").has_field("show_in_forms"):
		return []

	rows = frappe.get_list(
		"Agent",
		filters={"enabled": 1, "show_in_forms": 1, "run_as": "Session User"},
		fields=["name"],
		order_by="agent_name asc",
		limit_page_length=0,
	)

	agents = []
	for row in rows:
		agent = frappe.get_cached_doc("Agent", row.name)
		allowed_roles = {link.role for link in (agent.get("allowed_roles") or []) if link.role}
		if allowed_roles and not (allowed_roles & roles):
			continue
		agents.append(
			{
				"name": agent.name,
				"agent_name": agent.agent_name,
				# Empty means every doctype.
				"doctypes": [
					link.document_type for link in (agent.get("form_doctypes") or []) if link.document_type
				],
			}
		)
	return agents
