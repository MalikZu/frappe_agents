# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The patch that carries one sentence to an already-seeded Agents Builder.

Two things are being pinned here and they fail in opposite directions.

The first is drift. The patch quotes two sentences out of `BUILDER_INSTRUCTIONS`
and has to keep matching them character for character; nothing else checks that,
because the patch is never imported by the code that owns the text. Reword the
instructions and the patch goes on running, finds no seam, writes nothing, and
reports success on every site — the fix silently stops shipping and the suite
stays green. So the contract is asserted against the shipped constant itself:
both sentences are in it, exactly once, adjacent, in that order — and running
the patch over the v0.6.0 text has to reproduce the v0.6.1 text exactly.

The second is clobbering. This app overwrites an administrator's own words in
exactly one patch, and this is not it. So the other half of these tests is the
text the patch must refuse to touch: a rewritten paragraph, an instructions
field somebody replaced wholesale, and — the case worth being careful about — a
manager who kept the shipped paragraph and appended their own rules underneath,
whose additions have to survive the insertion word for word.
"""

from unittest.mock import patch

import frappe

from frappe_agents.access.builder import AGENT_DOCTYPE, BUILDER_AGENT, BUILDER_INSTRUCTIONS
from frappe_agents.patches.v0_6_1.update_agents_builder_instructions import (
	ADDITION,
	ANCHOR,
	INSTRUCTIONS_FIELD,
	JOIN,
	execute,
	update_seeded_builder,
	with_addition,
)
from frappe_agents.tests.fixtures import AgentTestCase

# What the field held on a site that installed v0.6.0 and only migrated since:
# today's text with the sentence this patch carries taken back out. Derived
# rather than pasted, so it cannot drift from the shipped constant either.
BEFORE = BUILDER_INSTRUCTIONS.replace(JOIN + ADDITION, "")

# A manager's own line, appended under the shipped text. Nothing about it may
# change when the sentence goes in higher up.
MANAGER_ADDITION = "\nAlways answer in the manager's own language, and keep every blueprint to one page.\n"


class TestThePatchStillMatchesTheShippedInstructions(AgentTestCase):
	"""The duplication between `access/builder.py` and the patch, pinned."""

	def test_the_anchor_is_shipped_text_and_appears_once(self):
		self.assertEqual(BUILDER_INSTRUCTIONS.count(ANCHOR), 1)

	def test_the_addition_is_shipped_text_and_appears_once(self):
		self.assertEqual(BUILDER_INSTRUCTIONS.count(ADDITION), 1)

	def test_the_addition_sits_directly_after_the_anchor(self):
		"""Where the patch puts it has to be where the shipped text has it."""
		self.assertIn(ANCHOR + JOIN + ADDITION, BUILDER_INSTRUCTIONS)

	def test_taking_the_sentence_out_changes_exactly_that_sentence(self):
		"""`BEFORE` is the v0.6.0 text, not the v0.6.1 text with a hole in it."""
		self.assertNotIn(ADDITION, BEFORE)
		self.assertIn(ANCHOR, BEFORE)
		self.assertEqual(len(BUILDER_INSTRUCTIONS) - len(BEFORE), len(JOIN + ADDITION))

	def test_the_patch_turns_the_old_text_into_the_shipped_text(self):
		"""The whole contract in one line: upgrade the old, get today's, exactly."""
		self.assertEqual(with_addition(BEFORE), BUILDER_INSTRUCTIONS)


class TestWhatItRefusesToTouch(AgentTestCase):
	def test_the_shipped_text_is_left_alone(self):
		"""A fresh v0.6.1 install, and every run after the first."""
		self.assertIsNone(with_addition(BUILDER_INSTRUCTIONS))

	def test_a_rewritten_paragraph_is_left_alone(self):
		"""No anchor, no insertion. Their write step, their words."""
		rewritten = BEFORE.replace(ANCHOR, "When they agree, write it up however they like.")

		self.assertIsNone(with_addition(rewritten))

	def test_instructions_replaced_wholesale_are_left_alone(self):
		self.assertIsNone(with_addition("Ask what they want. Write it down. Do not guess."))

	def test_an_empty_field_is_left_alone(self):
		for empty in (None, ""):
			with self.subTest(stored=empty):
				self.assertIsNone(with_addition(empty))

	def test_the_sentence_is_not_placed_when_there_are_two_places_for_it(self):
		"""Two anchors is no obvious seam, and guessing at one is the clobber."""
		self.assertIsNone(with_addition(BEFORE + "\n" + BEFORE))

	def test_a_managers_own_lines_survive_the_insertion(self):
		"""The common edit: the shipped text, plus house rules underneath."""
		edited = with_addition(BEFORE + MANAGER_ADDITION)

		self.assertEqual(edited, BUILDER_INSTRUCTIONS + MANAGER_ADDITION)
		self.assertTrue(edited.endswith(MANAGER_ADDITION))
		self.assertIn(ANCHOR + JOIN + ADDITION, edited)

	def test_it_inserts_and_never_removes(self):
		"""Every character that was there is still there, in the same order."""
		edited = with_addition(BEFORE + MANAGER_ADDITION)

		self.assertEqual(edited.replace(JOIN + ADDITION, "", 1), BEFORE + MANAGER_ADDITION)


class TestRunningItAgainstTheSeededBuilder(AgentTestCase):
	def stored(self) -> str:
		return frappe.db.get_value(AGENT_DOCTYPE, BUILDER_AGENT, INSTRUCTIONS_FIELD)

	def put_back(self, instructions: str) -> None:
		frappe.db.set_value(AGENT_DOCTYPE, BUILDER_AGENT, INSTRUCTIONS_FIELD, instructions)
		frappe.clear_document_cache(AGENT_DOCTYPE, BUILDER_AGENT)

	def test_it_brings_an_upgraded_site_up_to_date(self):
		self.put_back(BEFORE)

		self.assertTrue(update_seeded_builder())
		self.assertEqual(self.stored(), BUILDER_INSTRUCTIONS)

	def test_the_document_reads_back_updated_and_not_from_cache(self):
		"""What the runner loads is a document, so the cached copy has to go."""
		self.put_back(BEFORE)
		frappe.get_doc(AGENT_DOCTYPE, BUILDER_AGENT)

		update_seeded_builder()

		self.assertEqual(frappe.get_doc(AGENT_DOCTYPE, BUILDER_AGENT).instructions, BUILDER_INSTRUCTIONS)

	def test_running_it_twice_writes_once(self):
		self.put_back(BEFORE)
		update_seeded_builder()

		self.assertFalse(update_seeded_builder())
		self.assertEqual(self.stored(), BUILDER_INSTRUCTIONS)

	def test_it_does_not_switch_the_builder_on(self):
		"""It carries a sentence. Everything that makes the agent inert is untouched."""
		self.put_back(BEFORE)

		update_seeded_builder()

		agent = frappe.get_doc(AGENT_DOCTYPE, BUILDER_AGENT)
		self.assertFalse(agent.enabled)
		self.assertFalse(agent.model_profile)
		self.assertEqual(agent.autonomy, "Draft")

	def test_a_managers_rewrite_survives_the_patch(self):
		"""The end-to-end of the never-clobber rule, on the real row."""
		theirs = "Interview them, then write a blueprint. Nothing else."
		self.put_back(theirs)

		execute()

		self.assertEqual(self.stored(), theirs)

	def test_a_deleted_builder_is_not_an_error(self):
		"""A site that removed the agent meant to, and a migrate must not care."""
		frappe.delete_doc(AGENT_DOCTYPE, BUILDER_AGENT, force=True, ignore_permissions=True)

		self.assertFalse(update_seeded_builder())

	def test_a_write_that_throws_does_not_fail_the_migrate(self):
		"""A savepoint and a printed line, never an exception out of `execute`."""
		self.put_back(BEFORE)

		with patch.object(frappe.db, "set_value", side_effect=RuntimeError("FA Test write refused")):
			execute()

		self.assertEqual(self.stored(), BEFORE)
