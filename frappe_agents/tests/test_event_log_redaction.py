# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The run's event log holds back what the audit row holds back.

A tool call is stored twice: on its Agent Tool Call row, and in the run's own
event log. An auditor reads both, so a Password or a field Agent Settings marks
sensitive must be `[redacted]` in both. The live frame is the exception: it goes
only to the user who ran the turn, who already holds the value.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import get_conversation
from frappe_agents.harness.messages import message_text
from frappe_agents.patches.v0_7_0 import redact_stored_event_logs
from frappe_agents.redaction import REDACTED, redact_json_text
from frappe_agents.runner.providers import ProviderError
from frappe_agents.runner.run import EVENT
from frappe_agents.tests.fixtures import (
	DRAFT_AGENT,
	DRAFT_USER,
	IBAN_FIELD,
	ORDER_DT,
	PASSWORD_FIELD,
	PROJECT_ALPHA,
	AgentTestCase,
	as_user,
	make_conversation,
	make_run,
	model_calls,
	model_says,
	run_events,
	run_with_model,
	tool_calls_for,
	tool_request,
)

PASSWORD = "portal-pass-never-in-the-log"
IBAN = "AE070331234567890123456789"
DEAD = ProviderError("The provider is unreachable.")


def stored_log(run_name: str) -> str:
	return frappe.db.get_value("Agent Run", run_name, "event_log") or ""


def live(frames: list[dict], kind: str) -> list[dict]:
	return [
		frame["event"]
		for frame in frames
		if frame.get("type") == "harness_event" and frame["event"].get("type") == kind
	]


class TestEventLogRedaction(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.conversation = make_conversation(DRAFT_USER, agent=DRAFT_AGENT).name

	def turn(self, script) -> tuple[str, list[dict]]:
		"""One drafter run in this conversation. Returns the run and its live frames."""
		with as_user(DRAFT_USER):
			run = make_run(
				effective_user=DRAFT_USER,
				agent=DRAFT_AGENT,
				conversation=self.conversation,
				message="Work on the order.",
			)
		with patch("frappe.publish_realtime") as publish:
			run_with_model(run.name, script)
		frames = [call.args[1] for call in publish.call_args_list if call.args[0] == EVENT]
		return run.name, frames

	def order_with_iban(self) -> str:
		order = frappe.get_doc(
			{
				"doctype": ORDER_DT,
				"order_title": f"FA Redact Log {frappe.generate_hash(length=8)}",
				"project": PROJECT_ALPHA,
				"amount": 10,
				IBAN_FIELD: IBAN,
				"items": [{"item": "FA Widget", "qty": 1}],
			}
		)
		with as_user(DRAFT_USER):
			order.insert()
		return order.name

	def read_script(self, order: str, then: object) -> list:
		read = tool_request("get_document_slice", {"doctype": ORDER_DT, "name": order, "slice": "fields"})
		return [model_calls(read, text="Let me read it."), then]

	# --- a value the agent wrote -------------------------------------------

	def test_a_drafted_secret_is_redacted_in_the_log_and_live_to_the_user(self):
		title = f"FA Redact Draft {frappe.generate_hash(length=8)}"
		values = {
			"order_title": title,
			"project": PROJECT_ALPHA,
			"amount": 50,
			PASSWORD_FIELD: PASSWORD,
			IBAN_FIELD: IBAN,
			"items": [{"item": "FA Widget", "qty": 1}],
		}
		draft = tool_request("create_draft", {"doctype": ORDER_DT, "values": values})

		run, frames = self.turn([model_calls(draft), model_says("Drafted.")])

		calls = tool_calls_for(run)
		self.assertEqual([call.outcome for call in calls], ["Success"], calls[0].error if calls else None)

		log = stored_log(run)
		self.assertNotIn(PASSWORD, log)
		self.assertNotIn(IBAN, log)
		self.assertIn(REDACTED, log)
		# What the call did stays legible: the doctype, the field names, the title.
		self.assertIn(ORDER_DT, log)
		self.assertIn(PASSWORD_FIELD, log)
		self.assertIn(IBAN_FIELD, log)
		self.assertIn(title, log)

		events = run_events(run)
		start = next(event for event in events if event["type"] == "tool_execution_start")
		self.assertEqual(start["args"]["values"][PASSWORD_FIELD], REDACTED)
		asked = next(
			block
			for event in events
			if event["type"] == "message_end" and event["message"].get("role") == "assistant"
			for block in event["message"].get("content") or []
			if block.get("type") == "toolCall"
		)
		self.assertEqual(asked["arguments"]["values"][IBAN_FIELD], REDACTED)

		# The person who ran the turn already holds the value, and is told it live.
		self.assertEqual(live(frames, "tool_execution_start")[0]["args"]["values"][PASSWORD_FIELD], PASSWORD)

	# --- a value the agent read --------------------------------------------

	def test_a_read_secret_is_redacted_in_the_stored_result_and_message(self):
		order = self.order_with_iban()

		run, frames = self.turn(self.read_script(order, model_says("Read it.")))

		# The read really carried the value, or there would be nothing to redact.
		self.assertIn(IBAN, live(frames, "tool_execution_end")[0]["result"]["content"][0]["text"])

		events = run_events(run)
		ended = next(event for event in events if event["type"] == "tool_execution_end")
		text = ended["result"]["content"][0]["text"]
		self.assertNotIn(IBAN, text)
		self.assertIn(REDACTED, text)
		self.assertIn(order, text)

		results = [
			event
			for event in events
			if event["type"] == "message_end" and event["message"].get("role") == "toolResult"
		]
		self.assertTrue(results)
		block = results[0]["message"]["content"][0]["text"]
		self.assertNotIn(IBAN, block)
		self.assertIn(REDACTED, block)

		self.assertNotIn(IBAN, stored_log(run))

	def test_a_reopened_conversation_shows_the_marker(self):
		order = self.order_with_iban()
		self.turn(self.read_script(order, model_says("Read it.")))

		with as_user(DRAFT_USER):
			runs = get_conversation(self.conversation)["runs"]

		shown = frappe.as_json([run["event_log"] for run in runs])
		self.assertNotIn(IBAN, shown)
		self.assertIn(REDACTED, shown)

	def test_a_replayed_failed_turn_hands_the_model_the_marker(self):
		"""Replay reads the stored log, so the model is told the call, not the secret."""
		order = self.order_with_iban()
		self.turn(self.read_script(order, DEAD))

		with as_user(DRAFT_USER):
			retry = make_run(
				effective_user=DRAFT_USER,
				agent=DRAFT_AGENT,
				conversation=self.conversation,
				message="Ask that again.",
			)
		provider = run_with_model(retry.name, model_says("Done."))

		prompt = "\n".join(message_text(message) for message in provider.calls[0]["messages"])
		self.assertIn(order, prompt)
		self.assertIn(REDACTED, prompt)
		self.assertNotIn(IBAN, prompt)

	# --- failing closed ----------------------------------------------------

	def test_an_event_that_cannot_be_redacted_is_stored_without_its_body(self):
		order = self.order_with_iban()

		with patch("frappe_agents.redaction.EventRedactor.event", side_effect=RuntimeError("broken")):
			run, frames = self.turn(self.read_script(order, model_says("Read it.")))

		events = run_events(run)
		self.assertTrue(events)
		self.assertNotIn(IBAN, stored_log(run))
		for event in events:
			self.assertLessEqual(set(event), {"type", "seq", "toolCallId", "toolName"})
		# Numbering is untouched: the stored seq is the one the frame went out with.
		self.assertEqual(
			[event["seq"] for event in events],
			[frame["seq"] for frame in frames if frame.get("type") == "harness_event"],
		)


class TestRedactJsonText(AgentTestCase):
	def test_text_with_nothing_to_hold_back_keeps_every_byte(self):
		text = '{"ok": true,   "rows": []}'

		self.assertEqual(redact_json_text(text, {IBAN_FIELD}), (text, set()))

	def test_a_result_cut_short_is_redacted_by_pattern(self):
		cut = frappe.as_json({"result": {IBAN_FIELD: IBAN, "notes": "x" * 50}})[:-20]

		clean, secrets = redact_json_text(cut, {IBAN_FIELD})

		self.assertNotIn(IBAN, clean)
		self.assertIn(REDACTED, clean)
		self.assertEqual(secrets, {IBAN})


class TestRedactStoredEventLogs(AgentTestCase):
	"""The upgrade re-redacts logs written before the log held values back."""

	def old_log(self) -> str:
		args = {
			"doctype": ORDER_DT,
			"values": {"order_title": "FA Old", PASSWORD_FIELD: PASSWORD, IBAN_FIELD: IBAN},
		}
		result = frappe.as_json({"ok": True, "result": {"fields": {IBAN_FIELD: IBAN}}, "error": None})
		events = [
			{"type": "agent_start", "seq": 1},
			{
				"type": "tool_execution_start",
				"seq": 2,
				"toolCallId": "call_1",
				"toolName": "create_draft",
				"args": args,
			},
			{
				"type": "tool_execution_end",
				"seq": 3,
				"toolCallId": "call_1",
				"toolName": "create_draft",
				"result": {"content": [{"type": "text", "text": result}], "details": None},
				"isError": False,
			},
			{
				"type": "message_end",
				"seq": 4,
				"message": {
					"role": "assistant",
					"content": [{"type": "text", "text": f"The IBAN is {IBAN}."}],
				},
			},
		]
		return frappe.as_json({"events": events, "truncated": True})

	def test_an_old_log_is_redacted_once_and_left_alone_after(self):
		run = make_run(effective_user=DRAFT_USER, agent=DRAFT_AGENT)
		frappe.db.set_value("Agent Run", run.name, "event_log", self.old_log(), update_modified=False)
		modified = frappe.db.get_value("Agent Run", run.name, "modified")

		redact_stored_event_logs.execute()

		log = stored_log(run.name)
		self.assertNotIn(PASSWORD, log)
		self.assertNotIn(IBAN, log)
		self.assertIn(REDACTED, log)
		self.assertIn("FA Old", log)
		parsed = frappe.parse_json(log)
		self.assertTrue(parsed["truncated"])
		self.assertEqual([event["seq"] for event in parsed["events"]], [1, 2, 3, 4])
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "modified"), modified)

		redact_stored_event_logs.execute()

		self.assertEqual(stored_log(run.name), log)

	def test_an_unreadable_log_is_left_as_it_was(self):
		run = make_run(effective_user=DRAFT_USER, agent=DRAFT_AGENT)
		frappe.db.set_value("Agent Run", run.name, "event_log", "{not json", update_modified=False)

		self.assertFalse(redact_stored_event_logs.redact_row(run.name, "{not json"))
		self.assertEqual(stored_log(run.name), "{not json")
