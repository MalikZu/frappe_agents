# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The kill switch.

Clearing Agent Settings.global_enabled has to stop everything: no new run may be
started, a run already on the queue must not proceed, and a run in flight must
stop before its next tool call and before its next model call. A run stopped
that way is Cancelled, not Failed, and it leaves the same audit trail behind as
one that ran to the end.
"""

import frappe
from frappe.utils import cint

from frappe_agents.api import start_run
from frappe_agents.harness.events import TurnStartEvent
from frappe_agents.runner.run import KILL_SWITCH_MESSAGE, RunCancellation, RunEvents
from frappe_agents.tests.fixtures import (
	AGENT,
	RESTRICTED_USER,
	TICKET_DT,
	AgentTestCase,
	as_user,
	make_run,
	model_calls,
	model_says,
	run_with_model,
	set_kill_switch,
	tool_calls_for,
	tool_request,
)
from frappe_agents.tools.base import (
	KILL_SWITCH_FIELD,
	KILL_SWITCH_KEY,
	SETTINGS,
	KillSwitchActive,
	execute_tool,
	publish_kill_switch,
	runtime_enabled,
)

SEARCH = model_calls(tool_request("search_documents", {"doctype": TICKET_DT}), tokens_in=10, tokens_out=5)


class TestKillSwitch(AgentTestCase):
	def disable_runtime(self) -> None:
		set_kill_switch(0)
		self.addCleanup(set_kill_switch, 1)

	def throw_the_switch_behind_the_caches(self) -> None:
		"""Clear the switch in the database and tell no cache about it.

		This is what a run in flight actually faces. The Desk saves Agent
		Settings in a web worker: that process drops its own cached copy, and
		the worker running the job keeps the copy it already has. So the write
		here goes straight at the stored row, and every cache on the reading
		side is left exactly as the run left it.
		"""
		singles = frappe.qb.DocType("Singles")
		(
			frappe.qb.update(singles)
			.set(singles.value, "0")
			.where((singles.doctype == SETTINGS) & (singles.field == KILL_SWITCH_FIELD))
		).run()
		self.addCleanup(set_kill_switch, 1)

	def assert_the_cache_is_stale(self) -> None:
		"""The control the tests below depend on.

		If the reading side's cached copy said the runtime was off, the tests
		would pass against the code they exist to catch.
		"""
		self.assertTrue(cint(frappe.get_cached_doc(SETTINGS).global_enabled))

	def test_start_run_throws_when_the_runtime_is_off(self):
		self.disable_runtime()
		before = frappe.db.count("Agent Run")

		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.ValidationError):
				start_run(agent=AGENT, message="Anything at all")

		self.assertEqual(frappe.db.count("Agent Run"), before)

	def test_queued_run_is_cancelled_when_the_runtime_is_off(self):
		run = make_run(effective_user=RESTRICTED_USER)
		self.disable_runtime()

		provider = run_with_model(run.name)

		self.assertEqual(provider.call_count, 0)
		values = frappe.db.get_value("Agent Run", run.name, ["status", "error"], as_dict=True)
		self.assertEqual(values.status, "Cancelled")
		self.assertTrue(values.error)
		self.assertEqual(frappe.db.count("Agent Tool Call", {"run": run.name}), 0)

	def test_run_aborts_at_the_next_tool_call(self):
		"""Switch thrown mid-run: the tool is refused and the run stops there."""
		run = make_run(effective_user=RESTRICTED_USER)
		self.addCleanup(set_kill_switch, 1)

		def flip_then_ask_for_a_tool() -> dict:
			set_kill_switch(0)
			return SEARCH

		provider = run_with_model(run.name, [flip_then_ask_for_a_tool])

		self.assertEqual(provider.call_count, 1)

		values = frappe.db.get_value("Agent Run", run.name, ["status", "output_message"], as_dict=True)
		self.assertEqual(values.status, "Cancelled")
		self.assertFalse(values.output_message)

		# The refusal is still audited: an auditor can see what was attempted.
		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
		self.assertEqual(calls[0].tool, "search_documents")

	def test_the_switch_stops_the_run_before_the_next_model_call(self):
		"""Thrown between two tool calls: the second turn never reaches the model.

		The first tool runs and is audited Success. The switch goes off while the
		model is deciding what to do next, so the second tool is refused, the run
		is cancelled, and the third model call the script is holding never
		happens.
		"""
		run = make_run(effective_user=RESTRICTED_USER)
		self.addCleanup(set_kill_switch, 1)

		def flip_then_ask_again() -> dict:
			set_kill_switch(0)
			return SEARCH

		provider = run_with_model(
			run.name,
			[SEARCH, flip_then_ask_again, model_says("Whatever I was never asked.")],
		)

		self.assertEqual(provider.call_count, 2)

		values = frappe.db.get_value("Agent Run", run.name, ["status", "error"], as_dict=True)
		self.assertEqual(values.status, "Cancelled")
		self.assertEqual(values.error, KILL_SWITCH_MESSAGE)

		# One row per attempt, and both attempts are on the record.
		calls = tool_calls_for(run.name)
		self.assertEqual([call.outcome for call in calls], ["Success", "Denied"])

	def test_a_turn_boundary_reads_the_switch(self):
		"""The enforcement point between two model calls, on its own.

		A turn starts, the runner re-reads Agent Settings, and the run's
		cancellation token carries the reason the loop will stop for.
		"""
		run = make_run(effective_user=RESTRICTED_USER)
		cancellation = RunCancellation()
		events = RunEvents(run, cancellation)
		self.disable_runtime()

		events.handle(TurnStartEvent())

		self.assertTrue(cancellation.is_cancelled())
		self.assertEqual(cancellation.reason, KILL_SWITCH_MESSAGE)

	def test_a_turn_boundary_leaves_a_live_run_alone(self):
		"""The positive control: the switch is on, so the turn proceeds."""
		run = make_run(effective_user=RESTRICTED_USER)
		cancellation = RunCancellation()
		events = RunEvents(run, cancellation)

		events.handle(TurnStartEvent())

		self.assertFalse(cancellation.is_cancelled())

	def test_a_turn_boundary_sees_a_switch_thrown_behind_the_caches(self):
		"""The turn-boundary read, against the way the switch is really thrown.

		The run holds a cached copy of Agent Settings from before the switch
		moved. Reading that copy would let the run take another turn.
		"""
		run = make_run(effective_user=RESTRICTED_USER)
		cancellation = RunCancellation()
		events = RunEvents(run, cancellation)
		# The copy the run is holding, taken before the switch moves.
		frappe.get_cached_doc(SETTINGS)
		self.throw_the_switch_behind_the_caches()
		self.assert_the_cache_is_stale()

		events.handle(TurnStartEvent())

		self.assertTrue(cancellation.is_cancelled())
		self.assertEqual(cancellation.reason, KILL_SWITCH_MESSAGE)

	def test_a_tool_call_sees_a_switch_thrown_behind_the_caches(self):
		"""The per-tool read, against the same throw. It is audited as a denial."""
		run = make_run(effective_user=RESTRICTED_USER)
		frappe.get_cached_doc(SETTINGS)
		self.throw_the_switch_behind_the_caches()
		self.assert_the_cache_is_stale()

		with as_user(RESTRICTED_USER):
			with self.assertRaises(KillSwitchActive):
				execute_tool(run, "search_documents", {"doctype": TICKET_DT})

		calls = tool_calls_for(run.name)
		self.assertEqual([call.outcome for call in calls], ["Denied"])

	def test_starting_a_run_sees_a_switch_thrown_behind_the_caches(self):
		"""The same read guards the door: a switched-off runtime queues nothing."""
		frappe.get_cached_doc(SETTINGS)
		self.throw_the_switch_behind_the_caches()
		self.assert_the_cache_is_stale()
		before = frappe.db.count("Agent Run")

		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.ValidationError):
				start_run(agent=AGENT, message="Anything at all")

		self.assertEqual(frappe.db.count("Agent Run"), before)

	def test_the_published_switch_stops_a_run_the_stored_one_cannot(self):
		"""The other half of the read, for the staleness a fresh query cannot fix.

		A worker holds one transaction for the whole run, and the database
		shows that transaction what it saw when it started — so a row another
		connection wrote afterwards stays invisible however often it is asked.
		Saving Agent Settings publishes the switch for exactly this, and the
		runtime is off the moment the published copy says so.
		"""
		publish_kill_switch(0)
		self.addCleanup(set_kill_switch, 1)

		self.assertTrue(cint(frappe.db.get_value(SETTINGS, None, KILL_SWITCH_FIELD)))
		self.assertFalse(runtime_enabled())

	def test_nothing_published_leaves_the_stored_switch_in_charge(self):
		"""A site that has never saved the settings still runs."""
		frappe.cache.delete_value(KILL_SWITCH_KEY)
		self.addCleanup(set_kill_switch, 1)

		self.assertTrue(runtime_enabled())

	def test_tool_call_is_refused_directly_when_the_runtime_is_off(self):
		run = make_run(effective_user=RESTRICTED_USER)
		self.disable_runtime()

		with as_user(RESTRICTED_USER):
			with self.assertRaises(KillSwitchActive):
				execute_tool(run, "search_documents", {"doctype": TICKET_DT})

		calls = tool_calls_for(run.name)
		self.assertEqual(len(calls), 1)
		self.assertEqual(calls[0].outcome, "Denied")
