# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The run's event log holds back what the audit row holds back.

A tool call is stored twice: on its Agent Tool Call row, and in the run's own
event log. An auditor reads both, so a Password or a field Agent Settings marks
sensitive must be `[redacted]` in both. The live frame is the exception: it goes
only to the user who ran the turn, who already holds the value.
"""

import datetime
import json
from unittest.mock import patch

import frappe

from frappe_agents import redaction
from frappe_agents.api import get_conversation
from frappe_agents.harness.messages import message_text
from frappe_agents.patches.v0_7_0 import redact_stored_event_logs
from frappe_agents.redaction import REDACTED, EventRedactor, redact, redact_json_text, scrub
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
# A number marked sensitive for one test. Numbers are secrets too: a salary is.
AMOUNT = 98765
DEAD = ProviderError("The provider is unreachable.")


def stored_log(run_name: str) -> str:
	return frappe.db.get_value("Agent Run", run_name, "event_log") or ""


def amount_is_sensitive():
	"""Mark the order's amount sensitive, on top of whatever the site marks."""
	original = redaction.doctype_sensitive_keys
	return patch(
		"frappe_agents.redaction.doctype_sensitive_keys",
		side_effect=lambda doctype: original(doctype) | {"amount"},
	)


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

	def order_with_iban(self, amount: int = 10) -> str:
		order = frappe.get_doc(
			{
				"doctype": ORDER_DT,
				"order_title": f"FA Redact Log {frappe.generate_hash(length=8)}",
				"project": PROJECT_ALPHA,
				"amount": amount,
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

	def test_a_result_cut_at_the_model_limit_keeps_no_secret_of_any_type(self):
		"""The cut lands mid-JSON, so the stored copy is redacted before the cut."""
		order = self.order_with_iban(amount=AMOUNT)

		with amount_is_sensitive(), patch("frappe_agents.runner.run.TOOL_RESULT_LIMIT", 400):
			run, frames = self.turn(self.read_script(order, model_says("Read it.")))

		sent = live(frames, "tool_execution_end")[0]["result"]["content"][0]["text"]
		self.assertIn("result truncated", sent)
		self.assertIn(str(AMOUNT), sent)

		events = run_events(run)
		ended = next(event for event in events if event["type"] == "tool_execution_end")
		text = ended["result"]["content"][0]["text"]
		self.assertIn("result truncated", text)
		self.assertIn(REDACTED, text)
		log = stored_log(run)
		self.assertNotIn(str(AMOUNT), log)
		self.assertNotIn(IBAN, log)

	def test_an_answer_that_repeats_a_secret_is_stored_without_it(self):
		order = self.order_with_iban()
		answer = f"The order pays into {IBAN}."

		run, frames = self.turn(self.read_script(order, model_says(answer)))

		# Live, the person who ran the turn reads what the model wrote.
		said = [frame for frame in frames if frame.get("type") == "message"]
		self.assertEqual(said[-1]["message"], answer)

		stored = frappe.db.get_value("Agent Run", run, ["status", "output_message"], as_dict=True)
		self.assertEqual(stored.status, "Completed")
		self.assertEqual(stored.output_message, f"The order pays into {REDACTED}.")
		self.assertNotIn(IBAN, stored_log(run))

		with as_user(DRAFT_USER):
			reopened = next(
				item for item in get_conversation(self.conversation)["runs"] if item["name"] == run
			)
		self.assertNotIn(IBAN, frappe.as_json(reopened))
		# The chat draws the answer once: the column matches the log's last answer.
		last = [
			event["message"]
			for event in reopened["event_log"]
			if event.get("type") == "message_end" and event["message"].get("role") == "assistant"
		][-1]
		text = "".join(block.get("text") or "" for block in last["content"] if block.get("type") == "text")
		self.assertEqual(text, reopened["output_message"])

	def test_an_answer_that_repeats_a_currency_value_is_stored_without_it(self):
		order = self.order_with_iban(amount=AMOUNT)
		answer = (
			"Salary is 98765. Written 98,765.00, 98765.00, AED98765, 98765AED, 98765/month, "
			"amount:98765 and 98765-99000. Dated 2025-01-02, ref ORD-2025-0001, not 1987650."
		)

		with amount_is_sensitive():
			run, _ = self.turn(self.read_script(order, model_says(answer)))

		stored = frappe.db.get_value("Agent Run", run, "output_message")
		# Word-bounded: the answer also holds 1987650 on purpose, which must stay.
		self.assertNotRegex(stored, r"(?<!\d)98765(?!\d)")
		self.assertNotIn("98,765", stored)
		for kept in ("2025-01-02", "ORD-2025-0001", "1987650", "-99000"):
			self.assertIn(kept, stored)
		last = [
			event["message"]
			for event in run_events(run)
			if event["type"] == "message_end" and event["message"].get("role") == "assistant"
		][-1]
		self.assertEqual(last["content"][0]["text"], stored)

	def test_an_answer_after_a_withheld_result_is_withheld_too(self):
		"""A result that could not be redacted taught the log nothing. Fail closed."""
		order = self.order_with_iban()

		with patch("frappe_agents.redaction.EventRedactor.record_result", side_effect=RuntimeError("broken")):
			run, frames = self.turn(self.read_script(order, model_says(f"It pays into {IBAN}.")))

		self.assertEqual(frappe.db.get_value("Agent Run", run, "output_message"), REDACTED)
		self.assertNotIn(IBAN, stored_log(run))
		# Live, the person who ran the turn still reads the answer.
		said = [frame for frame in frames if frame.get("type") == "message"]
		self.assertEqual(said[-1]["message"], f"It pays into {IBAN}.")

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


class TestVersionChanges(AgentTestCase):
	"""A version names the field as a value, so the walk reads the name."""

	def versions(self) -> dict:
		# The shape `get_document_slice` returns for slice="versions".
		return {
			"versions": [
				{
					"changes": [
						{"field": IBAN_FIELD, "from": "AE0100000000", "to": IBAN},
						{"field": f"items[0].{IBAN_FIELD}", "from": None, "to": "AE0200000000"},
						{"field": "order_title", "from": "Old title", "to": "New title"},
					],
				}
			]
		}

	def test_a_sensitive_field_change_holds_back_from_and_to(self):
		clean, secrets = redact(self.versions(), {IBAN_FIELD})

		changes = clean["versions"][0]["changes"]
		self.assertEqual(changes[0], {"field": IBAN_FIELD, "from": REDACTED, "to": REDACTED})
		self.assertEqual(changes[1]["to"], REDACTED)
		self.assertEqual(changes[2]["to"], "New title")
		self.assertEqual(secrets, {"AE0100000000", IBAN, "AE0200000000"})

	def test_a_stored_versions_result_keeps_no_changed_secret(self):
		args = {"doctype": ORDER_DT, "name": "FA-1", "slice": "versions"}
		text = frappe.as_json({"ok": True, "result": self.versions(), "error": None})
		redactor = EventRedactor()
		redactor.event({"type": "tool_execution_start", "toolCallId": "call_1", "args": args})

		stored = redactor.event(
			{
				"type": "tool_execution_end",
				"toolCallId": "call_1",
				"result": {"content": [{"type": "text", "text": text}]},
			}
		)

		stored_text = stored["result"]["content"][0]["text"]
		self.assertNotIn(IBAN, stored_text)
		self.assertNotIn("AE0100000000", stored_text)
		self.assertIn("New title", stored_text)


NUMBER_ARGS = {"doctype": ORDER_DT, "name": "ORD-2025-0001", "slice": "fields"}
NUMBER_RESULT = {
	"ok": True,
	"result": {
		"amount": 2025,
		"date": "2025-01-02",
		"name": "ORD-2025-0001",
		"total": 12025,
		"note": "Paid 2025.",
	},
	"error": None,
}
NUMBER_KEPT = {"date": "2025-01-02", "name": "ORD-2025-0001", "total": 12025}


class TestNumbersStayInTheirPlace(AgentTestCase):
	"""A sensitive number is held back where it stands, not inside other values.

	A sensitive amount of 2025 must not turn a date into `[redacted]-01-02`, a
	name into `ORD-[redacted]-0001`, or 12025 into `1[redacted]` — and the stored
	result must stay JSON, because the chat parses it to redraw a proposal card.
	"""

	def stored(self, recorded: bool) -> tuple[dict, str]:
		text = frappe.as_json(NUMBER_RESULT)
		with amount_is_sensitive():
			redactor = EventRedactor()
			redactor.event({"type": "tool_execution_start", "toolCallId": "call_1", "args": NUMBER_ARGS})
			if recorded:
				redactor.record_result("call_1", NUMBER_ARGS, NUMBER_RESULT, text, frappe.as_json)
			ended = redactor.event(
				{
					"type": "tool_execution_end",
					"toolCallId": "call_1",
					"result": {"content": [{"type": "text", "text": text}]},
				}
			)
			said = redactor.event(
				{
					"type": "message_end",
					"message": {
						"role": "assistant",
						"content": [
							{"type": "text", "text": "Paid 2025 on 2025-01-02 for ORD-2025-0001, 12025."}
						],
					},
				}
			)
		return json.loads(ended["result"]["content"][0]["text"]), said["message"]["content"][0]["text"]

	def test_a_recorded_result_stays_json_with_its_neighbours_intact(self):
		result, said = self.stored(recorded=True)

		self.assertEqual(result["result"]["amount"], REDACTED)
		self.assertEqual(result["result"]["note"], f"Paid {REDACTED}.")
		for key, value in NUMBER_KEPT.items():
			self.assertEqual(result["result"][key], value)
		self.assertEqual(said, f"Paid {REDACTED} on 2025-01-02 for ORD-2025-0001, 12025.")

	def test_a_result_read_back_from_text_stays_json_too(self):
		result, _ = self.stored(recorded=False)

		self.assertEqual(result["result"]["amount"], REDACTED)
		for key, value in NUMBER_KEPT.items():
			self.assertEqual(result["result"][key], value)

	def test_prose_keeps_a_number_inside_a_date_a_reference_or_a_longer_number(self):
		text = "2025 on 2025-01-02, 02/01/2025, ORD-2025-0001, 12025, 2025.5 and 15000; then 2025."

		self.assertEqual(
			scrub(text, {"2025", "1500"}),
			f"{REDACTED} on 2025-01-02, 02/01/2025, ORD-2025-0001, 12025, 2025.5 and 15000; then {REDACTED}.",
		)

	def test_a_unit_or_a_label_never_protects_a_number(self):
		_, secrets = redact({"amount": AMOUNT}, {"amount"})

		for written in (
			"98765-99000/year",
			"AED-98765/month",
			"98765/month/employee",
			"salary/98765/month",
			"net-pay-98765",
			"98765-99000-1",
		):
			with self.subTest(written=written):
				self.assertNotIn("98765", scrub(written, secrets))

	def test_a_date_or_a_reference_keeps_its_number(self):
		_, secrets = redact({"amount": 2025}, {"amount"})

		for written in ("2025-01-02", "02/01/2025", "ORD-2025-0001", "HR-EMP-2025-0001"):
			with self.subTest(written=written):
				self.assertEqual(scrub(written, secrets), written)

	def test_a_date_read_live_is_learned_like_a_date_read_from_text(self):
		"""A tool hands a Date over as an object, before it is serialised."""
		args = {"doctype": ORDER_DT, "slice": "fields"}
		born = datetime.date(1990, 5, 17)
		result = {"ok": True, "result": {"fields": {"name": "FA-1", "date_of_birth": born}}, "error": None}
		original = redaction.doctype_sensitive_keys
		with patch(
			"frappe_agents.redaction.doctype_sensitive_keys",
			side_effect=lambda doctype: original(doctype) | {"date_of_birth"},
		):
			redactor = EventRedactor()
			redactor.event({"type": "tool_execution_start", "toolCallId": "call_1", "args": args})
			text = frappe.as_json(result)
			redactor.record_result("call_1", args, result, text, frappe.as_json)
			ended = redactor.event(
				{
					"type": "tool_execution_end",
					"toolCallId": "call_1",
					"result": {"content": [{"type": "text", "text": text}]},
				}
			)
			said = redactor.event(
				{
					"type": "message_end",
					"message": {
						"role": "assistant",
						"content": [{"type": "text", "text": "Born 1990-05-17."}],
					},
				}
			)

		stored = json.loads(ended["result"]["content"][0]["text"])
		self.assertEqual(stored["result"]["fields"]["date_of_birth"], REDACTED)
		self.assertEqual(said["message"]["content"][0]["text"], f"Born {REDACTED}.")
		self.assertEqual(redactor.scrub("Born 1990-05-17."), f"Born {REDACTED}.")

	def test_prose_loses_a_currency_value_in_every_form_it_is_written(self):
		# A Currency field reaches the redactor as a float, so the value held back
		# is 98765.0 and the prose writes it however it likes.
		_, secrets = redact({"amount": float(AMOUNT)}, {"amount"})
		self.assertEqual(secrets, {"98765.0"})

		for written in (
			"Salary is 98765.",
			"98,765.00",
			"98765.00",
			"AED98765",
			"98765AED",
			"98765/month",
			"amount:98765",
			'{"amount":98765}',
			"98765-99000",
		):
			with self.subTest(written=written):
				scrubbed = scrub(written, secrets)
				self.assertNotIn("98765", scrubbed)
				self.assertNotIn("98,765", scrubbed)
				self.assertIn(REDACTED, scrubbed)

	def test_update_events_hold_back_their_arguments_too(self):
		stored = EventRedactor().event(
			{
				"type": "tool_execution_update",
				"toolCallId": "call_1",
				"args": {"doctype": ORDER_DT, PASSWORD_FIELD: PASSWORD},
				"partialResult": {"content": []},
			}
		)

		self.assertEqual(stored["args"][PASSWORD_FIELD], REDACTED)


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

	def test_a_cut_text_holds_back_numbers_objects_and_a_value_the_cut_is_inside(self):
		keys = {"amount", "bank", IBAN_FIELD}
		whole = frappe.as_json(
			{"rows": [{"amount": AMOUNT, "bank": {"code": "BANKCODE1"}, IBAN_FIELD: IBAN}]}
		)
		cut = whole[: whole.index(IBAN) + 6]

		clean, secrets = redact_json_text(cut, keys)

		self.assertNotIn(str(AMOUNT), clean)
		self.assertNotIn("BANKCODE1", clean)
		self.assertNotIn(IBAN[:6], clean)
		self.assertIn(str(AMOUNT), secrets)
		self.assertEqual(redact_json_text(clean, keys)[0], clean)

	def test_a_cut_versions_result_holds_back_a_sensitive_change(self):
		whole = frappe.as_json(
			{
				"versions": [
					{
						"changes": [
							{"field": f"items[0].{IBAN_FIELD}", "from": "AE0100000000", "to": IBAN},
							{"field": "order_title", "from": "Old title", "to": "New title"},
						]
					}
				]
			}
		)
		cut = whole[: whole.index("New title")]

		clean, secrets = redact_json_text(cut, {IBAN_FIELD})

		self.assertNotIn(IBAN, clean)
		self.assertNotIn("AE0100000000", clean)
		self.assertIn("Old title", clean)
		self.assertEqual(secrets, {"AE0100000000", IBAN})

	def test_a_key_inside_a_string_is_not_a_key(self):
		text = '{"notes": "the \\"iban\\": 1234 is fine", "rows": [' + "1" * 5

		clean, _ = redact_json_text(text, {IBAN_FIELD})

		self.assertEqual(clean, text)


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

	def test_an_old_cut_result_with_no_start_event_is_redacted(self):
		"""A long log drops its middle, start events and all. The result names its doctype."""
		whole = frappe.as_json(
			{
				"ok": True,
				"result": {
					"doctype": ORDER_DT,
					"fields": {"amount": AMOUNT, IBAN_FIELD: IBAN, "notes": "x" * 400},
				},
				"error": None,
			}
		)
		cut = whole[:200] + "\n... (result truncated)"
		self.assertIn(IBAN, cut)
		events = [
			{
				"type": "tool_execution_end",
				"seq": 30,
				"toolCallId": "call_9",
				"toolName": "get_document_slice",
				"result": {"content": [{"type": "text", "text": cut}], "details": None},
				"isError": False,
			},
			{
				"type": "message_end",
				"seq": 33,
				"message": {"role": "assistant", "content": [{"type": "text", "text": f"It is {IBAN}."}]},
			},
		]
		run = make_run(effective_user=DRAFT_USER, agent=DRAFT_AGENT)
		frappe.db.set_value(
			"Agent Run",
			run.name,
			{
				"event_log": frappe.as_json({"events": events, "truncated": True}),
				"output_message": f"It is {IBAN}.",
			},
			update_modified=False,
		)

		with amount_is_sensitive():
			redact_stored_event_logs.execute()

		log = stored_log(run.name)
		self.assertNotIn(str(AMOUNT), log)
		self.assertNotIn(IBAN, log)
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "output_message"), f"It is {REDACTED}.")

	def test_an_event_the_backfill_cannot_redact_loses_its_body(self):
		run = make_run(effective_user=DRAFT_USER, agent=DRAFT_AGENT)
		frappe.db.set_value("Agent Run", run.name, "event_log", self.old_log(), update_modified=False)

		with patch("frappe_agents.redaction.EventRedactor.event", side_effect=RuntimeError("broken")):
			redact_stored_event_logs.execute()

		log = stored_log(run.name)
		self.assertNotIn(PASSWORD, log)
		self.assertNotIn(IBAN, log)
		for event in frappe.parse_json(log)["events"]:
			self.assertLessEqual(set(event), {"type", "seq", "toolCallId", "toolName"})

	def test_an_unreadable_log_is_left_as_it_was(self):
		run = make_run(effective_user=DRAFT_USER, agent=DRAFT_AGENT)
		frappe.db.set_value("Agent Run", run.name, "event_log", "{not json", update_modified=False)

		self.assertFalse(redact_stored_event_logs.redact_row(run.name, "{not json"))
		self.assertEqual(stored_log(run.name), "{not json")
