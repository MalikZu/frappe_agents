# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Write the Run Queue and Run Timeout defaults on a site that had Agent Settings already.

A single doctype stores no default for a field added later, so an upgraded site
showed an empty queue and a timeout of 0 — which reads as "no limit". The code
already falls back to the same values; this makes the form say what runs do.
A value somebody set is left alone.
"""

import frappe

from frappe_agents.runner.queue import DEFAULT_QUEUE, DEFAULT_RUN_TIMEOUT, SETTINGS


def execute() -> None:
	if not frappe.db.get_single_value(SETTINGS, "run_queue"):
		frappe.db.set_single_value(SETTINGS, "run_queue", DEFAULT_QUEUE)
	if not frappe.db.get_single_value(SETTINGS, "run_timeout"):
		frappe.db.set_single_value(SETTINGS, "run_timeout", DEFAULT_RUN_TIMEOUT)
