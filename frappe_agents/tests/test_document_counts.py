# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""How many and how much: a page of rows is not a total.

`search_documents` hands back a page and says whether there is another one.
`summarize_documents` asks the database for the number, as the user, and holds
every field it touches to the same permission rules a read is held to — and a
little more, because an aggregate reads every row it covers.

Every query here is scoped to orders this test created, by title prefix. Other
tests' rows ride implicit DDL commits and survive rollback, so an unscoped count
would depend on which tests ran first.
"""

import frappe

from frappe_agents.access.grants import exposed_tool_names
from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	IBAN_FIELD,
	OPEN_USER,
	ORDER_DT,
	PASSWORD_FIELD,
	PROJECT_ALPHA,
	PROJECT_BETA,
	RESTRICTED_USER,
	SECOND_DRAFTER,
	TICKET_DT,
	VENDOR_DT,
	AgentTestCase,
	as_user,
	call_tool,
	make_matrix_agent,
	make_order_draft,
	rule,
)
from frappe_agents.tools.base import ToolDenied, masking_supported
from frappe_agents.tools.read_tools import search_documents
from frappe_agents.tools.summary_tools import MIN_GROUP_SIZE, summarize_documents


class CountTestCase(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.prefix = f"FA Count {frappe.generate_hash(length=8)}"
		self.scope = {"order_title": ["like", f"{self.prefix}%"]}

	def order(self, user: str, amount: int, project: str = PROJECT_ALPHA) -> str:
		title = f"{self.prefix} {frappe.generate_hash(length=6)}"
		if project == PROJECT_ALPHA:
			return make_order_draft(user=user, title=title, amount=amount).name
		# The drafters may not be pinned to a project, but the fixture only gives
		# them Alpha. A Beta order is the site's, not theirs.
		order = frappe.get_doc(
			{"doctype": ORDER_DT, "order_title": title, "project": project, "amount": amount}
		)
		order.insert(ignore_permissions=True)
		return order.name

	def summarize(self, user: str, **args) -> dict:
		filters = dict(self.scope, **(args.pop("filters", None) or {}))
		with as_user(user):
			return summarize_documents({"doctype": ORDER_DT, "filters": filters, **args})

	def by(self, result: dict, field: str) -> dict:
		return {row[field]: row for row in result["rows"]}


class TestSearchPaging(CountTestCase):
	def test_two_pages_cover_every_row_once(self):
		created = {self.order(DRAFT_USER, amount) for amount in (10, 20, 30)}
		args = {
			"doctype": ORDER_DT,
			"fields": ["name"],
			"filters": self.scope,
			"order_by": "order_title asc",
			"limit": 2,
		}

		with as_user(OPEN_USER):
			first = search_documents(args)
			second = search_documents(dict(args, start=first["next_start"]))

		self.assertEqual(first["returned"], 2)
		self.assertEqual(first["start"], 0)
		self.assertTrue(first["has_more"])
		self.assertEqual(first["next_start"], 2)

		self.assertEqual(second["returned"], 1)
		self.assertEqual(second["start"], 2)
		self.assertFalse(second["has_more"])
		self.assertIsNone(second["next_start"])

		first_names = {row["name"] for row in first["rows"]}
		second_names = {row["name"] for row in second["rows"]}
		self.assertFalse(first_names & second_names)
		self.assertEqual(first_names | second_names, created)

	def test_a_page_no_longer_calls_itself_a_count(self):
		"""'count' read as a total and was the page size. It is gone."""
		self.order(DRAFT_USER, 10)

		with as_user(OPEN_USER):
			result = search_documents({"doctype": ORDER_DT, "filters": self.scope})

		self.assertNotIn("count", result)
		self.assertEqual(result["returned"], 1)
		self.assertFalse(result["has_more"])


class TestCounts(CountTestCase):
	def setUp(self) -> None:
		super().setUp()
		# Four, three and one: two groups big enough to show values, one too small.
		for amount in (10, 20, 30, 40):
			self.order(DRAFT_USER, amount)
		for amount in (100, 200, 300):
			self.order("Administrator", amount, project=PROJECT_BETA)
		self.order(SECOND_DRAFTER, 1000)

	def test_counts_by_owner_across_readable_rows(self):
		result = self.summarize(OPEN_USER, group_by=["owner"])

		counts = {owner: row["count"] for owner, row in self.by(result, "owner").items()}
		self.assertEqual(counts, {DRAFT_USER: 4, "Administrator": 3, SECOND_DRAFTER: 1})
		self.assertEqual(result["total"]["count"], 8)
		self.assertEqual(result["rows"][0]["owner"], DRAFT_USER, "largest group first")
		self.assertFalse(result["truncated"])

	def test_user_permissions_restrict_the_counts(self):
		"""The Beta orders are not in the restricted user's world, so not in their numbers."""
		result = self.summarize(RESTRICTED_USER, group_by=["owner"])

		counts = {owner: row["count"] for owner, row in self.by(result, "owner").items()}
		self.assertEqual(counts, {DRAFT_USER: 4, SECOND_DRAFTER: 1})
		self.assertEqual(result["total"]["count"], 5)

	def test_a_plain_count_needs_no_group(self):
		result = self.summarize(RESTRICTED_USER)

		self.assertEqual(result["returned"], 1)
		self.assertEqual(result["rows"][0]["count"], 5)
		self.assertEqual(result["total"]["count"], 5)

	def test_sum_on_a_number_field(self):
		result = self.summarize(OPEN_USER, group_by=["owner"], aggregates=[{"fn": "sum", "field": "amount"}])

		rows = self.by(result, "owner")
		self.assertEqual(rows[DRAFT_USER]["sum_amount"], 100)
		self.assertEqual(rows["Administrator"]["sum_amount"], 600)
		self.assertEqual(rows[DRAFT_USER]["count"], 4)
		self.assertEqual(result["total"]["sum_amount"], 1700)
		self.assertEqual(result["columns"], ["owner", "sum_amount", "count"])
		self.assertEqual(result["rows"][0]["owner"], "Administrator", "ordered by the first aggregate")

	def test_small_groups_keep_their_count_and_lose_their_values(self):
		result = self.summarize(
			OPEN_USER,
			group_by=["owner"],
			aggregates=[{"fn": "count"}, {"fn": "sum", "field": "amount"}, {"fn": "max", "field": "amount"}],
		)

		rows = self.by(result, "owner")
		self.assertLess(rows[SECOND_DRAFTER]["count"], MIN_GROUP_SIZE)
		self.assertEqual(rows[SECOND_DRAFTER]["count"], 1)
		self.assertIsNone(rows[SECOND_DRAFTER]["sum_amount"])
		self.assertIsNone(rows[SECOND_DRAFTER]["max_amount"])
		self.assertTrue(rows[SECOND_DRAFTER]["suppressed"])

		self.assertEqual(rows[DRAFT_USER]["max_amount"], 40)
		self.assertFalse(rows[DRAFT_USER]["suppressed"])
		self.assertEqual(result["suppressed_groups"], 1)

	def test_a_small_group_does_not_keep_its_rank(self):
		"""The one 1000 order would sort first by sum. Its place would give it away."""
		result = self.summarize(OPEN_USER, group_by=["owner"], aggregates=[{"fn": "sum", "field": "amount"}])

		self.assertEqual(result["rows"][-1]["owner"], SECOND_DRAFTER)
		self.assertTrue(result["rows"][-1]["suppressed"])

	def test_groups_are_capped_and_say_so(self):
		result = self.summarize(OPEN_USER, group_by=["owner"], limit=1)

		self.assertEqual(result["returned"], 1)
		self.assertTrue(result["truncated"])
		self.assertEqual(result["total"]["count"], 8, "the total still covers every group")

	def test_cancelled_documents_are_left_out_by_default(self):
		cancelled = frappe.get_doc(
			{"doctype": ORDER_DT, "order_title": f"{self.prefix} cancelled", "project": PROJECT_ALPHA}
		)
		cancelled.insert(ignore_permissions=True)
		cancelled.submit()
		cancelled.cancel()

		default = self.summarize(OPEN_USER, group_by=["docstatus"])
		included = self.summarize(OPEN_USER, group_by=["docstatus"], include_cancelled=True)

		self.assertNotIn("CANCELLED", self.by(default, "status_word"))
		self.assertEqual(self.by(included, "status_word")["CANCELLED"]["count"], 1)
		self.assertEqual(included["columns"], ["status_word", "count"])


class TestRefusals(CountTestCase):
	def assertRefused(self, user: str, error: type = ToolDenied, **args) -> str:
		with self.assertRaises(error) as caught:
			self.summarize(user, **args)
		return str(caught.exception)

	def test_sum_on_text_is_refused(self):
		message = self.assertRefused(
			OPEN_USER, ValueError, aggregates=[{"fn": "sum", "field": "order_title"}]
		)
		self.assertIn("number field", message)

	def test_free_text_is_not_a_group(self):
		self.assertRefused(OPEN_USER, ValueError, group_by=["notes"])

	def test_group_by_above_the_permlevel_is_refused(self):
		self.assertRefused(RESTRICTED_USER, group_by=["internal_rate"])
		# The same field, for a user who may read it, is a group like any other.
		self.summarize(OPEN_USER, group_by=["internal_rate"])

	def test_filter_above_the_permlevel_is_refused(self):
		self.assertRefused(RESTRICTED_USER, filters={"internal_rate": "x"})

	def test_a_sensitive_field_is_not_a_group(self):
		"""Marked sensitive in Agent Settings: readable, but not something to tally."""
		self.assertRefused(OPEN_USER, group_by=[IBAN_FIELD])

	def test_a_password_field_is_not_a_group(self):
		self.assertRefused(OPEN_USER, group_by=[PASSWORD_FIELD])

	def test_a_filter_on_a_sensitive_field_is_refused(self):
		"""Counting under a filter asks 'how many have this value' — one guess at a time."""
		self.assertRefused(OPEN_USER, filters={IBAN_FIELD: ["like", "AE07%"]})

	def test_a_masked_field_is_not_a_group(self):
		if not masking_supported():
			self.skipTest("this Frappe version does not mask fields")
		with as_user(DRAFT_USER), self.assertRaises(ToolDenied):
			summarize_documents({"doctype": VENDOR_DT, "group_by": [IBAN_FIELD]})

	def test_a_doctype_with_a_row_permission_hook_is_refused(self):
		"""get_list never runs a has_permission hook, so its count could include unreadable rows."""
		with self.assertRaises(ToolDenied) as caught:
			summarize_documents({"doctype": "ToDo", "group_by": ["allocated_to"]})
		self.assertIn("search_documents", str(caught.exception))

	def test_too_many_groups_are_refused(self):
		self.assertRefused(OPEN_USER, ValueError, group_by=["owner", "project", "docstatus"])


class TestGrant(CountTestCase):
	def test_no_read_rule_no_tool(self):
		agent = make_matrix_agent([], autonomy="Suggest")
		self.assertNotIn("summarize_documents", exposed_tool_names(agent))

		payload, _ = call_tool(OPEN_USER, "summarize_documents", {"doctype": ORDER_DT}, agent=agent.name)
		self.assertFalse(payload["ok"])

	def test_a_rule_on_another_doctype_does_not_reach_this_one(self):
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")
		self.assertIn("summarize_documents", exposed_tool_names(agent))

		payload, _ = call_tool(OPEN_USER, "summarize_documents", {"doctype": ORDER_DT}, agent=agent.name)
		self.assertFalse(payload["ok"])
		self.assertIn("no access rule", payload["error"])

	def test_a_read_rule_allows_it(self):
		self.order(DRAFT_USER, 10)
		agent = make_matrix_agent([rule(ORDER_DT, can_read=1)], autonomy="Suggest")

		payload, _ = call_tool(
			OPEN_USER,
			"summarize_documents",
			{"doctype": ORDER_DT, "filters": self.scope, "group_by": ["owner"]},
			agent=agent.name,
		)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(payload["result"]["total"]["count"], 1)
