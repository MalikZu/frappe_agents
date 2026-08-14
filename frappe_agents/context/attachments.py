# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The files hanging off one conversation.

A file uploaded from the chat composer is attached to the Agent Conversation, so
the conversation is the record that owns it and nothing ever deletes it. That was
already true; what was missing was anywhere to read it back from. The chat surface
knew a file existed only from the sentence the composer wrote into the message,
and the agent knew it only for as long as that turn stayed in the model's window.

Two callers, one question, so one query: `get_conversation` draws the strip of
chips from this, and the run's system prompt is built from it at the start of
every run.
"""

from typing import Any

import frappe
from frappe.utils import cint

CONVERSATION = "Agent Conversation"

# One strip of chips, and one prompt section. A conversation with more files than
# this is a conversation whose oldest files are not what anybody came back for.
ATTACHMENT_LIMIT = 50
# How far past the limit the count is allowed to run before it gives up. The tail
# line says how many were left out, and "several hundred" is as exact as that
# sentence ever needs to be.
ATTACHMENT_COUNT_CAP = 500


def _filters(conversation: str) -> dict:
	return {
		"attached_to_doctype": CONVERSATION,
		"attached_to_name": str(conversation),
		"is_folder": 0,
	}


def conversation_attachments(conversation: str, limit: int = ATTACHMENT_LIMIT) -> list[Any]:
	"""The newest files attached to this conversation, oldest last.

	Behind the conversation's own read gate, which every caller here has already
	passed. File's permission check is doctype-level, so a `get_list` would be the
	wrong question twice over: it would hide files that are this conversation's
	own, and it would answer about files that are not. These files belong to this
	record, and whoever may read the record may see what is attached to it — that
	is the rule frappe core's own attachment sidebar works by.
	"""
	rows = frappe.get_all(  # focal-doc-gated: see AGENTS.md ORM rule
		"File",
		fields=["name", "file_name", "file_url", "is_private", "creation"],
		filters=_filters(conversation),
		order_by="creation desc",
		limit=max(1, cint(limit) or ATTACHMENT_LIMIT),
	)
	for row in rows:
		row["is_private"] = bool(cint(row.get("is_private")))
	# Read newest first so a capped page is the newest files, turned round so the
	# strip and the prompt list them in the order they arrived.
	rows.reverse()
	return rows


def conversation_attachment_count(conversation: str, cap: int = ATTACHMENT_COUNT_CAP) -> int:
	"""How many files there are, counted no further than `cap`. Names only."""
	return len(
		frappe.get_all(  # focal-doc-gated: see AGENTS.md ORM rule
			"File",
			filters=_filters(conversation),
			pluck="name",
			limit=cap,
			order_by=None,
		)
	)
