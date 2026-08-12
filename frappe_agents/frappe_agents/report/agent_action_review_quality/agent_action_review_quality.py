# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Per agent: how many writes it proposed, what humans did with them, and how
often the human changed the draft before approving it.

A high approval rate with nothing ever edited is the failure mode this report
exists to show: people clicking approve without reading.
"""

from statistics import median

import frappe
from frappe import _
from frappe.utils import add_to_date, get_datetime

# An approval that later failed to apply was still an approval: the human read it
# and said yes. Count it as approved, and as a denominator for the edit rate.
APPROVED_STATUSES = ("Approved", "Applied", "Failed")


def execute(filters: dict | None = None):
	"""Return columns and data for the report."""
	filters = frappe._dict(filters or {})

	return get_columns(), get_data(filters)


def get_columns() -> list[dict]:
	return [
		{
			"label": _("Agent"),
			"fieldname": "agent",
			"fieldtype": "Link",
			"options": "Agent",
			"width": 220,
		},
		{
			"label": _("Proposed"),
			"fieldname": "proposed",
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"label": _("Approved"),
			"fieldname": "approved",
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"label": _("Rejected"),
			"fieldname": "rejected",
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"label": _("Expired"),
			"fieldname": "expired",
			"fieldtype": "Int",
			"width": 110,
		},
		{
			"label": _("% Edited Before Approval"),
			"fieldname": "edited_before_approval_rate",
			"fieldtype": "Percent",
			"width": 200,
		},
		{
			"label": _("Median Decision Latency"),
			"fieldname": "median_decision_latency",
			"fieldtype": "Duration",
			"width": 200,
		},
	]


def get_data(filters: frappe._dict) -> list[dict]:
	actions = frappe.get_list(
		"Agent Action",
		filters=get_filters(filters),
		fields=["agent", "status", "edited_before_approval", "creation", "decided_at"],
		limit_page_length=0,
		order_by="agent asc",
	)

	rows = [summarise(agent, group) for agent, group in group_by_agent(actions).items()]
	rows.sort(key=lambda row: (-row["proposed"], row["agent"]))

	return rows


def get_filters(filters: frappe._dict) -> list[list]:
	conditions = []

	if filters.get("agent"):
		conditions.append(["agent", "=", filters.get("agent")])

	if filters.get("from_date"):
		conditions.append(["creation", ">=", get_datetime(filters.get("from_date"))])

	if filters.get("to_date"):
		# The filter is a date, so include everything proposed on that day.
		day_after = add_to_date(get_datetime(filters.get("to_date")), days=1)
		conditions.append(["creation", "<", day_after])

	return conditions


def group_by_agent(actions: list[dict]) -> dict[str, list[dict]]:
	grouped: dict[str, list[dict]] = {}

	for action in actions:
		grouped.setdefault(action.get("agent"), []).append(action)

	return grouped


def summarise(agent: str, actions: list[dict]) -> dict:
	approved = [a for a in actions if a.get("status") in APPROVED_STATUSES]
	edited = [a for a in approved if a.get("edited_before_approval")]

	return {
		"agent": agent,
		"proposed": len(actions),
		"approved": len(approved),
		"rejected": len([a for a in actions if a.get("status") == "Rejected"]),
		"expired": len([a for a in actions if a.get("status") == "Expired"]),
		"edited_before_approval_rate": percent(len(edited), len(approved)),
		"median_decision_latency": median_latency(actions),
	}


def percent(part: int, whole: int) -> float:
	if not whole:
		return 0.0

	return round(part * 100.0 / whole, 2)


def median_latency(actions: list[dict]) -> int:
	"""Median seconds between proposal and decision, over the decided actions.

	Actions nobody decided have no latency to report, so they are left out
	rather than counted as instant or as pending forever.
	"""
	latencies = []

	for action in actions:
		decided_at = get_datetime(action.get("decided_at"))
		created = get_datetime(action.get("creation"))

		if decided_at and created:
			latencies.append((decided_at - created).total_seconds())

	if not latencies:
		return 0

	return int(median(latencies))
