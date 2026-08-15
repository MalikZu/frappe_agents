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
	ERROR_LIMIT,
	EVENT,
	EVENT_LOG_BYTES,
	EVENT_LOG_HEAD,
	EVENT_LOG_TAIL,
	EVENT_TEXT_LIMIT,
	RUN_FAILED,
	STREAM_FLUSH_CHARS,
	STREAM_FLUSH_MS,
	STREAM_FRAME_CHARS,
	TRUNCATION_NOTE,
	StreamThrottle,
	_event_log,
	execute_run,
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
	model_streams,
	run_events,
	run_with_model,
	tool_request,
)

SEARCH = model_calls(tool_request("search_documents", {"doctype": TICKET_DT, "fields": ["name"]}))
ANSWER = model_says("One ticket is open.", tokens_in=10, tokens_out=5)
REASON = "The quantities match the signed quotation."

THINKING = "One ticket matched, so the count is one."
STREAMED = model_streams("One ticket is open.", thinking=THINKING, signature="ErUBCkYIBBgC")


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

	# --- the answer as it is written ----------------------------------------

	def updates(self, run_name: str, script) -> list[dict]:
		return [event for event in self.published(run_name, script) if event["type"] == "message_update"]

	def test_a_streamed_answer_reaches_the_browser_as_it_arrives(self):
		run = make_run(effective_user=RESTRICTED_USER)

		updates = self.updates(run.name, [STREAMED])

		shape = [(update["kind"], update["phase"]) for update in updates]
		self.assertEqual(shape[0], ("thinking", "start"))
		self.assertEqual(shape[-1], ("text", "end"))
		self.assertEqual(shape.index(("thinking", "end")) + 1, shape.index(("text", "start")))
		self.assertTrue(all(phase == "delta" for _, phase in shape[1 : shape.index(("thinking", "end"))]))

	def test_the_deltas_add_up_to_what_the_model_wrote(self):
		"""Coalescing may join them, but nothing may be lost or reordered."""
		run = make_run(effective_user=RESTRICTED_USER)

		updates = self.updates(run.name, [STREAMED])

		written = {"thinking": "", "text": ""}
		for update in updates:
			if update["phase"] == "delta":
				written[update["kind"]] += update["delta"]
		self.assertEqual(written["thinking"], THINKING)
		self.assertEqual(written["text"], "One ticket is open.")

	def test_a_block_never_closes_before_the_text_inside_it_was_sent(self):
		"""Held-back deltas are flushed by anything else going out."""
		run = make_run(effective_user=RESTRICTED_USER)

		updates = self.updates(run.name, [STREAMED])

		for position, update in enumerate(updates):
			if update["phase"] == "end":
				before = updates[position - 1]
				self.assertEqual((before["kind"], before["phase"]), (update["kind"], "delta"))

	def test_an_answer_delivered_whole_publishes_no_updates(self):
		"""A provider that does not stream costs the browser nothing extra."""
		run = make_run(effective_user=RESTRICTED_USER)

		self.assertEqual(self.updates(run.name, [SEARCH, ANSWER]), [])

	# --- what the run writes down -------------------------------------------

	def test_the_run_stores_the_events_it_saw(self):
		name = self.run_once()

		types = event_types(run_events(name))
		self.assertEqual(types[0], "agent_start")
		self.assertEqual(types[-1], "agent_end")
		for expected in ("turn_start", "message_end", "tool_execution_start", "tool_execution_end"):
			self.assertIn(expected, types)

	def test_partial_messages_are_not_stored(self):
		"""The log keeps final state. A half-written message has no final state."""
		name = self.run_once([STREAMED])

		self.assertNotIn("message_update", event_types(run_events(name)))

	def test_the_answer_the_model_thought_about_is_in_the_log(self):
		"""Thinking rides inside the assistant message, so it replays like the rest."""
		name = self.run_once([STREAMED])

		ended = [event for event in run_events(name) if event["type"] == "message_end"]
		thinking = [
			block
			for event in ended
			for block in event["message"].get("content") or []
			if isinstance(block, dict) and block.get("type") == "thinking"
		]
		self.assertEqual([block["thinking"] for block in thinking], [THINKING])
		self.assertEqual(thinking[0]["thinkingSignature"], "ErUBCkYIBBgC")

	def test_the_final_answer_is_the_text_alone(self):
		"""Thinking is never the answer. The run's output_message says only the text."""
		name = self.run_once([STREAMED])

		self.assertEqual(frappe.db.get_value("Agent Run", name, "output_message"), "One ticket is open.")

	def test_the_stored_log_ends_on_the_answer_the_run_kept(self):
		"""The chat redraws a finished run from its log, answer included.

		If the last message the log kept said anything other than what the run
		stored as its answer, a reopened conversation would show the answer twice
		— once from the log and once from the row.
		"""
		name = self.run_once([SEARCH, ANSWER])

		ended = [event for event in run_events(name) if event["type"] == "message_end"]
		content = ended[-1]["message"].get("content") or []
		said = "".join(block.get("text") or "" for block in content if block.get("type") == "text")
		self.assertEqual(said, frappe.db.get_value("Agent Run", name, "output_message"))

	def test_every_stored_event_carries_the_number_it_went_out_with(self):
		"""Log order is draw order, and the number says so out loud.

		The live frame has always carried a seq. The stored copy left it to the
		reader to infer from the position in a list whose middle can be dropped.
		"""
		name = self.run_once()

		stored = run_events(name)
		self.assertEqual([event["seq"] for event in stored], list(range(1, len(stored) + 1)))

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
		self.assertEqual(event_types(run_events(name)), ["agent_start", "turn_start", RUN_FAILED])

	# --- how a run says it failed --------------------------------------------

	def failure(self, events: list[dict]) -> dict:
		failures = [event for event in events if event.get("type") == RUN_FAILED]
		self.assertEqual(len(failures), 1)
		return failures[0]

	def test_a_failed_run_writes_the_failure_into_its_own_log(self):
		"""The failure belongs with the rest of the run, not only on the row.

		The log is what a reopened conversation is redrawn from. A failure that
		lived in one realtime frame and a column was news the surface heard once
		and could never ask for again.
		"""
		name = self.run_once([ProviderError("HTTP 401 from the provider.")])

		failure = self.failure(run_events(name))
		self.assertEqual(failure["status"], "Failed")
		self.assertEqual(failure["error"], "HTTP 401 from the provider.")

	def test_the_failure_is_the_last_thing_the_log_says(self):
		"""Log order is draw order, so the reason comes after what led to it."""
		name = self.run_once([SEARCH, ProviderError("The provider is unreachable.")])

		stored = run_events(name)
		self.assertEqual(stored[-1]["type"], RUN_FAILED)
		self.assertIn("tool_execution_end", event_types(stored))

	def test_the_failure_carries_the_number_it_went_out_with(self):
		"""It is one more event in the sequence, numbered like the rest."""
		name = self.run_once([SEARCH, ProviderError("The provider is unreachable.")])

		stored = run_events(name)
		self.assertEqual([event["seq"] for event in stored], list(range(1, len(stored) + 1)))

	def test_a_run_refused_before_the_loop_still_says_why_in_its_log(self):
		"""The identity checks happen before there is a loop to have events.

		A run refused there used to store nothing at all, so the chat had a turn
		that stopped at "Queued…" and a log with no reason in it.
		"""
		run = make_run(effective_user=RESTRICTED_USER)
		frappe.db.set_value("Agent Run", run.name, "effective_user", "Administrator", update_modified=False)

		execute_run(run.name)

		failure = self.failure(run_events(run.name))
		self.assertEqual(failure["status"], "Failed")
		self.assertEqual(failure["error"], "Refusing to run as Administrator.")
		self.assertEqual(failure["seq"], 1)

	def test_the_stored_reason_is_bounded(self):
		"""A provider may answer with a megabyte. The log keeps a summary of it."""
		name = self.run_once([ProviderError("x" * (ERROR_LIMIT * 10))])

		failure = self.failure(run_events(name))
		self.assertEqual(len(failure["error"]), ERROR_LIMIT)
		self.assertEqual(failure["error"], frappe.db.get_value("Agent Run", name, "error"))

	def test_the_failure_goes_out_in_the_envelope_the_log_stores(self):
		"""Live and replayed are the same event, so the surface draws it one way.

		The legacy error frame still goes out behind it: a browser serving an
		older bundle has never heard of this event kind and reads that one.
		"""
		run = make_run(effective_user=RESTRICTED_USER)

		events = self.published(run.name, [ProviderError("The provider is unreachable.")])

		published = [event["event"] for event in events if event["type"] == "harness_event"]
		self.assertEqual(self.failure(published), self.failure(run_events(run.name)))
		self.assertEqual(events[-1]["type"], "error")

	def test_every_frame_says_which_run_and_which_conversation_it_is_for(self):
		"""The two keys a chat surface places a live frame by.

		It cannot place one by the run alone. A run refused before the model is
		called — the identity checks, a disabled agent, a provider answering 400
		in under a second — publishes its whole turn before `start_run` has
		answered with the name of the run, so for those milliseconds the browser
		knows only which conversation it is looking at. A frame that did not say
		would be a frame it could only drop, and a turn stuck at "Queued…" until
		somebody reloaded.
		"""
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			started = start_run(agent=AGENT, message="How many tickets are open?")

		with patch("frappe.publish_realtime") as publish:
			run_with_model(started["run"], [ProviderError("HTTP 401 from the provider.")])

		frames = [call.args[1] for call in publish.call_args_list if call.args[0] == EVENT]
		self.assertTrue(frames)
		for frame in frames:
			self.assertEqual(frame["run"], started["run"])
			self.assertEqual(frame["conversation"], started["conversation"])

	def test_a_reload_gets_the_failure_back(self):
		"""The rehydrate path end to end: the reason comes back through the API."""
		with as_user(RESTRICTED_USER), patch("frappe.enqueue"):
			started = start_run(agent=AGENT, message="How many tickets are open?")

		run_with_model(started["run"], [ProviderError("HTTP 401 from the provider.")])

		with as_user(RESTRICTED_USER):
			runs = get_conversation(started["conversation"])["runs"]

		replayed = next(run for run in runs if run["name"] == started["run"])
		self.assertEqual(self.failure(replayed["event_log"])["error"], "HTTP 401 from the provider.")
		self.assertEqual(replayed["status"], "Failed")

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

	def test_the_log_replays_in_the_order_the_run_happened(self):
		"""A reopened conversation redraws the whole run by walking the log.

		Log order is draw order, so the log has to hold everything the browser
		was told live, in the same places: the tool line between the turn that
		asked for it and the answer that came after it.
		"""
		run = make_run(effective_user=RESTRICTED_USER)

		published = self.published(run.name, [SEARCH, STREAMED])

		live = [event["event"]["type"] for event in published if event["type"] == "harness_event"]
		stored = event_types(run_events(run.name))
		self.assertEqual(stored, live)

		ended = [position for position, name in enumerate(stored) if name == "message_end"]
		self.assertLess(ended[0], stored.index("tool_execution_start"))
		self.assertLess(stored.index("tool_execution_end"), ended[-1])

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

	def test_one_event_over_the_cap_is_cut_short_rather_than_dropped(self):
		"""It is still an event that happened. Only its tail is expendable."""
		stored = _event_log([{"type": "message_end", "text": "x" * (EVENT_LOG_BYTES + 1000)}])

		log = frappe.parse_json(stored)
		self.assertLessEqual(len(stored), EVENT_LOG_BYTES)
		self.assertEqual(len(log["events"]), 1)
		self.assertTrue(log["truncated"])
		self.assertTrue(log["events"][0]["text"].endswith(TRUNCATION_NOTE))

	def test_one_enormous_event_does_not_empty_the_log_around_it(self):
		"""A run that thought for two megabytes still shows what else it did.

		Cutting whole events to fit one of them is the wrong trade: the log's job
		is the shape of the run, and a marker on the long one costs nothing.
		"""
		entries = [
			{"type": "agent_start", "seq": 1},
			{"type": "tool_execution_start", "seq": 2, "toolName": "search_documents"},
			{
				"type": "message_end",
				"seq": 3,
				"message": {"content": [{"type": "thinking", "thinking": "z" * 2_000_000}]},
			},
			{"type": "agent_end", "seq": 4},
		]

		log = frappe.parse_json(_event_log(entries))

		self.assertEqual(
			[event["type"] for event in log["events"]],
			["agent_start", "tool_execution_start", "message_end", "agent_end"],
		)
		self.assertTrue(log["truncated"])
		thinking = log["events"][2]["message"]["content"][0]["thinking"]
		self.assertEqual(len(thinking), EVENT_TEXT_LIMIT + len(TRUNCATION_NOTE))
		self.assertTrue(thinking.endswith(TRUNCATION_NOTE))

	def test_a_log_that_will_not_write_does_not_fail_the_run(self):
		"""A run's answer must not be lost because its diary would not fit."""
		run = make_run(effective_user=RESTRICTED_USER)

		with patch("frappe_agents.runner.run._event_log", side_effect=ValueError("nope")):
			run_with_model(run.name, [SEARCH, ANSWER])

		values = frappe.db.get_value("Agent Run", run.name, ["status", "output_message"], as_dict=True)
		self.assertEqual(values.status, "Completed")
		self.assertEqual(values.output_message, ANSWER["text"])


class TestStreamThrottle(AgentTestCase):
	"""Deltas, on their way to being a few frames instead of hundreds.

	A model writes a handful of characters at a time. Every one of them as its
	own realtime frame is the same answer at fifty times the cost, so they are
	held and sent together — until there are enough of them, until enough time
	has passed, or until something else has to go out first.
	"""

	def setUp(self) -> None:
		super().setUp()
		self.sent: list[tuple] = []
		self.now = 0.0

	def throttle(self) -> StreamThrottle:
		return StreamThrottle(lambda *update: self.sent.append(update), clock=lambda: self.now)

	def test_small_deltas_are_held_and_sent_as_one(self):
		throttle = self.throttle()

		for _ in range(5):
			throttle.add("text", 0, "word ")
		self.assertEqual(self.sent, [])

		throttle.flush()
		self.assertEqual(self.sent, [("text", 0, "delta", "word word word word word ")])

	def test_enough_characters_go_out_without_waiting(self):
		throttle = self.throttle()

		throttle.add("text", 0, "x" * (STREAM_FLUSH_CHARS - 1))
		self.assertEqual(self.sent, [])

		throttle.add("text", 0, "yy")
		self.assertEqual(len(self.sent), 1)
		self.assertEqual(len(self.sent[0][3]), STREAM_FLUSH_CHARS + 1)

		# Nothing is sent twice: what went out is no longer held.
		throttle.flush()
		self.assertEqual(len(self.sent), 1)

	def test_a_slow_answer_goes_out_on_the_clock(self):
		"""Two hundred milliseconds of silence is a long time to watch nothing."""
		throttle = self.throttle()

		throttle.add("text", 0, "Three ")
		self.now += 0.2
		throttle.add("text", 0, "tickets")

		self.assertEqual(self.sent, [("text", 0, "delta", "Three tickets")])

	def test_one_block_is_never_glued_to_another(self):
		"""Two kinds are held side by side, and come out as two updates."""
		throttle = self.throttle()

		throttle.add("thinking", 0, "counting them")
		throttle.add("text", 1, "Three")
		self.assertEqual(self.sent, [])

		throttle.flush()
		self.assertEqual(
			self.sent,
			[("thinking", 0, "delta", "counting them"), ("text", 1, "delta", "Three")],
		)

	def test_flushing_nothing_sends_nothing(self):
		throttle = self.throttle()

		throttle.add("text", 0, "")
		throttle.flush()
		throttle.flush()

		self.assertEqual(self.sent, [])

	# --- the boundaries, held the same way ----------------------------------

	def test_a_block_opens_says_its_piece_and_closes_in_that_order(self):
		throttle = self.throttle()

		throttle.start("text", 0)
		throttle.add("text", 0, "Three.")
		throttle.end("text", 0)
		throttle.flush()

		self.assertEqual(
			self.sent,
			[("text", 0, "start", ""), ("text", 0, "delta", "Three."), ("text", 0, "end", "")],
		)

	def test_a_block_that_reopens_before_its_close_went_out_stays_one_block(self):
		"""The browser was never told it closed, so it never closed."""
		throttle = self.throttle()

		throttle.start("thinking", 0)
		throttle.add("thinking", 0, "counting")
		throttle.end("thinking", 0)
		throttle.start("thinking", 2)
		throttle.add("thinking", 2, " them")
		throttle.flush()

		self.assertEqual(
			self.sent,
			[("thinking", 0, "start", ""), ("thinking", 0, "delta", "counting them")],
		)

	def test_alternating_kinds_cannot_force_a_frame_each(self):
		"""The attack: a stream that changes kind on every single delta.

		One SSE frame may carry reasoning and answer both, so a provider can
		alternate them for as long as it likes. Coalescing per block would flush
		on every change and send a frame per delta — the exact cost the throttle
		exists to avoid. Per kind, alternation is free: what goes out is bounded
		by the clock and by how much was written, never by how often the kind
		changed.
		"""
		throttle = self.throttle()
		throttle.start("thinking", 0)
		throttle.start("text", 1)

		for _ in range(1000):
			throttle.add("thinking", 0, "t")
			throttle.add("text", 1, "x")
			# A millisecond a pair: one second of a very talkative model.
			self.now += 0.001
		throttle.flush()

		# One flush per STREAM_FLUSH_MS and one per STREAM_FLUSH_CHARS, and each
		# flush is at most an open, a delta and a close for each of two kinds.
		flushes = 1000 / STREAM_FLUSH_MS + 2000 / STREAM_FLUSH_CHARS + 1
		self.assertLessEqual(len(self.sent), 3 * 2 * flushes)
		# Unthrottled this is 2000 deltas and 4000 boundary events.
		self.assertLess(len(self.sent), 50)

		written = {"thinking": "", "text": ""}
		for kind, _index, phase, delta in self.sent:
			if phase == "delta":
				written[kind] += delta
		self.assertEqual(written, {"thinking": "t" * 1000, "text": "x" * 1000})

	def test_one_enormous_delta_goes_out_in_frames_a_socket_can_carry(self):
		"""The flush size is a floor. Without a ceiling one frame is the answer."""
		throttle = self.throttle()

		throttle.add("text", 0, "x" * 100_000)
		throttle.flush()

		self.assertGreater(len(self.sent), 1)
		self.assertTrue(all(len(delta) <= STREAM_FRAME_CHARS for *_, delta in self.sent))
		self.assertEqual(
			{(kind, index, phase) for kind, index, phase, _ in self.sent}, {("text", 0, "delta")}
		)
		self.assertEqual("".join(delta for *_, delta in self.sent), "x" * 100_000)
