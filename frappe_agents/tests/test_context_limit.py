# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The window the model actually has.

A profile says how much context its model takes, and a long conversation will
walk past it. What comes back then is a provider refusing the whole call — after
the run has read documents, called tools and spent the time. So the transcript is
trimmed to fit before it is sent: oldest turns first, never this turn's question,
and the run says on its own row that it was cut.
"""

import math

import frappe

from frappe_agents.runner.run import (
	CONTEXT_CHARS_PER_TOKEN,
	CONTEXT_PROMPT_SHARE,
	DEFAULT_CONTEXT_LIMIT,
	_prompt_budget,
)
from frappe_agents.tests.fixtures import (
	AGENT,
	PROFILE,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
	make_conversation,
	make_run,
	model_says,
	run_with_model,
)

PROFILE_DT = "LLM Model Profile"
# Small enough that a handful of ordinary turns will not fit, large enough that
# the system prompt and one turn do.
TINY_LIMIT = 2000
TURN_CHARS = 600
QUESTION = "And what about this one?"


class TestContextLimit(AgentTestCase):
	def set_limit(self, limit: int) -> None:
		frappe.db.set_value(PROFILE_DT, PROFILE, "context_limit", limit, update_modified=False)
		frappe.clear_document_cache(PROFILE_DT, PROFILE)
		self.addCleanup(frappe.clear_document_cache, PROFILE_DT, PROFILE)

	def seed_turns(self, count: int, chars: int = TURN_CHARS) -> tuple[str, list[str]]:
		"""A conversation of `count` finished turns, each one long. Returns their marks."""
		conversation = make_conversation(RESTRICTED_USER).name
		marks = []
		# As the user whose runs they are: a run is readable by the person who
		# started it, so a history seeded by anyone else is a history of nothing.
		with as_user(RESTRICTED_USER):
			for index in range(count):
				mark = f"TURN-{index}"
				prior = make_run(
					effective_user=RESTRICTED_USER,
					agent=AGENT,
					conversation=conversation,
					message=f"{mark} question {'q' * chars}",
				)
				prior.flags.ignore_permissions = True
				prior.db_set(
					{"status": "Completed", "output_message": f"{mark} answer {'a' * chars}"},
					update_modified=False,
				)
				marks.append(mark)
		return conversation, marks

	def prompt(self, provider) -> str:
		"""Everything the model was handed on its first call, as one string."""
		call = provider.calls[0]
		return "\n".join([call["system"]] + [message.text for message in call["messages"]])

	def test_context_limit_trims_history_before_provider_call(self):
		conversation, marks = self.seed_turns(6)
		self.set_limit(TINY_LIMIT)

		run = make_run(
			effective_user=RESTRICTED_USER, agent=AGENT, conversation=conversation, message=QUESTION
		)
		provider = run_with_model(run.name, model_says("Done."))

		prompt = self.prompt(provider)
		# Under the window, measured the same pessimistic way the trimming is.
		self.assertLessEqual(math.ceil(len(prompt) / CONTEXT_CHARS_PER_TOKEN), TINY_LIMIT)

		# The oldest turns are the ones that went, and this turn's question stayed.
		self.assertNotIn(marks[0], prompt)
		self.assertNotIn(marks[1], prompt)
		self.assertIn(marks[-1], prompt)
		self.assertEqual(provider.calls[0]["messages"][-1].text, QUESTION)

		# And the run says it was cut, so a reader can tell that from a model
		# that simply forgot.
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "history_truncated"), 1)

	def test_a_short_conversation_with_no_limit_is_sent_whole(self):
		"""The state every profile is in until an administrator fills the field in.

		Unset is read as a small window, not as no window — but a small window is
		still a window, and a conversation that fits inside it is sent exactly as
		it always was, with nothing recorded as cut.
		"""
		conversation, marks = self.seed_turns(3)
		self.set_limit(0)

		run = make_run(
			effective_user=RESTRICTED_USER, agent=AGENT, conversation=conversation, message=QUESTION
		)
		provider = run_with_model(run.name, model_says("Done."))

		prompt = self.prompt(provider)
		for mark in marks:
			self.assertIn(mark, prompt)
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "history_truncated"), 0)

	def test_a_profile_with_no_limit_still_has_a_ceiling(self):
		"""An unset field is not a licence. It is `DEFAULT_CONTEXT_LIMIT`.

		`context_limit` is a plain Int with no default and no `reqd`, and the
		seeded catalog fills it in for its own profiles alone — so every profile
		an administrator made by hand has none. A budget that answers "no limit"
		to that is a budget that never guards the installations most likely to
		need guarding.
		"""
		self.set_limit(0)

		self.assertEqual(
			_prompt_budget(PROFILE),
			int(DEFAULT_CONTEXT_LIMIT * CONTEXT_PROMPT_SHARE) * CONTEXT_CHARS_PER_TOKEN,
		)

	def test_a_long_conversation_with_no_limit_is_trimmed_to_that_ceiling(self):
		"""And the ceiling is enforced, not merely computed."""
		conversation, marks = self.seed_turns(12, chars=3000)
		self.set_limit(0)

		run = make_run(
			effective_user=RESTRICTED_USER, agent=AGENT, conversation=conversation, message=QUESTION
		)
		provider = run_with_model(run.name, model_says("Done."))

		prompt = self.prompt(provider)
		# What went first, and then the ceiling that made it go: the oldest turns
		# are gone whatever the budget works out to, and it is under the budget.
		self.assertNotIn(marks[0], prompt)
		self.assertLessEqual(len(prompt), _prompt_budget(PROFILE))
		self.assertIn(marks[-1], prompt)
		self.assertEqual(provider.calls[0]["messages"][-1].text, QUESTION)
		self.assertEqual(frappe.db.get_value("Agent Run", run.name, "history_truncated"), 1)
