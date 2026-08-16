# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Tell an already-seeded Agents Builder to describe its Suggested Rules table.

v0.6.1 opened `describe_site_doctype` on the child table a blueprint's suggested
rules live in, and added one sentence to `BUILDER_INSTRUCTIONS` telling the
Builder to call it before writing that table. The tool half reaches every site;
the sentence does not. `seed_agents_builder` is insert-if-missing — the whole
point of it — so on every site that already took v0.6.0 the Agents Builder row
is there, the seed skips it, and the agent keeps the instructions it was created
with. It would gain the ability to describe the table and never be told to, which
leaves the fix half-landed exactly where it is hardest to notice: the Builder
goes on inventing fieldnames and `create_draft` goes on refusing them.

**Nothing here overwrites what a person wrote.** The app has one sanctioned
exception to that rule and it is `flip_openai_to_responses_wire`, not this. So
this does not re-seed the field, and does not compare it against the shipped
text and replace it when it differs. It makes one insertion, at one seam, and
only when that seam is present character for character as v0.6.0 shipped it:

* the added sentence already somewhere in the text — a fresh v0.6.1 install, or
  a second run — and there is nothing to do;
* the sentence it belongs after is not there exactly once — a manager rewrote
  that paragraph, or dropped it — and their text is left completely alone. They
  wrote their own instructions for the write step; a sentence about a tool they
  did not mention would be an edit they never asked for;
* otherwise the sentence goes in directly after it, and every other character of
  the field, including anything a manager appended elsewhere, is untouched.

Written with `db.set_value` rather than a save, deliberately. The seeded Builder
ships with no model profile and Model Profile is mandatory on the form — the
seed itself needs `ignore_mandatory` — so saving the document would either fail
on a row this patch is not here to fix or need that flag to paper over it. One
field is changing; one field is written.

post_model_sync, after `v0_6_0.seed_agents_builder`, which is what puts the row
there in the first place on a site upgrading straight from v0.5.0.
"""

import frappe

from frappe_agents.access.builder import AGENT_DOCTYPE, BUILDER_AGENT

INSTRUCTIONS_FIELD = "instructions"

# Both of these are quoted out of `BUILDER_INSTRUCTIONS` verbatim, which is a
# duplication and therefore a drift risk: change the wording there and this patch
# silently stops matching, finds no seam, and every upgrading site keeps the old
# text while the tests stay green. `tests/test_builder_instructions_patch.py`
# fails the moment ANCHOR and ADDITION stop being pieces of the shipped
# instructions, joined by exactly this JOIN, in exactly this order.
ANCHOR = (
	"When they agree, write the blueprint with create_draft on Agent Blueprint: a title, the purpose "
	"in their words, the suggested rules, and the names of any existing access profiles worth attaching."
)

ADDITION = (
	"Before you write the suggested rules, call describe_site_doctype on Agent Blueprint and then on "
	"the doctype its Suggested Rules table names, so you write that table's real fieldnames instead of "
	"inventing them."
)

JOIN = " "


def execute() -> None:
	# An agent's wording is not worth a failed migrate. Anything this dislikes
	# rolls back to the savepoint and prints: the cost is a Builder that still
	# guesses fieldnames, which is today's behaviour without this patch at all.
	save_point = "frappe_agents_builder_instructions"
	frappe.db.savepoint(save_point)
	try:
		updated = update_seeded_builder()
	except Exception as exc:
		frappe.db.rollback(save_point=save_point)
		print(f"frappe_agents: could not update the {BUILDER_AGENT} instructions — {exc}")
		return

	frappe.db.release_savepoint(save_point)
	if updated:
		print(f"frappe_agents: told {BUILDER_AGENT} to describe its Suggested Rules table first")


def update_seeded_builder() -> bool:
	"""Insert the sentence into the seeded Builder. False when it left it alone.

	False is the ordinary answer on a second run, on a fresh v0.6.1 install, on a
	site whose manager rewrote the instructions, and on a site that deleted the
	agent — all four are sites with nothing to fix.
	"""
	if not frappe.db.exists(AGENT_DOCTYPE, BUILDER_AGENT):
		return False

	stored = frappe.db.get_value(AGENT_DOCTYPE, BUILDER_AGENT, INSTRUCTIONS_FIELD)
	brought_forward = with_addition(stored)
	if brought_forward is None:
		return False

	frappe.db.set_value(AGENT_DOCTYPE, BUILDER_AGENT, INSTRUCTIONS_FIELD, brought_forward)
	frappe.clear_document_cache(AGENT_DOCTYPE, BUILDER_AGENT)
	return True


def with_addition(instructions: str | None) -> str | None:
	"""These instructions with the sentence inserted, or None to leave them alone.

	`count(ANCHOR) != 1` is the never-clobber check and covers both directions:
	zero means the paragraph this belongs to is not the shipped one any more, and
	more than one means there is no single place the sentence obviously goes. In
	either case the honest answer is to write nothing.
	"""
	instructions = instructions or ""
	if ADDITION in instructions:
		return None
	if instructions.count(ANCHOR) != 1:
		return None

	return instructions.replace(ANCHOR, ANCHOR + JOIN + ADDITION)
