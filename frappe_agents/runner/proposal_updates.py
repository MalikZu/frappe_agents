"""What the agent is told about its proposals once a person has decided them.

A proposal is decided by somebody else, after the turn that made it has ended.
So the agent's next turn opens with a short note, in the user's message and
ahead of it, saying how each decided proposal ended: applied, rejected with the
approver's note, failed with the reason, or expired.

The note is written once, at the start of the run, and stored on the run. The
history replays the stored note and never works it out again. Two things depend
on that:

* **The transcript never changes behind the model.** A turn already sent is
  sent again byte for byte, so a provider that caches a stable prefix keeps
  caching it, and a permission that changed since does not rewrite old turns.
* **Each decision is told once.** A proposal is listed on the run that told
  it, and a run that went into the history is the record that it was told. A
  run that was cancelled before it ran never reached the model, so what it
  carried is told again on the next one.

The approver's note and the failure text are written by people or quote the
document, so both go to the model inside the untrusted wrapper. A document name
is cleaned before it sits in a line the system writes. The note is not
translated: the bytes of a turn must not depend on the language of the day.
"""

from typing import Any

import frappe

from frappe_agents.actions import (
	ACTION_CANCEL,
	STATUS_APPLIED,
	STATUS_EXPIRED,
	STATUS_FAILED,
	STATUS_REJECTED,
	proposal_state,
	state_fields,
)
from frappe_agents.context.untrusted import plain, wrap

HEAD = "[Proposal updates, from the system and not from the user]"
TAIL = "[End of proposal updates]"
# How far back a conversation is searched for a proposal not yet told. A decision
# is told on the next turn, so in practice it is the last few runs.
RUN_WINDOW = 200
# How many decisions one note carries. More wait for the next turn.
NOTE_ITEMS = 10
NOTE_TEXT_LIMIT = 1000
TOLD = (STATUS_APPLIED, STATUS_REJECTED, STATUS_FAILED, STATUS_EXPIRED)


def note_for(run: Any, delivered_statuses: tuple[str, ...]) -> dict | None:
	"""This run's note, and the proposals it tells, for the caller to store. Once per run.

	`delivered_statuses` are the run statuses whose turns go into the history: a
	proposal listed on such a run has been told. Runs as the run's own user, so
	both reads are that user's own rows through `get_list`.
	"""
	if run.get("proposal_updates_for") or not run.get("conversation"):
		return None

	states = _untold(run, delivered_statuses)
	if not states:
		return None

	return {
		"proposal_updates": render(states),
		"proposal_updates_for": "\n".join(state["action"] for state in states),
	}


def with_note(note: str | None, message: str | None) -> str:
	"""The text of a user turn: the stored note, if there is one, then the message."""
	if not note:
		return message or ""
	return f"{note}\n\n{message or ''}"


def render(states: list[dict]) -> str:
	lines = [HEAD]
	lines += [f"- {_line(state)}" for state in states]
	lines.append(TAIL)
	return "\n".join(lines)


def _untold(run: Any, delivered_statuses: tuple[str, ...]) -> list[dict]:
	runs = frappe.get_list(
		"Agent Run",
		filters={"conversation": run.conversation, "name": ("!=", run.name)},
		fields=["name", "status", "proposal_updates_for"],
		order_by="creation desc",
		limit_page_length=RUN_WINDOW,
	)
	if not runs:
		return []

	told = set()
	for row in runs:
		if row.status in delivered_statuses and row.proposal_updates_for:
			told.update(row.proposal_updates_for.split())

	rows = frappe.get_list(
		"Agent Action",
		filters={"run": ("in", [row.name for row in runs])},
		fields=state_fields(),
		order_by="creation asc",
		limit_page_length=0,
	)
	untold = []
	for row in rows:
		if row.name in told:
			continue
		state = proposal_state(row)
		if state["status"] not in TOLD:
			continue
		untold.append(state)
		if len(untold) >= NOTE_ITEMS:
			break
	return untold


def _line(state: dict) -> str:
	verb = _verb(state)
	if state.get("target_visible"):
		target = f"{plain(state['target_doctype'])} {plain(state['target_name'])}"
	else:
		target = "a document you can no longer read"
	subject = f"Your proposal to {verb} {target}"

	status = state["status"]
	if status == STATUS_APPLIED:
		return f"{subject} was approved and applied."
	if status == STATUS_REJECTED:
		line = f"{subject} was rejected."
		if state.get("decision_note"):
			note = wrap(_cut(state["decision_note"]), "approver note")
			line += f" The approver wrote: {note}"
		return line
	if status == STATUS_FAILED:
		line = f"{subject} was approved, but applying it failed."
		if state.get("failure"):
			line += f" The reason: {wrap(_cut(state['failure']), 'failure message')}"
		return line
	return f"{subject} expired with nobody deciding it."


def _verb(state: dict) -> str:
	if state.get("workflow_action"):
		return f'apply the workflow action "{plain(state["workflow_action"])}" to'
	return "cancel" if state.get("action_type") == ACTION_CANCEL else "submit"


def _cut(text: str) -> str:
	text = str(text or "")
	return text if len(text) <= NOTE_TEXT_LIMIT else text[:NOTE_TEXT_LIMIT] + "…"
