# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Which model a run actually ran on, and who is allowed to decide that.

The run row is the audit record, so it has to name the profile the provider was
built from — not the agent's default, which is only the answer when nothing
overrode it. And an override is re-checked here, at the last moment before the
provider exists: a run row is a queued instruction, and the roles it was written
under can be gone by the time a worker picks it up.
"""

import frappe

from frappe_agents.tests.fixtures import (
	AGENT,
	ALT_PROFILE,
	EXTRACT_PROFILE,
	GATED_PROFILE,
	OPEN_USER,
	PROFILE,
	RESTRICTED_USER,
	AgentTestCase,
	make_run,
	model_says,
	run_with_model,
)

ANSWER = model_says("One ticket is open.")


def run_values(run_name: str) -> dict:
	return frappe.db.get_value("Agent Run", run_name, ["status", "error", "model_profile"], as_dict=True)


class TestRunModelProfile(AgentTestCase):
	def test_a_run_records_the_agents_own_profile(self):
		run = make_run(effective_user=RESTRICTED_USER)

		provider = run_with_model(run.name, ANSWER)

		self.assertEqual(provider.calls[0]["model"], PROFILE)
		self.assertEqual(run_values(run.name).model_profile, PROFILE)

	def test_a_run_records_the_override_it_was_queued_with(self):
		run = make_run(effective_user=RESTRICTED_USER, model_profile=ALT_PROFILE)

		provider = run_with_model(run.name, ANSWER)

		# The override is what reached the loop, and what the row now says ran.
		self.assertEqual(provider.calls[0]["model"], ALT_PROFILE)
		values = run_values(run.name)
		self.assertEqual(values.status, "Completed")
		self.assertEqual(values.model_profile, ALT_PROFILE)

	def test_a_profile_the_agent_never_listed_fails_the_run(self):
		run = make_run(effective_user=RESTRICTED_USER, model_profile=EXTRACT_PROFILE)

		provider = run_with_model(run.name, ANSWER)

		self.assertEqual(provider.call_count, 0)
		values = run_values(run.name)
		self.assertEqual(values.status, "Failed")
		self.assertIn(EXTRACT_PROFILE, values.error)

	def test_a_profile_the_effective_users_roles_fail_fails_the_run(self):
		run = make_run(effective_user=RESTRICTED_USER, model_profile=GATED_PROFILE)

		provider = run_with_model(run.name, ANSWER)

		self.assertEqual(provider.call_count, 0)
		values = run_values(run.name)
		self.assertEqual(values.status, "Failed")
		self.assertIn(GATED_PROFILE, values.error)

	def test_a_gated_profile_runs_for_a_user_who_holds_the_role(self):
		run = make_run(effective_user=OPEN_USER, model_profile=GATED_PROFILE)

		provider = run_with_model(run.name, ANSWER)

		self.assertEqual(provider.calls[0]["model"], GATED_PROFILE)
		self.assertEqual(run_values(run.name).model_profile, GATED_PROFILE)

	def test_a_disabled_alternate_is_refused(self):
		frappe.db.set_value("LLM Model Profile", ALT_PROFILE, "enabled", 0)
		frappe.clear_document_cache("LLM Model Profile", ALT_PROFILE)
		self.addCleanup(frappe.clear_document_cache, "LLM Model Profile", ALT_PROFILE)
		self.addCleanup(frappe.db.set_value, "LLM Model Profile", ALT_PROFILE, "enabled", 1)

		run = make_run(effective_user=RESTRICTED_USER, agent=AGENT, model_profile=ALT_PROFILE)
		provider = run_with_model(run.name, ANSWER)

		self.assertEqual(provider.call_count, 0)
		self.assertEqual(run_values(run.name).status, "Failed")
