# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What a conversation can still say about an extraction it asked for.

An extraction is its own record and its own job: it finishes long after the run
that asked for it, and until now nothing tied the two together. So the card in
the chat lived in the browser and nowhere else — switch conversation, or reload,
and the reading a person was waiting on had vanished.

The link is one field, `agent_run`, stamped by `queue_extraction` when a run is
what asked. Everything here is about what that field is allowed to do: it says
which conversation a live update belongs to, and it lets `get_conversation`
report how each run's readings ended. What it must not do is widen anything —
the outcomes come back through `get_list`, so they are the caller's own rows, and
another person's conversation is refused exactly as it was before.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import get_conversation, start_run
from frappe_agents.extraction.pipeline import EVENT, EXTRACTION
from frappe_agents.tests.fixtures import (
	DRAFT_AGENT,
	DRAFT_USER,
	ORDER_DT,
	SECOND_DRAFTER,
	AgentTestCase,
	as_user,
	call_tool,
	extraction_reply,
	make_pdf_attachment,
	queue_extraction_as,
	run_extraction_with,
)

OUTCOME_KEYS = {"name", "status", "target_doctype", "created_doc"}


class TestExtractionReplay(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.file = make_pdf_attachment()

	def extract_in_a_conversation(self, user: str = DRAFT_USER) -> tuple[dict, str]:
		"""One extraction asked for by an agent, inside a conversation of this user's.

		The conversation and the run are opened through `start_run` rather than
		inserted, because both doctypes are readable by their owner alone — a row
		the test session inserted would be invisible to the user for the wrong
		reason.
		"""
		with as_user(user), patch("frappe.enqueue"):
			started = start_run(agent=DRAFT_AGENT, message="What does this invoice say?")

		run = frappe.get_doc("Agent Run", started["run"])
		with patch("frappe.enqueue"):
			payload, _ = call_tool(
				user,
				"extract_document",
				{"file": self.file.name, "target_doctype": ORDER_DT},
				run=run,
			)
		self.assertTrue(payload["ok"], payload["error"])
		return started, payload["result"]["extraction"]

	def read(self, conversation: str, user: str = DRAFT_USER) -> dict:
		with as_user(user):
			return get_conversation(conversation)

	def test_the_run_that_asked_is_stamped_on_the_extraction(self):
		started, extraction = self.extract_in_a_conversation()

		self.assertEqual(frappe.db.get_value(EXTRACTION, extraction, "agent_run"), started["run"])

	def test_an_extraction_a_person_started_is_attributed_to_no_run(self):
		"""The stamp is the run's, and there is no run behind the review surface."""
		extraction = queue_extraction_as(DRAFT_USER, self.file.name)

		self.assertFalse(frappe.db.get_value(EXTRACTION, extraction, "agent_run"))

	def test_every_update_says_which_conversation_the_card_belongs_to(self):
		"""The job runs after the run has ended, so the event has to carry the address."""
		started, extraction = self.extract_in_a_conversation()

		with patch("frappe.publish_realtime") as published:
			run_extraction_with(extraction, extraction_reply({"order_title": "FA Extracted in chat"}))

		payloads = [call.args[1] for call in published.call_args_list if call.args and call.args[0] == EVENT]
		self.assertTrue(payloads)
		for payload in payloads:
			self.assertEqual(payload["run"], started["run"])
			self.assertEqual(payload["conversation"], started["conversation"])

	def test_an_extraction_nobody_asked_for_addresses_no_conversation(self):
		extraction = queue_extraction_as(DRAFT_USER, self.file.name)

		with patch("frappe.publish_realtime") as published:
			run_extraction_with(extraction, extraction_reply({"order_title": "FA Extracted by hand"}))

		payloads = [call.args[1] for call in published.call_args_list if call.args and call.args[0] == EVENT]
		self.assertTrue(payloads)
		for payload in payloads:
			self.assertNotIn("conversation", payload)
			self.assertNotIn("run", payload)

	def test_the_conversation_reports_how_each_reading_ended(self):
		started, extraction = self.extract_in_a_conversation()
		run_extraction_with(extraction, extraction_reply({"order_title": "FA Extracted in chat"}))

		runs = self.read(started["conversation"])["runs"]

		self.assertEqual(len(runs), 1)
		outcomes = runs[0]["extractions"]
		self.assertEqual(len(outcomes), 1)
		self.assertEqual(set(outcomes[0]), OUTCOME_KEYS)
		self.assertEqual(outcomes[0]["name"], extraction)
		self.assertEqual(outcomes[0]["status"], "Needs Review")
		self.assertEqual(outcomes[0]["target_doctype"], ORDER_DT)
		self.assertTrue(outcomes[0]["created_doc"])

	def test_a_reading_still_queued_is_reported_as_it_stands(self):
		"""A card that comes back mid-extraction is the point of the whole lane."""
		started, extraction = self.extract_in_a_conversation()

		outcomes = self.read(started["conversation"])["runs"][0]["extractions"]

		self.assertEqual([row["name"] for row in outcomes], [extraction])
		self.assertEqual(outcomes[0]["status"], "Pending")
		self.assertIsNone(outcomes[0]["created_doc"])

	def test_a_run_that_read_nothing_carries_an_empty_list(self):
		with as_user(DRAFT_USER), patch("frappe.enqueue"):
			started = start_run(agent=DRAFT_AGENT, message="How many orders are there?")

		self.assertEqual(self.read(started["conversation"])["runs"][0]["extractions"], [])

	def test_another_users_conversation_is_refused_rather_than_summarised(self):
		started, _ = self.extract_in_a_conversation()

		with as_user(SECOND_DRAFTER), self.assertRaises(frappe.PermissionError):
			get_conversation(started["conversation"])

	def test_another_users_conversation_yields_none_of_their_extractions(self):
		"""And their own conversation says nothing about somebody else's reading."""
		_, extraction = self.extract_in_a_conversation()

		with as_user(SECOND_DRAFTER), patch("frappe.enqueue"):
			started = start_run(agent=DRAFT_AGENT, message="Anything waiting for me?")
		runs = self.read(started["conversation"], user=SECOND_DRAFTER)["runs"]

		self.assertEqual([run["extractions"] for run in runs], [[]])
		self.assertNotIn(extraction, frappe.as_json(runs))
