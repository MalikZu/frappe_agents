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
document, so both go to the model inside the untrusted wrapper. Either can quote
a field held back from the audit trail, so the note is scrubbed of those values
before it is stored and sent, the way a run's answer is. A document name
is cleaned before it sits in a line the system writes. The note is not
translated: the bytes of a turn must not depend on the language of the day.
"""

import re
import unicodedata
from typing import Any

import frappe

from frappe_agents.actions import (
	ACTION_CANCEL,
	ACTION_SUBMIT,
	ACTION_TRANSITION,
	ACTION_UPDATE,
	STATUS_APPLIED,
	STATUS_EXPIRED,
	STATUS_FAILED,
	STATUS_REJECTED,
	proposal_state,
	state_fields,
)
from frappe_agents.context.untrusted import plain, wrap
from frappe_agents.redaction import doctype_sensitive_keys, redact, scrub

CHAT_SURFACE = "Desk Chat"
HEAD = "[Proposal updates, from the system and not from the user]"
TAIL = "[End of proposal updates]"
# How far back a conversation is searched for a proposal not yet told. A decision
# is told on the next turn, so in practice it is the last few runs.
RUN_WINDOW = 200
# How many decisions one note carries. More wait for the next turn.
NOTE_ITEMS = 10
NOTE_TEXT_LIMIT = 1000
TOLD = (STATUS_APPLIED, STATUS_REJECTED, STATUS_FAILED, STATUS_EXPIRED)
# The opening bracket of anything that reads like the note's own markers. Text a
# person wrote loses it, so a line they typed cannot pose as the system's block.
# Matched on a normalised copy: fullwidth letters fold to plain ones there, and
# brackets NFKC does not fold are listed.
_MARKER = re.compile(r"[\[⟦〚【〔⁅❲](?=\s*(?:end\s+of\s+)?proposal\s+updates)", re.IGNORECASE)
# Characters that print as nothing, and so can split a marker without showing.
_INVISIBLE = frozenset("\u00ad\u200b\u200c\u200d\u200e\u200f\u2060\u2061\u2062\u2063\u2064\ufeff")


def note_for(run: Any, delivered_statuses: tuple[str, ...]) -> dict | None:
	"""This run's note, and the proposals it tells, for the caller to store. Once per run.

	`delivered_statuses` are the run statuses whose turns go into the history: a
	proposal listed on such a run has been told. Runs as the run's own user, so
	both reads are that user's own rows through `get_list`.
	"""
	if run.get("proposal_updates_for") or not run.get("conversation"):
		return None
	# Only a person in chat is told. A triggered run answers a document event and
	# a rehearsal is a test: neither has a user who made those proposals.
	if run.get("surface") and run.surface != CHAT_SURFACE:
		return None

	states = _untold(run, delivered_statuses)
	if not states:
		return None

	# Before the note is drawn: a target whose values cannot be read loses its
	# note and failure text here.
	secrets = _held_back(states)
	return {
		"proposal_updates": render(states, secrets),
		"proposal_updates_for": "\n".join(state["action"] for state in states),
	}


def with_note(note: str | None, message: str | None) -> str:
	"""The text of a user turn: the stored note, if there is one, then the message.

	The message is the user's own text, so a marker typed into it is defused.
	"""
	message = defuse(message or "")
	if not note:
		return message
	return f"{note}\n\n{message}"


def defuse(text: str) -> str:
	"""Text a person wrote, with anything shaped like the note's markers made inert.

	The marker is looked for in a copy folded with NFKC and stripped of invisible
	characters, so a lookalike reads as what it looks like. Only the bracket that
	opens a marker is changed, in the text as typed: everything else stays byte
	for byte, because a turn already sent is sent again unchanged.
	"""
	if not text:
		return text
	folded, origin = [], []
	for index, char in enumerate(text):
		if char in _INVISIBLE:
			continue
		for part in unicodedata.normalize("NFKC", char):
			folded.append(part)
			origin.append(index)
	starts = {origin[match.start()] for match in _MARKER.finditer("".join(folded))}
	if not starts:
		return text
	return "".join("(" if index in starts else char for index, char in enumerate(text))


def render(states: list[dict], secrets: set[str] | frozenset = frozenset()) -> str:
	lines = [HEAD]
	lines += [f"- {_line(state, secrets)}" for state in states]
	lines.append(TAIL)
	return "\n".join(lines)


def document_secrets(doctype: str, name: str) -> set[str]:
	"""The values a document holds in its sensitive fields, child rows included.

	Read as the session user, after the permission check. Raises when the
	document cannot be read, so a caller never treats "unknown" as "none".
	"""
	keys = doctype_sensitive_keys(doctype)
	if not keys:
		return set()
	doc = frappe.get_doc(doctype, name)
	if not frappe.has_permission(doctype, "read", doc=doc):
		raise frappe.PermissionError(f"{doctype} {name} cannot be read.")
	return redact(doc.as_dict(), keys)[1]


def _untold(run: Any, delivered_statuses: tuple[str, ...]) -> list[dict]:
	try:
		return _read_untold(run, delivered_statuses)
	except frappe.PermissionError:
		# A user who may not read their own runs or proposals is told nothing,
		# the way the history itself is built for them.
		return []


def _read_untold(run: Any, delivered_statuses: tuple[str, ...]) -> list[dict]:
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


def _held_back(states: list[dict]) -> set[str]:
	"""The sensitive values on the documents these proposals are about.

	A note or a failure can quote them, and the note is stored on the run where an
	auditor reads it. Each document is one the user can read today: a state whose
	target they cannot read carries no text to scrub. A document that cannot be
	read for its values keeps its proposal's line, without the note or the failure.
	"""
	secrets: set[str] = set()
	for state in states:
		if not state.get("target_visible"):
			continue
		try:
			secrets |= document_secrets(state["target_doctype"], state["target_name"])
		except Exception:
			state["decision_note"] = None
			state["failure"] = None
	return secrets


def _authored(text: str, secrets: set[str] | frozenset) -> str:
	"""Text a person wrote, made safe to sit in the note.

	Scrubbed before it is wrapped: a held-back value can be any word, "untrusted"
	included, and scrubbing the finished note would rewrite the wrapper's own tags.
	"""
	return defuse(scrub(str(text), secrets) or "")


def _line(state: dict, secrets: set[str] | frozenset = frozenset()) -> str:
	verb = _verb(state)
	if state.get("target_visible"):
		# A doctype is named by an administrator. A document name can be any text a
		# user typed, so it is read as data like the note is.
		name = plain(_authored(state["target_name"], secrets))
		target = f"{plain(state['target_doctype'])} {wrap(name, 'document name')}"
	else:
		target = "a document you can no longer read"
	subject = f"Your proposal to {verb} {target}"

	status = state["status"]
	if status == STATUS_APPLIED:
		return f"{subject} was approved and applied."
	if status == STATUS_REJECTED:
		line = f"{subject} was rejected."
		if state.get("decision_note"):
			note = wrap(_cut(_authored(state["decision_note"], secrets)), "approver note")
			line += f" The approver wrote: {note}"
		return line
	if status == STATUS_FAILED:
		line = f"{subject} was approved, but applying it failed."
		if state.get("failure"):
			failure = _authored(state["failure"], secrets)
			line += f" The reason: {wrap(_cut(failure), 'failure message')}"
		return line
	return f"{subject} expired with nobody deciding it."


def _verb(state: dict) -> str:
	if state.get("workflow_action"):
		verb = f'apply the workflow action "{plain(state["workflow_action"])}"'
		if state.get("from_workflow_state"):
			verb += f' from the state "{plain(state["from_workflow_state"])}"'
		return f"{verb} to"
	action_type = state.get("action_type")
	if action_type == ACTION_SUBMIT:
		return "submit"
	if action_type == ACTION_CANCEL:
		return "cancel"
	if action_type == ACTION_UPDATE:
		# Field names are set by an administrator, not typed by a user.
		if state.get("update_fields"):
			return f'change the fields "{plain(state["update_fields"])}" on'
		return "change fields on"
	# A transition whose target the user can no longer read has its action held
	# back, and any type this module does not know yet gets the same plain verb.
	# Neither may be called a submit.
	if action_type == ACTION_TRANSITION:
		return "apply a workflow action to"
	return "act on"


def _cut(text: str) -> str:
	text = str(text or "")
	return text if len(text) <= NOTE_TEXT_LIMIT else text[:NOTE_TEXT_LIMIT] + "…"
