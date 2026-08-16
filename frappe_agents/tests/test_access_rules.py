# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The rule row: what it may name, what it may never name, and how rows combine.

Two claims live here. The first is that a rule which could not mean anything is
refused when it is written — a doctype that does not exist, a row with no verb
ticked, a draft verb on a child table. The second is the exclusion, and it is
asserted twice on purpose: once at validation, and once on a row put past
validation the way a direct SQL write would. A gate that only holds on the way
in is not a gate.

One doctype tests both claims at once. The blueprint's own child table is the
one thing the Builder may read the *shape* of, so it is the one place where
"describable" and "grantable" come apart — and the rows below say, in both
places, that reading its shape never made it nameable.
"""

import frappe

from frappe_agents.access.exclusions import is_describable, is_excluded
from frappe_agents.access.grants import compiled_grants, grant_for, require_grant
from frappe_agents.tests.fixtures import (
	ORDER_DT,
	ORDER_ITEM_DT,
	RESTRICTED_USER,
	TEST_REPORT,
	TICKET_DT,
	VAULT_DT,
	AgentTestCase,
	call_tool,
	make_access_profile,
	make_matrix_agent,
	make_run,
	rule,
)
from frappe_agents.tools.base import RUN_FLAG, ToolDenied

BLUEPRINT = "Agent Blueprint"
# The table a blueprint's suggested rules live in. The Builder may read its
# fields; nothing may ever name it in a rule.
RULE_DT = "Agent Access Rule"
REPORT = TEST_REPORT
# The app's own report. It reads Agent Action, which is a doctype no rule may
# name, so no rule may name the report over it either.
APP_REPORT = "Agent Action Review Quality"
# A single settings document nothing in this app owns. Drafting one is not a
# thing that exists, whoever asks.
SINGLE_DT = "Website Settings"


class TestAccessRuleValidation(AgentTestCase):
	def test_a_core_security_doctype_is_refused(self):
		"""User is where permissions come from. An agent that writes it rewrites itself."""
		with self.assertRaises(frappe.ValidationError):
			make_matrix_agent([rule("User", can_read=1)])

	def test_the_apps_own_doctypes_are_refused(self):
		"""Computed from the module, so a governance doctype added later is covered too."""
		self.assertTrue(is_excluded("Agent"))
		self.assertTrue(is_excluded("Agent Tool Call"))
		with self.assertRaises(frappe.ValidationError):
			make_matrix_agent([rule("Agent Action", can_read=1)])

	def test_a_blueprint_is_grantable(self):
		"""The Builder's one write surface. Materialising a blueprint is still a human act."""
		self.assertFalse(is_excluded(BLUEPRINT))

		agent = make_matrix_agent([rule(BLUEPRINT, can_read=1, can_create_draft=1)])

		self.assertTrue(grant_for(agent, BLUEPRINT).get("create_draft"))

	def test_read_alone_on_the_blueprints_child_table_is_still_refused(self):
		"""Describable is not grantable, and Read is the row that proves it.

		The child-table refusal further down only fires when a draft verb is
		ticked, so a row with nothing but Read on it would sail straight past that
		check. What refuses this row is the exclusion — which is why the Builder's
		schema hole is a second predicate and not a widening of this one. A rule
		naming Agent Access Rule is an agent granted the rows that say what agents
		may do.
		"""
		self.assertTrue(is_describable(RULE_DT))
		self.assertTrue(is_excluded(RULE_DT))

		with self.assertRaises(frappe.ValidationError):
			make_matrix_agent([rule(RULE_DT, can_read=1)])

	def test_a_row_that_grants_nothing_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			make_matrix_agent([rule(TICKET_DT)])

	def test_a_target_that_does_not_exist_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			make_matrix_agent([rule("FA No Such DocType", can_read=1)])

	def test_draft_verbs_are_refused_on_a_child_table(self):
		with self.assertRaises(frappe.ValidationError):
			make_matrix_agent([rule(ORDER_ITEM_DT, can_create_draft=1)])

	def test_draft_verbs_are_refused_on_a_single(self):
		with self.assertRaises(frappe.ValidationError):
			make_matrix_agent([rule(SINGLE_DT, can_update_draft=1)])

	def test_a_report_row_with_read_off_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			make_matrix_agent([rule(REPORT, target_type="Report", can_extract=1)])

	def test_a_report_row_keeps_only_its_read(self):
		"""A report is run or it is not. The document verbs are cleared, not honoured."""
		agent = make_matrix_agent(
			[rule(REPORT, target_type="Report", can_read=1, can_create_draft=1, can_propose=1)]
		)

		row = agent.access_rules[0]
		self.assertEqual(row.can_read, 1)
		self.assertEqual(row.can_create_draft, 0)
		self.assertEqual(row.can_propose, 0)

	def test_a_report_over_an_excluded_doctype_is_refused(self):
		"""The exclusions hold on both doors: a report is a way of reading a doctype."""
		with self.assertRaises(frappe.ValidationError):
			make_matrix_agent([rule(APP_REPORT, target_type="Report", can_read=1)])


class TestGrantCompilation(AgentTestCase):
	def test_profiles_and_local_rules_union_their_verbs(self):
		"""Most permissive wins: attaching a profile adds access, it never removes any."""
		profile = make_access_profile([rule(TICKET_DT, can_read=1)])

		agent = make_matrix_agent(
			[rule(TICKET_DT, can_create_draft=1)],
			profiles=[profile.name],
		)

		verbs = grant_for(agent, TICKET_DT)
		self.assertTrue(verbs["read"])
		self.assertTrue(verbs["create_draft"])
		self.assertFalse(verbs["propose"])

	def test_the_smallest_nonzero_cap_wins(self):
		"""A cap only ever narrows, so two of them resolve the other way from a verb."""
		profile = make_access_profile([rule(TICKET_DT, can_read=1, max_rows_per_call=5)])

		agent = make_matrix_agent(
			[rule(TICKET_DT, can_read=1, max_rows_per_call=2)],
			profiles=[profile.name],
		)

		self.assertEqual(grant_for(agent, TICKET_DT)["max_rows_per_call"], 2)

	def test_an_unset_cap_does_not_beat_a_set_one(self):
		profile = make_access_profile([rule(TICKET_DT, can_read=1, max_rows_per_call=5)])

		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], profiles=[profile.name])

		self.assertEqual(grant_for(agent, TICKET_DT)["max_rows_per_call"], 5)

	def test_a_row_smuggled_past_validation_still_denies(self):
		"""The second exclusion check: the row exists, and it grants nothing anyway."""
		agent = make_matrix_agent(
			[rule(TICKET_DT, can_read=1), rule(VAULT_DT, can_read=1)],
			autonomy="Suggest",
		)
		smuggled = agent.access_rules[1]
		frappe.db.set_value("Agent Access Rule", smuggled.name, "target", "Agent Run")
		frappe.clear_document_cache("Agent", agent.name)

		reloaded = frappe.get_cached_doc("Agent", agent.name)
		self.assertNotIn("Agent Run", compiled_grants(reloaded)["DocType"])

		payload, _ = call_tool(
			RESTRICTED_USER, "search_documents", {"doctype": "Agent Run"}, agent=agent.name
		)
		self.assertFalse(payload["ok"])
		self.assertIn("agent framework", payload["error"])

	def test_a_smuggled_row_on_the_blueprints_child_table_still_denies(self):
		"""The same second check, on the one doctype whose schema is readable.

		Validation is not the only thing keeping this row out, and it must not be
		the only thing: `is_describable` widens the schema door for this exact
		name, so the grant door is asserted separately. The row exists, compiles to
		nothing, and the tool still refuses at call time.
		"""
		agent = make_matrix_agent(
			[rule(TICKET_DT, can_read=1), rule(VAULT_DT, can_read=1)],
			autonomy="Suggest",
		)
		smuggled = agent.access_rules[1]
		frappe.db.set_value("Agent Access Rule", smuggled.name, "target", RULE_DT)
		frappe.clear_document_cache("Agent", agent.name)

		reloaded = frappe.get_cached_doc("Agent", agent.name)
		self.assertNotIn(RULE_DT, compiled_grants(reloaded)["DocType"])

		payload, _ = call_tool(RESTRICTED_USER, "search_documents", {"doctype": RULE_DT}, agent=agent.name)
		self.assertFalse(payload["ok"])
		self.assertIn("agent framework", payload["error"])

	def test_a_smuggled_report_row_still_denies(self):
		"""Same second check for reports: the row exists and grants nothing anyway."""
		agent = make_matrix_agent(
			[rule(REPORT, target_type="Report", can_read=1)],
			autonomy="Suggest",
		)
		smuggled = agent.access_rules[0]
		frappe.db.set_value("Agent Access Rule", smuggled.name, "target", APP_REPORT)
		frappe.clear_document_cache("Agent", agent.name)

		reloaded = frappe.get_cached_doc("Agent", agent.name)
		self.assertNotIn(APP_REPORT, compiled_grants(reloaded)["Report"])

		run = make_run(effective_user=RESTRICTED_USER, agent=agent.name)
		previous = frappe.flags.get(RUN_FLAG)
		frappe.flags[RUN_FLAG] = run
		try:
			with self.assertRaises(ToolDenied) as caught:
				require_grant(APP_REPORT, "read", "Report")
		finally:
			frappe.flags[RUN_FLAG] = previous

		self.assertIn("agent framework", str(caught.exception))

	def test_a_rule_on_one_doctype_says_nothing_about_another(self):
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")

		self.assertEqual(grant_for(agent, ORDER_DT), {})
