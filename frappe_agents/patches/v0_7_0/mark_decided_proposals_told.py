# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Count every proposal decided before this upgrade as already told.

The agent learns how a proposal ended from a note at the start of its next
turn. A proposal counts as told once it is listed on a run the history replays.
Proposals decided before the note existed are listed nowhere, so without this
patch the first turn after the upgrade would open with every old decision in the
conversation, long after the user stopped caring about them.

Each conversation's latest replayed run is stamped with its decided proposals.
Only the hidden list is written, never the note itself, so no old turn changes
what it sends to the model. A proposal still pending is left alone and is told
normally once it is decided. Re-running adds nothing: a name already listed is
kept once.

A conversation with no Completed or Failed run is left alone. Only a run the
history replays can record that something was told, and such a conversation has
none. Its decisions are told on its next turn, ten to a turn, like new ones.
Stamping a cancelled or queued run would count for nothing.
"""

import frappe

from frappe_agents.runner.proposal_updates import TOLD
from frappe_agents.runner.run import REPLAYABLE_STATUSES

CHUNK = 500


def execute() -> None:
	stamped = mark_decided_proposals_told()
	if stamped:
		print(f"frappe_agents: {stamped} conversations had their decided proposals marked as told")


def mark_decided_proposals_told() -> int:
	"""Stamp each conversation's latest replayed run and return how many were stamped."""
	actions = frappe.get_all(
		"Agent Action",
		filters={"status": ("in", TOLD), "run": ("is", "set")},
		fields=["name", "run"],
		limit_page_length=0,
	)
	if not actions:
		return 0

	conversation_of = {}
	for chunk in _chunks(sorted({row.run for row in actions})):
		for row in frappe.get_all(
			"Agent Run",
			filters={"name": ("in", chunk), "conversation": ("is", "set")},
			fields=["name", "conversation"],
			limit_page_length=0,
		):
			conversation_of[row.name] = row.conversation

	decided = {}
	for row in actions:
		conversation = conversation_of.get(row.run)
		if conversation:
			decided.setdefault(conversation, set()).add(row.name)

	stamped = 0
	for chunk in _chunks(sorted(decided)):
		latest = {}
		for row in frappe.get_all(
			"Agent Run",
			filters={"conversation": ("in", chunk), "status": ("in", REPLAYABLE_STATUSES)},
			fields=["name", "conversation", "proposal_updates_for"],
			order_by="creation desc",
			limit_page_length=0,
		):
			latest.setdefault(row.conversation, row)

		for conversation, run in latest.items():
			told = set((run.proposal_updates_for or "").split())
			names = told | decided[conversation]
			if names == told:
				continue
			frappe.db.set_value(
				"Agent Run",
				run.name,
				"proposal_updates_for",
				"\n".join(sorted(names)),
				update_modified=False,
			)
			stamped += 1

	return stamped


def _chunks(items: list) -> list[list]:
	return [items[start : start + CHUNK] for start in range(0, len(items), CHUNK)]
