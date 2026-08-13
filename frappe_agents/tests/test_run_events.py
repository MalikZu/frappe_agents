# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What a run tells the browser, and what it writes down.

Two audiences read a run's events. The chat surface reads them live: the three
legacy events it has always read, and the loop's own events beside them. A
reload reads them from the run row, because a proposal card published while the
run was going has nothing else to come back from.

The log is capped in both directions — how many events and how many bytes —
because a run that called a tool a hundred times must not put megabytes on one
row.
"""

from unittest.mock import patch

import frappe

from frappe_agents.api import get_conversation, start_run
from frappe_agents.runner.providers import ProviderError
from frappe_agents.runner.run import (
	EVENT,
	EVENT_LOG_BYTES,
	EVENT_LOG_HEAD,
	EVENT_LOG_TAIL,
	_event_log,
)
from frappe_agents.tests.fixtures import (
	AGENT,
	DRAFT_AGENT,
	DRAFT_USER,
	ORDER_DT,
	RESTRICTED_USER,
	TICKET_DT,
	AgentTestCase,
	as_user,
	event_types,
	make_order_draft,
	make_run,
	model_calls,
	model_says,
	run_events,
	run_with_model,
	tool_request,
)

SEARCH = model_calls(tool_request("search_documents", {"doctype": TICKET_DT, "fields": ["name"]}))
ANSWER = model_says("One ticket is open.", tokens_in=10, tokens_out=5)
REASON = "The quantities match the signed quotation."


class TestRunEvents(AgentTestCase):
	def run_once(self, script=None, **values) -> str:
		run = make_run(effective_user=RESTRICTED_USER, **values)
		run_with_model(run.name, script if script is not None else [SEARCH, ANSWER])
		return run.name

	def published(self, run_name: str, script=None) -> list[dict]:
		"""Everything the run pushed to the browser, in order."""
		with patch("frappe.publish_realtime") as publish:
			run_with_model(run_name, script if script is not None else [SEARCH, ANSWER])
		return [call.args[1] for call in publish.call_args_list if call.args[0] == EVENT]

	# --- what the browser is told -------------------------------------------

	def test_the_legacy_events_still_fire(self):
		"""The chat surface reads these three. Nothing in the port may drop them."""
		run = make_run(effective_user=RESTRICTED_USER)

		events = self.published(run.name)

		legacy = [event for event in events if event["type"] != "harness_event"]
		self.assertEqual([event["type"] for event in legacy], ["status", "tool_call", "message"])
		self.assertEqual(legacy[0]["status"], "Running")
		self.assertEqual(legacy[1]["tool"], "search_documents")
		self.assertTrue(legacy[1]["ok"])
		self.assertEqual(legacy[2]["message"], ANSWER["text"])

	def test_a_failure_is_published_as_an_error(self):
		run = make_run(effective_user=RESTRICTED_USER)

		events = self.published(run.name, [ProviderError("The provider is unreachable.")])

		self.assertEqual(events[-1]["type"], "error")
		self.assertEqual(events[-1]["status"], "Failed")

	def test_every_event_goes_only_to_the_user_the_run_acts_for(self):
		run = make_run(effective_user=RESTRICTED_USER)

		with patch("frappe.publish_realtime") as publish:
			run_with_model(run.name, [SEARCH, ANSWER])

		users = {call.kwargs.get("user") for call in publish.call_args_list if call.args[0] == EVENT}
		self.assertEqual(users, {RESTRICTED_USER})

	def test_the_loop_events_are_published_in_order_and_numbered(self):
		run = make_run(effective_user=RESTRICTED_USER)

		events = self.published(run.name)

		harness = [event for event in events if event["type"] == "harness_event"]
		self.assertEqual([event["seq"] for event in harness], list(range(1, len(harness) + 1)))
		self.assertEqual(harness[0]["event"]["type"], "agent_start")
		self.assertEqual(harness[-1]["event"]["type"], "agent_end")
		self.assertEqual(harness[0]["run"], run.name)

	def test_the_last_event_does_not_repeat_the_transcript(self):
		"""`agent_end` is a marker, not a second copy of the conversation.

		The loop signs off by handing back every message it produced. The
		surface was told about each of them as it happened, and the log kept
		them one by one, so neither needs the list again — it is the largest
		payload a run publishes and nothing reads it.
		"""
		run = make_run(effective_user=RESTRICTED_USER)

		events = self.published(run.name)

		published = [
			event["event"]
			for event in events
			if event["type"] == "harness_event" and event["event"]["type"] == "agent_end"
		]
		self.assertEqual(len(published), 1)
		self.assertNotIn("messages", published[0])

		stored = [event for event in run_events(run.name) if event["type"] == "agent_end"]
		self.assertEqual(len(stored), 1)
		self.assertNotIn("messages", stored[0])

	def test_a_tool_is_announced_before_it_runs(self):
		"""What the port is for: the surface hears about a tool while it works.

		The legacy tool_call event only arrives once the tool has finished.
		"""
		run = make_run(effective_user=RESTRICTED_USER)

		events = self.published(run.name)

		types = [
			event["event"]["type"] if event["type"] == "harness_event" else event["type"] for event in events
		]
		self.assertLess(types.index("tool_execution_start"), types.index("tool_call"))

	# --- what the run writes down -------------------------------------------

	def test_the_run_stores_the_events_it_saw(self):
		name = self.run_once()

		types = event_types(run_events(name))
		self.assertEqual(types[0], "agent_start")
		self.assertEqual(types[-1], "agent_end")
		for expected in ("turn_start", "message_end", "tool_execution_start", "tool_execution_end"):
			self.assertIn(expected, types)

	def test_partial_messages_are_not_stored(self):
		"""Nothing streams yet, so a half-written message is neither kept nor sent."""
		name = self.run_once()

		self.assertNotIn("message_update", event_types(run_events(name)))

	def test_a_stored_log_is_json_the_reader_can_parse(self):
		name = self.run_once()

		parsed = frappe.parse_json(frappe.db.get_value("Agent Run", name, "event_log"))
		self.assertIsInstance(parsed, dict)
		self.assertFalse(parsed["truncated"])
		self.assertTrue(all(isinstance(event, dict) for event in parsed["events"]))

	def test_a_run_that_failed_still_stored_its_events(self):
		"""The log is written on the way out, whichever way the run left."""
		name = self.run_once([ProviderError("The provider is unreachable.")])

		self.assertEqual(frappe.db.get_value("Agent Run", name, "status"), "Failed")
		self.assertEqual(event_types(run_events(name)), ["agent_start", "turn_start"])

	def test_a_conversation_replays_the_run_it_stored(self):
		"""The rehydrate path end to end: the log comes back through the API.

		Started and read as the user, because both ends of a replay belong to
		them: the conversation is theirs to open and the run is theirs to see.
		"""
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			started = start_run(agent=AGENT, message="How many tickets are open?")

		name = started["run"]
		run_with_model(name, [SEARCH, ANSWER])

		with as_user(RESTRICTED_USER):
			runs = get_conversation(started["conversation"])["runs"]

		replayed = next(run for run in runs if run["name"] == name)["event_log"]
		self.assertEqual(event_types(replayed), event_types(run_events(name)))
		started = [event for event in replayed if event["type"] == "tool_execution_start"]
		self.assertEqual(started[0]["toolName"], "search_documents")

	def test_a_proposal_can_be_redrawn_from_the_log(self):
		"""The reload the log exists for.

		A proposal card is published while the run is going. After a reload the
		only trace of it is the run's own events, so the tool call that made the
		proposal has to be in there with the arguments the card is drawn from.
		"""
		order = make_order_draft(DRAFT_USER)
		run = make_run(effective_user=DRAFT_USER, agent=DRAFT_AGENT)

		with patch("frappe.publish_realtime") as publish:
			run_with_model(
				run.name,
				[
					model_calls(
						tool_request(
							"propose_submit",
							{"doctype": ORDER_DT, "name": order.name, "reason": REASON},
						)
					),
					model_says("I proposed it. An approver has to decide."),
				],
			)

		live = [call.args[1] for call in publish.call_args_list if call.args[0] == EVENT]
		self.assertIn("action_proposed", [event["type"] for event in live])

		started = [event for event in run_events(run.name) if event["type"] == "tool_execution_start"]
		self.assertEqual(started[0]["toolName"], "propose_submit")
		self.assertEqual(started[0]["args"]["name"], order.name)
		self.assertEqual(started[0]["args"]["reason"], REASON)

	# --- the caps ------------------------------------------------------------

	def test_a_short_log_is_kept_whole(self):
		log = frappe.parse_json(_event_log([{"type": "turn_start"}] * 10))

		self.assertEqual(len(log["events"]), 10)
		self.assertFalse(log["truncated"])

	def test_a_long_log_keeps_the_start_and_the_end(self):
		entries = [{"type": "turn_start", "seq": seq} for seq in range(2000)]

		log = frappe.parse_json(_event_log(entries))

		self.assertEqual(len(log["events"]), EVENT_LOG_HEAD + EVENT_LOG_TAIL)
		self.assertTrue(log["truncated"])
		self.assertEqual(log["events"][0]["seq"], 0)
		self.assertEqual(log["events"][-1]["seq"], 1999)

	def test_a_heavy_log_is_cut_to_the_byte_cap(self):
		"""Few events, each enormous: the count cap would never fire."""
		entries = [{"type": "message_end", "text": "x" * 50_000} for _ in range(40)]

		stored = _event_log(entries)
		log = frappe.parse_json(stored)

		self.assertLessEqual(len(stored), EVENT_LOG_BYTES)
		self.assertTrue(log["truncated"])
		self.assertEqual(log["events"][0]["type"], "message_end")

	def test_one_event_over_the_cap_is_dropped_rather_than_stored(self):
		stored = _event_log([{"type": "message_end", "text": "x" * (EVENT_LOG_BYTES + 1000)}])

		log = frappe.parse_json(stored)
		self.assertLessEqual(len(stored), EVENT_LOG_BYTES)
		self.assertEqual(log["events"], [])
		self.assertTrue(log["truncated"])

	def test_a_log_that_will_not_write_does_not_fail_the_run(self):
		"""A run's answer must not be lost because its diary would not fit."""
		run = make_run(effective_user=RESTRICTED_USER)

		with patch("frappe_agents.runner.run._event_log", side_effect=ValueError("nope")):
			run_with_model(run.name, [SEARCH, ANSWER])

		values = frappe.db.get_value("Agent Run", run.name, ["status", "output_message"], as_dict=True)
		self.assertEqual(values.status, "Completed")
		self.assertEqual(values.output_message, ANSWER["text"])
