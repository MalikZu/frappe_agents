# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Where agent jobs run, and what happens when nothing runs them.

Workers are patched out: these tests are about the decision, not about RQ.
"""

from unittest.mock import patch

import frappe
from frappe.utils import add_to_date, now_datetime

from frappe_agents.api import start_run
from frappe_agents.patches.v0_7_0.set_run_queue_defaults import execute as set_defaults
from frappe_agents.runner.queue import (
	DEFAULT_QUEUE,
	DEFAULT_RUN_TIMEOUT,
	FALLBACK_QUEUE,
	SETTINGS,
	configured_queue,
	fail_unstarted_runs,
	has_worker,
	queue_warning,
	run_queue,
	run_timeout,
)
from frappe_agents.runner.run import execute_run
from frappe_agents.tests.fixtures import (
	AGENT,
	DRAFT_AGENT,
	DRAFT_USER,
	ORDER_DT,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
	call_tool,
	make_pdf_attachment,
	make_run,
)

BENCH_QUEUES = {"short": 300, "default": 300, "long": 1500, DEFAULT_QUEUE: 1500}


def worker_listens(answer: bool):
	return patch("frappe_agents.runner.queue.has_worker", return_value=answer)


class QueueCase(AgentTestCase):
	def use_setting(self, fieldname: str, value) -> None:
		"""Set one Agent Settings value for this test, and put it back before the rollback.

		Put back explicitly: the single value is cached, and a cache that outlived the
		rollback would carry this value into every later test.
		"""
		before = frappe.db.get_single_value(SETTINGS, fieldname)
		frappe.db.set_single_value(SETTINGS, fieldname, value)
		self.addCleanup(frappe.db.set_single_value, SETTINGS, fieldname, before)


class TestChoosingTheQueue(QueueCase):
	def test_the_configured_queue_is_used_when_a_worker_listens(self):
		self.use_setting("run_queue", DEFAULT_QUEUE)

		with worker_listens(True):
			self.assertEqual(run_queue(), DEFAULT_QUEUE)

	def test_no_worker_on_it_sends_the_job_to_long(self):
		self.use_setting("run_queue", DEFAULT_QUEUE)

		with worker_listens(False):
			self.assertEqual(run_queue(), FALLBACK_QUEUE)

	def test_an_empty_setting_means_the_agents_queue(self):
		"""A site that migrated in has no value stored for the new field."""
		self.use_setting("run_queue", "")

		self.assertEqual(configured_queue(), DEFAULT_QUEUE)


class TestFindingAWorker(AgentTestCase):
	def test_a_queue_the_bench_does_not_know_has_no_worker(self):
		"""frappe.enqueue refuses an unknown queue name, so Redis is not even asked."""
		known = {name: timeout for name, timeout in BENCH_QUEUES.items() if name != DEFAULT_QUEUE}
		with (
			patch("frappe.utils.background_jobs.get_queues_timeout", return_value=known),
			patch("frappe.utils.background_jobs.get_workers") as workers,
		):
			self.assertFalse(has_worker(DEFAULT_QUEUE))

		workers.assert_not_called()

	def test_a_known_queue_with_a_live_worker_has_one(self):
		with (
			patch("frappe.utils.background_jobs.get_queues_timeout", return_value=BENCH_QUEUES),
			patch("frappe.utils.background_jobs.get_workers", return_value=[object()]),
		):
			self.assertTrue(has_worker(DEFAULT_QUEUE))

	def test_a_known_queue_nobody_listens_on_has_none(self):
		with (
			patch("frappe.utils.background_jobs.get_queues_timeout", return_value=BENCH_QUEUES),
			patch("frappe.utils.background_jobs.get_workers", return_value=[]),
		):
			self.assertFalse(has_worker(DEFAULT_QUEUE))

	def test_redis_down_counts_as_no_worker(self):
		with (
			patch("frappe.utils.background_jobs.get_queues_timeout", return_value=BENCH_QUEUES),
			patch("frappe.utils.background_jobs.get_workers", side_effect=ConnectionError),
		):
			self.assertFalse(has_worker(DEFAULT_QUEUE))


class TestTheRunTimeout(QueueCase):
	def test_it_defaults_to_ten_minutes(self):
		self.use_setting("run_timeout", 0)

		self.assertEqual(run_timeout(), DEFAULT_RUN_TIMEOUT)

	def test_a_site_can_set_its_own(self):
		self.use_setting("run_timeout", 120)

		self.assertEqual(run_timeout(), 120)


class TestTheJobsUseIt(QueueCase):
	def test_a_chat_turn_goes_to_the_chosen_queue_with_the_timeout(self):
		self.use_setting("run_queue", DEFAULT_QUEUE)
		self.use_setting("run_timeout", 120)

		with as_user(RESTRICTED_USER), worker_listens(True), patch("frappe.enqueue") as enqueue:
			start_run(agent=AGENT, message="How many tickets are open?")

		self.assertEqual(enqueue.call_args.kwargs["queue"], DEFAULT_QUEUE)
		self.assertEqual(enqueue.call_args.kwargs["timeout"], 120)

	def test_an_extraction_goes_to_the_same_queue(self):
		self.use_setting("run_queue", DEFAULT_QUEUE)
		attachment = make_pdf_attachment()

		with worker_listens(True), patch("frappe.enqueue") as enqueue:
			payload, _ = call_tool(
				DRAFT_USER,
				"extract_document",
				{"file": attachment.name, "target_doctype": ORDER_DT},
				agent=DRAFT_AGENT,
			)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(enqueue.call_args.kwargs["queue"], DEFAULT_QUEUE)


class TestTheSettingsWarning(QueueCase):
	def test_it_names_the_queue_nobody_listens_on(self):
		self.use_setting("run_queue", DEFAULT_QUEUE)

		with worker_listens(False):
			warning = queue_warning()

		self.assertIn(DEFAULT_QUEUE, warning)
		self.assertIn(FALLBACK_QUEUE, warning)

	def test_it_is_quiet_when_a_worker_listens(self):
		self.use_setting("run_queue", DEFAULT_QUEUE)

		with worker_listens(True):
			self.assertIsNone(queue_warning())

	def test_it_is_quiet_when_the_site_chose_long(self):
		self.use_setting("run_queue", FALLBACK_QUEUE)

		with worker_listens(False):
			self.assertIsNone(queue_warning())

	def test_the_form_carries_it(self):
		self.use_setting("run_queue", DEFAULT_QUEUE)

		with worker_listens(False):
			settings = frappe.get_single(SETTINGS)
			settings.run_method("onload")

		self.assertTrue(settings.get_onload().get("queue_warning"))


class TestRunsNoWorkerStarted(QueueCase):
	def setUp(self) -> None:
		super().setUp()
		self.use_setting("run_timeout", DEFAULT_RUN_TIMEOUT)

	def queued_run(self, minutes_ago: int, status: str = "Queued") -> str:
		run = make_run(RESTRICTED_USER)
		created = add_to_date(now_datetime(), minutes=-minutes_ago)
		frappe.db.set_value(
			"Agent Run", run.name, {"creation": created, "status": status}, update_modified=False
		)
		return run.name

	def test_a_run_left_queued_past_twice_the_timeout_is_failed(self):
		stuck = self.queued_run(minutes_ago=60)

		self.assertGreaterEqual(fail_unstarted_runs(), 1)

		status, error = frappe.db.get_value("Agent Run", stuck, ["status", "error"])
		self.assertEqual(status, "Failed")
		self.assertIn("No worker started this run", error)

	def test_a_run_the_sweep_failed_does_not_start_later(self):
		"""Its job can still be in the queue, behind a busy worker."""
		stuck = self.queued_run(minutes_ago=60)
		fail_unstarted_runs()

		execute_run(stuck)

		status, started = frappe.db.get_value("Agent Run", stuck, ["status", "started_at"])
		self.assertEqual(status, "Failed")
		self.assertIsNone(started)

	def test_a_run_still_inside_the_window_is_left_alone(self):
		waiting = self.queued_run(minutes_ago=5)

		fail_unstarted_runs()

		self.assertEqual(frappe.db.get_value("Agent Run", waiting, "status"), "Queued")

	def test_a_run_a_worker_picked_up_is_left_alone(self):
		running = self.queued_run(minutes_ago=60, status="Running")

		fail_unstarted_runs()

		self.assertEqual(frappe.db.get_value("Agent Run", running, "status"), "Running")

	def test_the_sweep_runs_every_hour(self):
		hourly = frappe.get_hooks("scheduler_events", app_name="frappe_agents").get("hourly", [])

		self.assertIn("frappe_agents.runner.queue.fail_unstarted_runs", hourly)


class TestTheDefaultsOnAnUpgradedSite(QueueCase):
	def test_empty_values_get_the_defaults(self):
		self.use_setting("run_queue", "")
		self.use_setting("run_timeout", 0)

		set_defaults()

		self.assertEqual(frappe.db.get_single_value(SETTINGS, "run_queue"), DEFAULT_QUEUE)
		self.assertEqual(frappe.db.get_single_value(SETTINGS, "run_timeout"), DEFAULT_RUN_TIMEOUT)

	def test_a_value_somebody_set_is_kept(self):
		self.use_setting("run_queue", "long")
		self.use_setting("run_timeout", 120)

		set_defaults()

		self.assertEqual(frappe.db.get_single_value(SETTINGS, "run_queue"), "long")
		self.assertEqual(frappe.db.get_single_value(SETTINGS, "run_timeout"), 120)
