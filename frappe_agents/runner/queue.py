# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Which RQ queue an agent job runs on, and how long a chat turn may take.

A chat turn holds its worker for as long as the model takes, often minutes. On a
bench with one worker for `long`, `default` and `short`, that turn holds up every
email, report and scheduled job behind it. So agent jobs go to their own queue,
named in Agent Settings, and a bench gives that queue its own worker.

A queue with no worker is worse than a busy one: the job waits for ever and the
chat shows "Queued…" with nothing to explain it. So when nothing listens on the
configured queue, the job goes to `long` instead, Agent Settings says so, and a
run that no worker ever started is failed with a reason.
"""

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, now_datetime

SETTINGS = "Agent Settings"
DEFAULT_QUEUE = "agents"
FALLBACK_QUEUE = "long"
DEFAULT_RUN_TIMEOUT = 600


def run_queue() -> str:
	"""The queue to enqueue on now: the configured one if a worker listens, else `long`."""
	wanted = configured_queue()
	return wanted if has_worker(wanted) else FALLBACK_QUEUE


def run_timeout() -> int:
	"""Seconds a chat turn may run before RQ stops it."""
	return cint(frappe.db.get_single_value(SETTINGS, "run_timeout")) or DEFAULT_RUN_TIMEOUT


def queue_warning() -> str | None:
	"""Why agent jobs are not on the configured queue, or None when they are."""
	wanted = configured_queue()
	if wanted == FALLBACK_QUEUE or has_worker(wanted):
		return None
	return _(
		"No worker listens on the {0} queue, so agent runs use the {1} queue and wait behind "
		"other long jobs. Add the {0} queue to this bench's workers. See the admin docs."
	).format(frappe.bold(wanted), frappe.bold(FALLBACK_QUEUE))


def fail_unstarted_runs() -> int:
	"""Fail runs no worker started in twice the run timeout. Hourly job.

	Selects only Queued rows, so a run a worker picked up meanwhile is left alone.
	"""
	from frappe_agents.runner.run import fail_unstarted_run

	window = 2 * run_timeout()
	cutoff = add_to_date(now_datetime(), seconds=-window)
	# Scheduler code, not tool code: it runs as nobody and decides nothing for a user.
	stuck = frappe.get_all("Agent Run", filters={"status": "Queued", "creation": ("<", cutoff)}, pluck="name")
	message = (
		f"No worker started this run within {window // 60} minutes. The agent queue has no "
		"worker, or its workers were busy. Send the message again."
	)
	for name in stuck:
		fail_unstarted_run(name, message)
	return len(stuck)


def configured_queue() -> str:
	return (frappe.db.get_single_value(SETTINGS, "run_queue") or "").strip() or DEFAULT_QUEUE


def has_worker(queue: str) -> bool:
	"""Whether a live worker listens on `queue`.

	A name missing from the bench's queue list is refused by `frappe.enqueue`, so it
	counts as having no worker without asking Redis.
	"""
	from frappe.utils.background_jobs import get_queue, get_queues_timeout, get_workers

	if queue not in get_queues_timeout():
		return False
	try:
		return bool(get_workers(get_queue(queue)))
	except Exception:
		# Redis down means nothing runs anywhere; `long` is no worse a place to wait.
		return False
