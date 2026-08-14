# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What the v0_7_0 patch does to an agent that predates the access matrix.

The release-blocking question is whether a site can take this upgrade without
anybody noticing. Two answers count as yes, and both are asserted here:

* An agent whose reach can be written down is converted, and afterwards it holds
  every tool it held before, with no verb it did not already have, working on the
  doctypes it named.
* An agent whose reach cannot be written down — it names no doctypes, or it runs
  reports a selection does not identify — is not touched at all, and keeps
  behaving the way it did on the shim.

The narrowing that conversion does bring is asserted too, in
`test_a_doctype_it_never_named_is_refused_afterwards`: a converted agent reads
the doctypes it declared and no longer reads the rest of the site. That is the
point of the matrix, and a test is the right place to say it out loud.
"""

import frappe

from frappe_agents.access.grants import DOCTYPE_TOOL_VERBS, exposed_tool_names, in_legacy_mode
from frappe_agents.patches.v0_7_0.convert_tool_selection_to_access_rules import (
	convert_agent,
	execute,
)
from frappe_agents.tests.fixtures import (
	DRAFT_AGENT,
	DRAFT_USER,
	ORDER_DT,
	ORDER_ITEM_DT,
	PROJECT_DT,
	RESTRICTED_USER,
	SECOND_DRAFTER,
	TICKET_DT,
	TOOL_NAMES,
	VAULT_DT,
	AgentTestCase,
	as_user,
	call_tool,
	make_legacy_agent,
	make_order_draft,
)

# What a reading-and-drafting agent looked like before the matrix: tools picked
# one by one, and no statement anywhere about which documents they applied to.
SELECTION = ("search_documents", "get_doctype_meta", "create_draft", "update_draft", "read_document")

PARTNER_TOOL = "fa_test_partner_tool"


class MigrationTestCase(AgentTestCase):
	def legacy(self, tools=SELECTION, form_doctypes=(TICKET_DT, ORDER_DT), skills=()):
		return make_legacy_agent(tools=tools, form_doctypes=form_doctypes, skills=skills)

	def reload(self, agent):
		frappe.clear_document_cache("Agent", agent.name)
		return frappe.get_doc("Agent", agent.name)

	def rules(self, agent) -> list:
		return self.reload(agent).get("access_rules") or []

	def targets(self, agent) -> list[str]:
		return [row.target for row in self.rules(agent)]

	def tools(self, agent) -> list[str]:
		return [row.tool for row in self.reload(agent).get("tools") or []]

	def verbs(self, tools) -> set[str]:
		return {DOCTYPE_TOOL_VERBS[tool] for tool in tools if tool in DOCTYPE_TOOL_VERBS}

	def partner_tool(self) -> str:
		"""An Agent Tool from some other app: outside the generic family."""
		if not frappe.db.exists("Agent Tool", PARTNER_TOOL):
			frappe.get_doc(
				{
					"doctype": "Agent Tool",
					"tool_name": PARTNER_TOOL,
					"description": "Whatever the partner app does.",
					"handler_path": "frappe_agents.tests.fixtures.model_says",
					"capability": "Read",
					"provider_app": "fa_test_partner",
					"enabled": 1,
				}
			).insert(ignore_permissions=True)
		return PARTNER_TOOL

	def skill(self, doctype: str) -> str:
		"""An approved skill that says which doctype it applies to."""
		skill = frappe.get_doc(
			{
				"doctype": "Agent Skill",
				"skill_title": f"FA Migration Skill {frappe.generate_hash(length=6)}",
				"status": "Approved",
				"body": "Read the vault record before answering.",
				"applies_to_doctypes": [{"document_type": doctype}],
			}
		)
		skill.insert(ignore_permissions=True)
		return skill.name


class TestConvertedAgents(MigrationTestCase):
	def test_before_the_patch_the_agent_runs_on_the_shim(self):
		agent = self.legacy()

		self.assertTrue(in_legacy_mode(agent))
		self.assertEqual(exposed_tool_names(agent), set(SELECTION))

	def test_every_tool_it_was_offered_is_still_offered(self):
		"""The release gate: the toolset does not shrink under an upgrade."""
		agent = self.legacy()
		before = exposed_tool_names(agent)

		self.assertTrue(convert_agent(agent))

		after = exposed_tool_names(self.reload(agent))
		self.assertTrue(before <= after, sorted(before - after))

	def test_conversion_grants_no_verb_the_selection_did_not_hold(self):
		"""It may offer more tools of a verb it had. It never adds a verb."""
		agent = self.legacy()
		before = exposed_tool_names(agent)

		convert_agent(agent)

		self.assertEqual(self.verbs(exposed_tool_names(self.reload(agent))), self.verbs(before))

	def test_it_writes_one_rule_per_doctype_the_agent_named(self):
		agent = self.legacy()

		convert_agent(agent)

		self.assertEqual(self.targets(agent), [TICKET_DT, ORDER_DT])
		for row in self.rules(agent):
			self.assertEqual(row.target_type, "DocType")
			self.assertTrue(row.can_read)
			self.assertTrue(row.can_create_draft)
			self.assertTrue(row.can_update_draft)
			self.assertFalse(row.can_propose)
			self.assertFalse(row.can_extract)

	def test_converted_rules_carry_no_cap(self):
		"""Caps are new. The tools' own maximums were the only limit before."""
		agent = self.legacy()

		convert_agent(agent)

		self.assertEqual({row.max_rows_per_call for row in self.rules(agent)}, {0})

	def test_the_doctypes_it_named_still_answer(self):
		agent = self.legacy()
		convert_agent(agent)

		payload, _ = call_tool(RESTRICTED_USER, "search_documents", {"doctype": TICKET_DT}, agent=agent.name)

		self.assertTrue(payload["ok"], payload["error"])

	def test_a_doctype_it_never_named_is_refused_afterwards(self):
		"""The deliberate narrowing: the matrix reaches what the agent declared."""
		agent = self.legacy()
		convert_agent(agent)

		with as_user(RESTRICTED_USER):
			self.assertTrue(frappe.has_permission(PROJECT_DT, "read"))

		payload, _ = call_tool(RESTRICTED_USER, "search_documents", {"doctype": PROJECT_DT}, agent=agent.name)

		self.assertFalse(payload["ok"])
		self.assertIn("no access rule", payload["error"])

	def test_another_users_draft_is_still_editable(self):
		"""update_draft had no owner test, so the converted rule has to say so."""
		draft = make_order_draft(user=DRAFT_USER)
		agent = self.legacy(form_doctypes=(ORDER_DT,))

		convert_agent(agent)

		payload, _ = call_tool(
			SECOND_DRAFTER,
			"update_draft",
			{"doctype": ORDER_DT, "name": draft.name, "values": {"amount": 42}},
			agent=agent.name,
		)
		self.assertTrue(payload["ok"], payload["error"])

	def test_file_reading_moves_from_the_selection_to_the_flag(self):
		agent = self.legacy()

		convert_agent(agent)

		converted = self.reload(agent)
		self.assertTrue(converted.may_read_files)
		self.assertNotIn("read_document", self.tools(agent))
		self.assertIn("read_document", exposed_tool_names(converted))

	def test_the_generic_rows_go_and_another_apps_tool_stays(self):
		"""The matrix decides the generic family only. A partner tool is still picked."""
		agent = self.legacy(tools=("search_documents", self.partner_tool()))

		convert_agent(agent)

		self.assertEqual(self.tools(agent), [PARTNER_TOOL])
		self.assertIn(PARTNER_TOOL, exposed_tool_names(self.reload(agent)))

	def test_a_skill_is_a_source_of_doctypes_too(self):
		"""The other place an agent already says what it is about."""
		agent = self.legacy(form_doctypes=(), skills=(self.skill(VAULT_DT),))

		self.assertTrue(convert_agent(agent))

		self.assertEqual(self.targets(agent), [VAULT_DT])

	def test_an_excluded_doctype_never_becomes_a_rule(self):
		agent = self.legacy(form_doctypes=("Agent", TICKET_DT))

		convert_agent(agent)

		self.assertEqual(self.targets(agent), [TICKET_DT])


class TestAgentsLeftAlone(MigrationTestCase):
	def test_an_agent_that_names_no_doctypes_keeps_its_selection(self):
		"""Its reach cannot be written down, so nothing about it changes."""
		agent = self.legacy(form_doctypes=())
		before = exposed_tool_names(agent)

		self.assertFalse(convert_agent(agent))

		after = self.reload(agent)
		self.assertEqual(self.rules(agent), [])
		self.assertEqual(self.tools(agent), list(SELECTION))
		self.assertTrue(in_legacy_mode(after))
		self.assertEqual(exposed_tool_names(after), before)

	def test_an_agent_that_runs_reports_keeps_its_selection(self):
		"""A selection does not say which reports, and guessing would widen it."""
		agent = self.legacy(tools=("search_documents", "run_report"))

		self.assertFalse(convert_agent(agent))

		self.assertEqual(self.rules(agent), [])
		self.assertIn("run_report", exposed_tool_names(self.reload(agent)))

	def test_an_agent_with_no_generic_tools_is_not_touched(self):
		agent = self.legacy(tools=(self.partner_tool(),))

		self.assertFalse(convert_agent(agent))

		self.assertEqual(self.tools(agent), [PARTNER_TOOL])

	def test_an_agent_that_only_reads_files_alongside_a_partner_tool_is_left_alone(self):
		"""Converting it would empty the rules table and drop it back on the shim."""
		agent = self.legacy(tools=("read_document", self.partner_tool()))

		self.assertFalse(convert_agent(agent))

		self.assertFalse(self.reload(agent).may_read_files)
		self.assertIn("read_document", exposed_tool_names(self.reload(agent)))

	def test_a_doctype_that_cannot_carry_the_verbs_leaves_it_alone(self):
		"""Drafting a child table is not a thing, so the conversion produces no rule."""
		agent = self.legacy(tools=("create_draft",), form_doctypes=(ORDER_ITEM_DT,))

		self.assertFalse(convert_agent(agent))

		self.assertEqual(self.rules(agent), [])


class TestPatchRun(MigrationTestCase):
	def test_running_the_patch_twice_changes_nothing_the_second_time(self):
		agent = self.legacy()

		execute()
		first = [(row.target, row.can_read, row.can_create_draft) for row in self.rules(agent)]

		execute()

		self.assertEqual(
			[(row.target, row.can_read, row.can_create_draft) for row in self.rules(agent)], first
		)
		self.assertFalse(convert_agent(self.reload(agent)))

	def test_the_patch_converts_what_it_can_and_leaves_the_rest(self):
		agent = self.legacy()

		execute()

		self.assertTrue(self.rules(agent))
		# The shared fixture agent holds run_report, so the patch must not have
		# touched it — and it still holds everything it was given.
		self.assertEqual(exposed_tool_names(frappe.get_doc("Agent", DRAFT_AGENT)), set(TOOL_NAMES))
