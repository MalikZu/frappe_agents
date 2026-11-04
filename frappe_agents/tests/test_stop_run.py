# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""A person stopping their own turn.

The kill switch stops every run on the site, and only a manager can throw it.
Stop is the other half: the person a run acts for ends that one run. It is
read at the points the kill switch is read, for the reason the kill switch is
read there — a tool the model already asked for is refused and audited, never
silently dropped — and it reaches a running job through the cache, because the
job's own transaction cannot see the commit that asked.

Nobody else may press it, a finished run has nothing to stop, and stopping a
turn changes no document.
"""

from unittest.mock import patch

import frappe
from frappe import _

from frappe_agents.api import stop_run
from frappe_agents.harness.events import TurnStartEvent
from frappe_agents.runner import run as run_module
from frappe_agents.runner.providers import ProviderStream, _stop_probe
from frappe_agents.runner.run import RunCancellation, RunEvents
from frappe_agents.tests.fixtures import (
	OPEN_USER,
	RESTRICTED_USER,
	SKILL_AUTHOR,
	TICKET_DT,
	AgentTestCase,
	as_user,
	make_run,
	model_calls,
	model_says,
	run_with_model,
	tool_calls_for,
	tool_request,
)
from frappe_agents.tools.base import RUN_STOP_KEY, request_stop, stop_requested

SEARCH = model_calls(tool_request("search_documents", {"doctype": TICKET_DT}), tokens_in=10, tokens_out=5)
# Holds Agent Manager: the role that can throw the kill switch still cannot
# press another person's Stop.
MANAGER_USER = SKILL_AUTHOR


class TestStopRun(AgentTestCase):
	def make_run(self, status: str = "Queued"):
		run = make_run(effective_user=RESTRICTED_USER)
		self.addCleanup(frappe.cache.delete_value, RUN_STOP_KEY.format(run.name))
		if status != "Queued":
			run.db_set("status", status, update_modified=False)
		return run

	def stopped_reason(self) -> str:
		return _("Stopped by you.")

	# --- a running turn ------------------------------------------------------

	def test_owner_stops_a_running_run_at_its_next_turn(self):
		run = self.make_run("Running")

		with as_user(RESTRICTED_USER):
			result = stop_run(run.name)

		self.assertEqual(result, {"run": run.name, "status": "Running"})
		self.assertEqual(stop_requested(run.name), self.stopped_reason())

		cancellation = RunCancellation()
		RunEvents(run, cancellation).handle(TurnStartEvent())
		self.assertTrue(cancellation.is_cancelled())
		self.assertEqual(cancellation.reason, self.stopped_reason())

	def test_a_stopped_run_keeps_the_audit_rows_written_before_the_stop(self):
		"""Stopped between turns: the tool that ran stays on the record, the next call never happens."""
		run = self.make_run()
		real = run_module.execute_tool

		def search_then_stop(run_doc, name, args):
			result = real(run_doc, name, args)
			# The run executes as the person it acts for, so this is them pressing Stop.
			stop_run(run_doc.name)
			return result

		with patch("frappe_agents.runner.run.execute_tool", side_effect=search_then_stop):
			provider = run_with_model(run.name, [SEARCH, model_says("Never said.")])

		self.assertEqual(provider.call_count, 1)
		values = frappe.db.get_value(
			"Agent Run", run.name, ["status", "error", "output_message"], as_dict=True
		)
		self.assertEqual(values.status, "Cancelled")
		self.assertEqual(values.error, self.stopped_reason())
		self.assertFalse(values.output_message)
		self.assertEqual([call.outcome for call in tool_calls_for(run.name)], ["Success"])

	def test_a_tool_asked_for_after_the_stop_is_refused_and_audited(self):
		"""Stopped while the model was answering: what it asked for is refused, on the record."""
		run = self.make_run()

		def stop_then_search() -> dict:
			stop_run(run.name)
			return SEARCH

		provider = run_with_model(run.name, [stop_then_search, model_says("Never said.")])

		self.assertEqual(provider.call_count, 1)
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "status"), "Cancelled")
		calls = tool_calls_for(run.name)
		self.assertEqual([call.outcome for call in calls], ["Denied"])
		self.assertEqual(calls[0].error, self.stopped_reason())

	# --- a queued turn -------------------------------------------------------

	def test_owner_stops_a_queued_run_at_once(self):
		run = self.make_run()

		with as_user(RESTRICTED_USER):
			result = stop_run(run.name)

		self.assertEqual(result, {"run": run.name, "status": "Cancelled"})
		values = frappe.db.get_value("Agent Run", run.name, ["status", "error"], as_dict=True)
		self.assertEqual(values.status, "Cancelled")
		self.assertEqual(values.error, self.stopped_reason())

		# The job that picks it up afterwards does nothing at all.
		provider = run_with_model(run.name, [model_says("Never said.")])
		self.assertEqual(provider.call_count, 0)
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "status"), "Cancelled")
		self.assertEqual(frappe.db.count("Agent Tool Call", {"run": run.name}), 0)

	def test_a_run_stopped_after_the_job_read_it_still_never_calls_the_model(self):
		"""The job read Queued a moment before the Stop landed: the flag still stops it."""
		run = self.make_run()
		request_stop(run.name, self.stopped_reason())

		provider = run_with_model(run.name, [model_says("Never said.")])

		self.assertEqual(provider.call_count, 0)
		values = frappe.db.get_value("Agent Run", run.name, ["status", "error"], as_dict=True)
		self.assertEqual(values.status, "Cancelled")
		self.assertEqual(values.error, self.stopped_reason())

	def test_a_run_that_is_not_queued_is_never_started(self):
		run = self.make_run("Running")

		provider = run_with_model(run.name, [model_says("Never said.")])

		self.assertEqual(provider.call_count, 0)
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "status"), "Running")

	# --- who may stop it -----------------------------------------------------

	def test_nobody_else_can_stop_it(self):
		run = self.make_run()

		for user in (OPEN_USER, MANAGER_USER, "Administrator"):
			with self.subTest(user=user), as_user(user):
				with self.assertRaises(frappe.PermissionError):
					stop_run(run.name)

		self.assertIsNone(stop_requested(run.name))
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "status"), "Queued")

	def test_a_run_that_does_not_exist_is_refused_the_same_way(self):
		with as_user(RESTRICTED_USER):
			with self.assertRaises(frappe.PermissionError):
				stop_run("no-such-run")

	def test_a_finished_run_cannot_be_stopped(self):
		for status in ("Completed", "Failed", "Cancelled"):
			with self.subTest(status=status):
				run = self.make_run(status)
				with as_user(RESTRICTED_USER):
					with self.assertRaises(frappe.ValidationError):
						stop_run(run.name)
				self.assertIsNone(stop_requested(run.name))
				self.assertEqual(frappe.db.get_value("Agent Run", run.name, "status"), status)

	# --- a turn parked in a retry backoff ------------------------------------

	def test_a_stop_ends_a_retry_backoff(self):
		run = self.make_run("Running")
		probe = _stop_probe(run.name)
		self.assertFalse(probe())

		request_stop(run.name, self.stopped_reason())

		self.assertTrue(probe())
		# The wait asks first and returns at once: no further attempt is wanted.
		self.assertFalse(ProviderStream(stopped=probe).backoff(30))
