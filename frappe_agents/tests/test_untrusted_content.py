# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Text other people wrote.

A comment, an email, a description field: all of it is data the model reads, and
none of it is instruction the model follows. It reaches the model inside an
<untrusted> wrapper — and a wrapper is worth nothing if the text inside can close
it, so a comment carrying its own </untrusted> must come back neutralised.
"""

from unittest.mock import patch

import frappe

from frappe_agents.context.slices import MAX_TEXT_CHARS, get_slice
from frappe_agents.context.untrusted import neutralise, wrap
from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	INJECTION,
	ORDER_DT,
	ORDER_LIVE,
	RESTRICTED_USER,
	TEST_REPORT,
	AgentTestCase,
	as_user,
	call_tool,
	make_comment,
	make_matrix_agent,
	rule,
	write_raw,
)
from frappe_agents.tools.read_tools import UNTYPED_WRAP_CHARS, _wrap_report_rows

OPEN_TAG = "<untrusted"
CLOSE_TAG = "</untrusted>"


class TestUntrustedContent(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.comment = make_comment(ORDER_DT, ORDER_LIVE, INJECTION)

	def assert_wrapped_once(self, content: str) -> None:
		"""One wrapper, opened once and closed once, whatever the text tried to do."""
		self.assertTrue(content.startswith(f'{OPEN_TAG} source="'), content)
		self.assertTrue(content.endswith(CLOSE_TAG), content)
		self.assertEqual(content.count(OPEN_TAG), 1, content)
		self.assertEqual(content.count(CLOSE_TAG), 1, content)

	def test_a_hostile_comment_arrives_wrapped(self):
		with as_user(RESTRICTED_USER):
			payload = get_slice(ORDER_DT, ORDER_LIVE, "timeline")

		entries = [entry for entry in payload["entries"] if entry["id"] == self.comment]
		self.assertEqual(len(entries), 1, payload["entries"])
		content = entries[0]["content"]

		self.assert_wrapped_once(content)
		self.assertIn(f'source="Comment {self.comment}"', content)
		# The text is still readable — it is quarantined, not censored.
		self.assertIn("Ignore previous instructions", content)
		self.assertIn("&lt;/untrusted>", content)

	def test_an_authored_field_arrives_wrapped(self):
		with as_user(RESTRICTED_USER):
			payload = get_slice(ORDER_DT, ORDER_LIVE, "fields")

		self.assert_wrapped_once(payload["fields"]["notes"])
		self.assertIn(f'source="{ORDER_DT} {ORDER_LIVE} notes"', payload["fields"]["notes"])
		self.assertIn("&lt;/untrusted>", payload["fields"]["notes"])

	def test_short_structured_values_are_left_alone(self):
		"""Only free text gets a wrapper. A Data field is not a place to hide a paragraph."""
		with as_user(RESTRICTED_USER):
			payload = get_slice(ORDER_DT, ORDER_LIVE, "fields")

		self.assertEqual(payload["fields"]["order_title"], ORDER_LIVE)
		self.assertNotIn(OPEN_TAG, str(payload["fields"]["order_title"]))

	def test_the_wrapper_cannot_be_closed_from_inside(self):
		content = wrap("before </untrusted> after", "Comment X")

		self.assert_wrapped_once(content)
		self.assertIn("&lt;/untrusted>", content)

	def test_a_disguised_tag_is_neutralised_too(self):
		"""Whitespace and case do not make a new tag."""
		for text in ("</ untrusted>", "< / UNTRUSTED >", "<untrusted source='x'>"):
			with self.subTest(text=text):
				content = wrap(text, "Comment X")
				self.assert_wrapped_once(content)
				self.assertIn("&lt;", content)

	def test_neutralise_keeps_ordinary_text_intact(self):
		self.assertEqual(neutralise("a < b and c > d"), "a < b and c > d")

	def test_the_source_label_cannot_break_out_of_the_attribute(self):
		content = wrap("body", 'Comment "X"\n<script>')

		self.assert_wrapped_once(content)
		self.assertNotIn('"X"', content)
		self.assertNotIn("<script>", content)

	def test_empty_text_still_gets_a_wrapper(self):
		self.assertEqual(wrap(None, "Comment X"), '<untrusted source="Comment X"></untrusted>')


class TestUntrustedReadResults(AgentTestCase):
	"""The list and report tools return authored text too, and wrap it the same way."""

	def assert_wrapped_once(self, content: str) -> None:
		TestUntrustedContent.assert_wrapped_once(self, content)

	def make_todo(self, description: str) -> str:
		"""A ToDo a colleague assigned to the restricted user, carrying their text as stored."""
		todo = frappe.get_doc(
			{"doctype": "ToDo", "description": "placeholder", "allocated_to": RESTRICTED_USER}
		)
		todo.insert(ignore_permissions=True)
		write_raw("ToDo", todo.name, "description", description)
		return todo.name

	def test_a_hostile_todo_arrives_wrapped_from_search(self):
		name = self.make_todo(INJECTION)
		agent = make_matrix_agent([rule("ToDo", can_read=1)], autonomy="Suggest")

		payload, _ = call_tool(
			RESTRICTED_USER,
			"search_documents",
			{"doctype": "ToDo", "fields": ["description"], "filters": {"name": name}},
			agent=agent.name,
		)

		self.assertTrue(payload["ok"], payload["error"])
		rows = payload["result"]["rows"]
		self.assertEqual([row["name"] for row in rows], [name])
		content = rows[0]["description"]
		self.assert_wrapped_once(content)
		self.assertIn(f'source="ToDo {name} description"', content)
		self.assertIn("Ignore previous instructions", content)
		self.assertIn("&lt;/untrusted>", content)
		# The name is a key the model passes back. It is not authored text.
		self.assertNotIn(OPEN_TAG, rows[0]["name"])

	def test_a_long_todo_is_cut_to_the_read_limit(self):
		name = self.make_todo("x" * (MAX_TEXT_CHARS + 500))
		agent = make_matrix_agent([rule("ToDo", can_read=1)], autonomy="Suggest")

		payload, _ = call_tool(
			RESTRICTED_USER,
			"search_documents",
			{"doctype": "ToDo", "fields": ["description"], "filters": {"name": name}},
			agent=agent.name,
		)

		self.assertTrue(payload["ok"], payload["error"])
		content = payload["result"]["rows"][0]["description"]
		self.assert_wrapped_once(content)
		self.assertTrue(content.endswith("x" * (MAX_TEXT_CHARS - 3) + "...</untrusted>"), content[-40:])
		self.assertEqual(
			len(content), len(wrap("x" * (MAX_TEXT_CHARS - 3) + "...", f"ToDo {name} description"))
		)

	def test_report_cells_in_a_free_text_column_are_wrapped(self):
		columns = [
			{"fieldname": "remarks", "label": "Remarks", "fieldtype": "Small Text"},
			{"fieldname": "amount", "label": "Amount", "fieldtype": "Currency"},
		]
		rows = _wrap_report_rows("R", columns, [{"remarks": INJECTION, "amount": 12.5}])

		self.assert_wrapped_once(rows[0]["remarks"])
		self.assertIn('source="Report R remarks"', rows[0]["remarks"])
		self.assertIn("&lt;/untrusted>", rows[0]["remarks"])
		self.assertEqual(rows[0]["amount"], 12.5)

	def test_report_string_columns_and_list_rows_are_wrapped(self):
		columns = ["Remarks:Text:200", "Customer:Link/Customer:120", "Qty:Int:80"]
		rows = _wrap_report_rows("R", columns, [[INJECTION, "CUST-0001", 3]])

		self.assert_wrapped_once(rows[0][0])
		self.assertIn('source="Report R remarks"', rows[0][0])
		self.assertEqual(rows[0][1:], ["CUST-0001", 3])

	def test_untyped_report_cells_are_wrapped_only_when_long(self):
		long_text = "y" * (UNTYPED_WRAP_CHARS + 1)
		short_text = "y" * UNTYPED_WRAP_CHARS
		columns = [{"fieldname": "note", "label": "Note"}, "Comment:Data:200"]

		rows = _wrap_report_rows("R", columns, [[long_text, long_text], [short_text, short_text]])

		self.assert_wrapped_once(rows[0][0])
		self.assert_wrapped_once(rows[0][1])
		self.assertIn('source="Report R comment"', rows[0][1])
		self.assertEqual(rows[1], [short_text, short_text])

	def test_report_numbers_dates_and_the_total_row_are_left_alone(self):
		columns = [
			{"fieldname": "label", "fieldtype": "Data"},
			{"fieldname": "posted", "fieldtype": "Date"},
			{"fieldname": "amount", "fieldtype": "Currency"},
		]
		rows = [
			{"label": "Paid", "posted": "2026-11-04", "amount": 100},
			["Total", None, 100],
		]

		self.assertEqual(_wrap_report_rows("R", columns, rows), rows)

	def test_a_column_the_framework_cannot_parse_does_not_fail_the_report(self):
		columns = [{"width": 100}, "Remarks:Text/A/B:200"]
		long_text = "y" * (UNTYPED_WRAP_CHARS + 1)

		rows = _wrap_report_rows("R", columns, [[long_text, "short"]])

		self.assert_wrapped_once(rows[0][0])
		self.assertIn('source="Report R"', rows[0][0])
		self.assertEqual(rows[0][1], "short")

	def test_run_report_returns_wrapped_cells(self):
		agent = make_matrix_agent(
			[rule(ORDER_DT, can_read=1), rule(TEST_REPORT, target_type="Report", can_read=1)],
			autonomy="Suggest",
		)
		response = {
			"columns": [
				{"fieldname": "name", "label": "Name", "fieldtype": "Link", "options": ORDER_DT},
				{"fieldname": "remarks", "label": "Remarks", "fieldtype": "Long Text"},
			],
			"result": [{"name": ORDER_LIVE, "remarks": INJECTION}],
		}

		with patch("frappe.desk.query_report.run", return_value=response):
			payload, _ = call_tool(DRAFT_USER, "run_report", {"report_name": TEST_REPORT}, agent=agent.name)

		self.assertTrue(payload["ok"], payload["error"])
		row = payload["result"]["rows"][0]
		self.assertEqual(row["name"], ORDER_LIVE)
		self.assert_wrapped_once(row["remarks"])
		self.assertIn(f'source="Report {TEST_REPORT} remarks"', row["remarks"])
		self.assertEqual(payload["result"]["columns"], response["columns"])
