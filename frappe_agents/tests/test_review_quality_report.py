# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The report that says whether the approvals were real.

Approving everything and editing nothing is what "humans review diffs rather than
click approve reflexively" fails to. The arithmetic is asserted by calling
`execute` directly on a seeded history; the two role gates are a different
mechanism entirely — they live in `frappe.desk.query_report.run`, which is where
they are asserted.
"""

import frappe
from frappe.desk.query_report import run as run_query_report
from frappe.utils import add_days, add_to_date, now_datetime, today

from frappe_agents.frappe_agents.report.agent_action_review_quality.agent_action_review_quality import (
	execute,
)
from frappe_agents.tests.fixtures import (
	AGENT,
	APPROVER_USER,
	AUDITOR_USER,
	DRAFT_AGENT,
	DRAFT_USER,
	OWNED_AGENT,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
	make_action,
	make_run,
)

REPORT = "Agent Action Review Quality"


class TestReviewQualityReport(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.runs = {}

		# One agent whose approvals were read: three decided, one of them edited
		# first. One agent with a shorter history. One agent nobody decided at all.
		self.seed(DRAFT_AGENT, "Applied", latency=60, edited=1)
		self.seed(DRAFT_AGENT, "Applied", latency=120)
		self.seed(DRAFT_AGENT, "Failed", latency=180)
		self.seed(DRAFT_AGENT, "Rejected", latency=300)
		self.seed(DRAFT_AGENT, "Expired")
		self.seed(DRAFT_AGENT, "Pending")

		self.seed(OWNED_AGENT, "Applied", latency=30)
		self.seed(OWNED_AGENT, "Rejected", latency=90)

		self.seed(AGENT, "Pending")

	def seed(self, agent: str, status: str, latency: int | None = None, edited: int = 0, age_days: int = 0):
		"""One Agent Action with a known age and, if it was decided, a known latency."""
		if agent not in self.runs:
			self.runs[agent] = make_run(effective_user=DRAFT_USER, agent=agent)

		action = make_action(
			agent=agent,
			status=status,
			run=self.runs[agent],
			edited_before_approval=edited,
		)

		created = add_to_date(now_datetime(), days=-age_days, hours=-2)
		values = {"creation": created}
		if latency is not None:
			values["decided_at"] = add_to_date(created, seconds=latency)
			values["decided_by"] = APPROVER_USER
		frappe.db.set_value("Agent Action", action.name, values, update_modified=False)
		return action.name

	def row_for(self, rows: list[dict], agent: str) -> dict:
		for row in rows:
			if row["agent"] == agent:
				return row
		self.fail(f"{agent} not in {[row['agent'] for row in rows]}")

	def test_the_arithmetic_for_one_agent(self):
		_, rows = execute(frappe._dict({"agent": DRAFT_AGENT}))

		self.assertEqual(len(rows), 1)
		row = rows[0]
		self.assertEqual(row["agent"], DRAFT_AGENT)
		self.assertEqual(row["proposed"], 6)
		# An approval that later failed to apply was still an approval.
		self.assertEqual(row["approved"], 3)
		self.assertEqual(row["rejected"], 1)
		self.assertEqual(row["expired"], 1)
		self.assertEqual(row["edited_before_approval_rate"], 33.33)
		# Median over the four decided actions: 60, 120, 180, 300.
		self.assertEqual(row["median_decision_latency"], 150)

	def test_an_agent_nobody_decided_reports_zeroes_rather_than_dividing_by_none(self):
		_, rows = execute(frappe._dict({"agent": AGENT}))

		row = rows[0]
		self.assertEqual(row["proposed"], 1)
		self.assertEqual(row["approved"], 0)
		self.assertEqual(row["edited_before_approval_rate"], 0.0)
		self.assertEqual(row["median_decision_latency"], 0)

	def test_one_row_per_agent_busiest_first(self):
		_, rows = execute(frappe._dict({}))

		agents = [row["agent"] for row in rows]
		self.assertEqual(len(agents), len(set(agents)))
		self.assertIn(DRAFT_AGENT, agents)
		self.assertIn(OWNED_AGENT, agents)
		self.assertLess(agents.index(DRAFT_AGENT), agents.index(OWNED_AGENT))

		second = self.row_for(rows, OWNED_AGENT)
		self.assertEqual(second["proposed"], 2)
		self.assertEqual(second["approved"], 1)
		self.assertEqual(second["edited_before_approval_rate"], 0.0)
		self.assertEqual(second["median_decision_latency"], 60)

	def test_the_columns_name_what_the_rows_carry(self):
		columns, rows = execute(frappe._dict({"agent": DRAFT_AGENT}))

		fieldnames = {column["fieldname"] for column in columns}
		self.assertEqual(fieldnames, set(rows[0]))
		self.assertIn("edited_before_approval_rate", fieldnames)

	def test_the_date_filters_bound_the_window(self):
		self.seed(DRAFT_AGENT, "Applied", latency=45, age_days=10)

		_, all_time = execute(frappe._dict({"agent": DRAFT_AGENT}))
		self.assertEqual(all_time[0]["proposed"], 7)

		_, recent = execute(
			frappe._dict({"agent": DRAFT_AGENT, "from_date": add_days(today(), -1), "to_date": today()})
		)
		self.assertEqual(recent[0]["proposed"], 6)

	def test_a_user_without_the_reading_roles_cannot_run_it(self):
		"""Two independent gates, and this user passes neither."""
		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.PermissionError):
				run_query_report(REPORT)

	def test_an_auditor_can_run_it(self):
		with as_user(AUDITOR_USER):
			result = run_query_report(REPORT, filters={"agent": DRAFT_AGENT})

		self.assertTrue(result.get("columns"))
		self.assertEqual([row["agent"] for row in result["result"]], [DRAFT_AGENT])
