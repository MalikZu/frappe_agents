# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Hold back sensitive values in event logs written before the log redacted them.

An Agent Run's event log stores every tool call a second time — arguments and
result — next to the Agent Tool Call row. The row always kept back Password
fields and the fields Agent Settings marks sensitive; the log did not, so an
auditor reading a run saw in one place what the row hid in the other. New runs
store a redacted log. This brings the logs already on the site into line.

It can only redact what the site calls sensitive **today**. A field taken out of
Agent Settings > Sensitive Fields before this ran is not redacted, and a field
added afterwards is redacted only in logs written from then on. The docs say so.

The run's answer and error are stored beside the log and are scrubbed of the
same values, so the answer column and the answer in the log still match. A
value the model repeated is taken out of both.

Each log is rewritten only when redaction changed it, through the same writer
the runner uses, so the shape, the sequence numbers and the caps stay as they
were. A second run finds nothing left to change. `update_modified=False`: the
run did not change, its record did.

Read with `get_all` in batches of `BATCH`, keyed on the name so a rewritten row
never moves the page under the next read. This is migration code with no user
behind it, not tool code.
"""

import frappe

from frappe_agents.redaction import EventRedactor, body_withheld

BATCH = 200


def execute() -> None:
	last = ""
	while True:
		rows = frappe.get_all(
			"Agent Run",
			filters={"event_log": ("is", "set"), "name": (">", last)},
			fields=["name", "event_log", "output_message", "error"],
			order_by="name asc",
			limit=BATCH,
		)
		if not rows:
			return
		for row in rows:
			redact_row(row.name, row.event_log, row.output_message, row.error)
		last = rows[-1].name


def redact_row(name: str, raw: str | None, output: str | None = None, error: str | None = None) -> bool:
	"""Rewrite one run's log, answer and error if redaction changes them. Says whether it did."""
	from frappe_agents.runner.run import _event_log

	try:
		log = frappe.parse_json(raw) or {}
		events = log.get("events")
	except Exception:
		# A log nobody can parse holds nothing anyone can read back either.
		return False
	if not isinstance(events, list):
		return False

	redactor = EventRedactor()
	redacted = [_redacted(redactor, name, event) if isinstance(event, dict) else event for event in events]

	values = {}
	if redacted != events:
		values["event_log"] = _event_log(redacted, bool(log.get("truncated")))
	for field, text in (("output_message", output), ("error", error)):
		scrubbed = redactor.scrub(text)
		if scrubbed != text:
			values[field] = scrubbed
	if not values:
		return False

	frappe.db.set_value("Agent Run", name, values, update_modified=False)
	return True


def _redacted(redactor: EventRedactor, name: str, event: dict) -> dict:
	"""One stored event, redacted.

	Fails closed, like the live path: an event the redactor cannot read keeps
	what it was and when, and loses its body.
	"""
	try:
		return redactor.event(event)
	except Exception:
		frappe.logger("frappe_agents").error(
			f"could not redact event {event.get('seq')} of run {name}", exc_info=True
		)
		return body_withheld(event)
