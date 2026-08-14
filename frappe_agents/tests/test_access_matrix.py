# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The matrix in force: what the model is offered, and what a call is allowed.

Exposure and enforcement are the same question asked twice. A tool is offered
only when the compiled grant could let the agent use it, and every call is then
checked again against the specific target — so the model is never shown a tool
that always refuses, and never gets past the one it was shown.

Underneath both sits the intersection. A rule can only narrow what the invoking
user may already do: the frappe permission check stayed exactly where it was in
every tool, and the grant is checked beside it.

The legacy shim is asserted here too, because it is what keeps a site that has
not migrated yet working: an agent with no rules and a tool selection behaves
today the way it behaved yesterday.
"""

import frappe

from frappe_agents.access.grants import exposed_tool_names
from frappe_agents.tests.fixtures import (
	DRAFT_AGENT,
	DRAFT_USER,
	OPEN_USER,
	ORDER_DT,
	ORDER_LIVE,
	PROJECT_ALPHA,
	PROJECT_DT,
	RESTRICTED_USER,
	SECOND_DRAFTER,
	TICKET_DT,
	TOOL_NAMES,
	VAULT_DT,
	AgentTestCase,
	as_user,
	call_tool,
	make_attachment,
	make_matrix_agent,
	make_order_draft,
	make_run,
	rule,
	tool_calls_for,
)
from frappe_agents.tools.base import RUN_FLAG, ToolDenied
from frappe_agents.tools.read_tools import read_document
from frappe_agents.tools.registry import get_tool_schemas

READ_FAMILY = {
	"search_documents",
	"get_doctype_meta",
	"get_document_context",
	"get_document_slice",
	"find_doctypes",
}
REPORT = "Agent Action Review Quality"


class TestToolExposure(AgentTestCase):
	def test_an_empty_matrix_holds_no_generic_tools(self):
		"""Deny by default. Nothing granted is nothing offered, and nothing callable."""
		agent = make_matrix_agent([])

		self.assertEqual(exposed_tool_names(agent), set())
		self.assertEqual(get_tool_schemas(agent), [])

		payload, _ = call_tool(DRAFT_USER, "search_documents", {"doctype": TICKET_DT}, agent=agent.name)
		self.assertFalse(payload["ok"])
		self.assertIn("not enabled", payload["error"])

	def test_a_read_rule_offers_the_read_family_and_nothing_else(self):
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")

		self.assertEqual(exposed_tool_names(agent), READ_FAMILY)

	def test_each_write_verb_offers_its_own_tools(self):
		agent = make_matrix_agent(
			[rule(ORDER_DT, can_create_draft=1, can_update_draft=1, can_propose=1, can_extract=1)]
		)

		self.assertEqual(
			exposed_tool_names(agent),
			{
				"create_draft",
				"create_drafts",
				"update_draft",
				"propose_submit",
				"propose_cancel",
				"extract_document",
			},
		)

	def test_proposals_need_the_autonomy_as_well_as_the_rule(self):
		"""The ceiling is unchanged: a Suggest agent proposes nothing, rule or no rule."""
		agent = make_matrix_agent([rule(ORDER_DT, can_propose=1)], autonomy="Suggest")

		self.assertFalse({"propose_submit", "propose_cancel"} & exposed_tool_names(agent))

	def test_a_report_rule_is_what_offers_run_report(self):
		"""run_report used to ride tool selection. It rides a Report row now."""
		reader = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")
		self.assertNotIn("run_report", exposed_tool_names(reader))

		runner = make_matrix_agent(
			[rule(TICKET_DT, can_read=1), rule(REPORT, target_type="Report", can_read=1)],
			autonomy="Suggest",
		)
		self.assertIn("run_report", exposed_tool_names(runner))

	def test_an_agent_with_a_selection_and_no_rules_is_left_alone(self):
		"""The legacy shim: a site that has not migrated behaves exactly as before."""
		agent = frappe.get_cached_doc("Agent", DRAFT_AGENT)

		self.assertEqual(exposed_tool_names(agent), set(TOOL_NAMES))

		payload, _ = call_tool(RESTRICTED_USER, "search_documents", {"doctype": TICKET_DT}, agent=DRAFT_AGENT)
		self.assertTrue(payload["ok"], payload["error"])


class TestIntersection(AgentTestCase):
	def test_a_rule_the_user_lacks_the_permission_for_is_refused_and_audited(self):
		agent = make_matrix_agent(
			[rule(TICKET_DT, can_read=1), rule(VAULT_DT, can_read=1)], autonomy="Suggest"
		)

		payload, run = call_tool(RESTRICTED_USER, "search_documents", {"doctype": VAULT_DT}, agent=agent.name)

		self.assertFalse(payload["ok"])
		self.assertIn(VAULT_DT, payload["error"])
		self.assertEqual([call.outcome for call in tool_calls_for(run.name)], ["Denied"])

	def test_a_permission_with_no_rule_is_refused(self):
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")

		with as_user(RESTRICTED_USER):
			self.assertTrue(frappe.has_permission(PROJECT_DT, "read"))

		payload, _ = call_tool(RESTRICTED_USER, "search_documents", {"doctype": PROJECT_DT}, agent=agent.name)

		self.assertFalse(payload["ok"])
		self.assertIn("no access rule", payload["error"])

	def test_a_rule_and_a_permission_together_allow_it(self):
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")

		payload, _ = call_tool(RESTRICTED_USER, "search_documents", {"doctype": TICKET_DT}, agent=agent.name)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertTrue(payload["result"]["rows"])


class TestNarrowingCaps(AgentTestCase):
	def rows(self, count: int) -> list[dict]:
		return [
			{"order_title": f"FA Cap {index} {frappe.generate_hash(length=6)}", "project": PROJECT_ALPHA}
			for index in range(count)
		]

	def test_a_rule_cap_narrows_the_bulk_tool(self):
		agent = make_matrix_agent([rule(ORDER_DT, can_create_draft=1, max_rows_per_call=2)])

		payload, _ = call_tool(
			DRAFT_USER,
			"create_drafts",
			{"doctype": ORDER_DT, "rows": self.rows(3)},
			agent=agent.name,
		)

		self.assertFalse(payload["ok"])
		self.assertIn("2 is the limit", payload["error"])

	def test_no_rule_cap_leaves_the_tools_own(self):
		agent = make_matrix_agent([rule(ORDER_DT, can_create_draft=1)])

		payload, _ = call_tool(
			DRAFT_USER,
			"create_drafts",
			{"doctype": ORDER_DT, "rows": self.rows(3)},
			agent=agent.name,
		)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(payload["result"]["succeeded"], 3)

	def test_a_rule_cap_narrows_a_search(self):
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1, max_rows_per_call=1)], autonomy="Suggest")

		payload, _ = call_tool(
			OPEN_USER, "search_documents", {"doctype": TICKET_DT, "limit": 50}, agent=agent.name
		)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(payload["result"]["limit"], 1)
		self.assertEqual(len(payload["result"]["rows"]), 1)


class TestDraftOwnership(AgentTestCase):
	def edit(self, user: str, agent: str, name: str) -> dict:
		payload, _ = call_tool(
			user,
			"update_draft",
			{"doctype": ORDER_DT, "name": name, "values": {"amount": 42}},
			agent=agent,
		)
		return payload

	def test_someone_elses_draft_is_refused_by_default(self):
		draft = make_order_draft(user=DRAFT_USER)
		agent = make_matrix_agent([rule(ORDER_DT, can_update_draft=1)])

		payload = self.edit(SECOND_DRAFTER, agent.name, draft.name)

		self.assertFalse(payload["ok"])
		self.assertIn("its own user created", payload["error"])

	def test_your_own_draft_is_allowed(self):
		draft = make_order_draft(user=SECOND_DRAFTER)
		agent = make_matrix_agent([rule(ORDER_DT, can_update_draft=1)])

		payload = self.edit(SECOND_DRAFTER, agent.name, draft.name)

		self.assertTrue(payload["ok"], payload["error"])

	def test_update_any_draft_opens_both(self):
		draft = make_order_draft(user=DRAFT_USER)
		agent = make_matrix_agent([rule(ORDER_DT, can_update_draft=1, update_any_draft=1)])

		payload = self.edit(SECOND_DRAFTER, agent.name, draft.name)

		self.assertTrue(payload["ok"], payload["error"])

	def test_update_any_draft_is_still_only_drafts(self):
		"""Docstatus 0, always. Whose draft it is was never the only question."""
		agent = make_matrix_agent([rule(ORDER_DT, can_update_draft=1, update_any_draft=1)])

		payload = self.edit(DRAFT_USER, agent.name, ORDER_LIVE)

		self.assertFalse(payload["ok"])
		self.assertIn("not a draft", payload["error"])


class TestDiscovery(AgentTestCase):
	def found(self, user: str, agent: str, payload: dict | None = None) -> dict:
		result, _ = call_tool(user, "find_doctypes", payload or {}, agent=agent)
		self.assertTrue(result["ok"], result["error"])
		return result["result"]

	def test_find_doctypes_answers_only_granted_targets(self):
		"""Discovery is an information boundary: an ungranted doctype is not named."""
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")

		found = self.found(RESTRICTED_USER, agent.name)

		self.assertEqual({row["doctype"] for row in found["doctypes"]}, {TICKET_DT})

	def test_find_doctypes_drops_what_the_user_may_not_read(self):
		agent = make_matrix_agent(
			[rule(TICKET_DT, can_read=1), rule(VAULT_DT, can_read=1)], autonomy="Suggest"
		)

		found = self.found(RESTRICTED_USER, agent.name)

		self.assertEqual({row["doctype"] for row in found["doctypes"]}, {TICKET_DT})

	def test_find_doctypes_says_what_may_be_done_with_each(self):
		agent = make_matrix_agent([rule(ORDER_DT, can_read=1, can_create_draft=1)])

		found = self.found(DRAFT_USER, agent.name)

		self.assertEqual(found["doctypes"][0]["doctype"], ORDER_DT)
		self.assertEqual(found["doctypes"][0]["allowed"], ["read", "create_draft"])

	def test_get_doctype_meta_answers_only_granted_targets(self):
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")

		granted, _ = call_tool(RESTRICTED_USER, "get_doctype_meta", {"doctype": TICKET_DT}, agent=agent.name)
		self.assertTrue(granted["ok"], granted["error"])

		refused, _ = call_tool(RESTRICTED_USER, "get_doctype_meta", {"doctype": PROJECT_DT}, agent=agent.name)
		self.assertFalse(refused["ok"])
		self.assertIn("no access rule", refused["error"])


class TestFileReading(AgentTestCase):
	def note(self) -> str:
		return make_attachment(ORDER_DT, ORDER_LIVE, "fa-access-note.txt")

	def test_read_document_rides_the_flag_and_not_the_selection(self):
		"""Selecting the tool grants nothing once an agent is on the matrix."""
		agent = make_matrix_agent(
			[rule(ORDER_DT, can_read=1)],
			autonomy="Suggest",
			may_read_files=0,
			tools=["read_document"],
		)

		self.assertNotIn("read_document", exposed_tool_names(agent))

		payload, _ = call_tool(DRAFT_USER, "read_document", {"file": self.note()}, agent=agent.name)
		self.assertFalse(payload["ok"])

	def test_the_flag_alone_is_what_grants_it(self):
		agent = make_matrix_agent([rule(ORDER_DT, can_read=1)], autonomy="Suggest", may_read_files=1)

		self.assertIn("read_document", exposed_tool_names(agent))

		payload, _ = call_tool(DRAFT_USER, "read_document", {"file": self.note()}, agent=agent.name)
		self.assertTrue(payload["ok"], payload["error"])

	def test_the_handler_refuses_without_the_flag_too(self):
		"""Exposure is the first gate, not the only one: the handler asks again."""
		agent = make_matrix_agent([rule(ORDER_DT, can_read=1)], autonomy="Suggest", may_read_files=0)
		run = make_run(effective_user=DRAFT_USER, agent=agent.name)
		file_name = self.note()

		previous = frappe.flags.get(RUN_FLAG)
		frappe.flags[RUN_FLAG] = run
		try:
			with as_user(DRAFT_USER):
				self.assertRaises(ToolDenied, read_document, {"file": file_name})
		finally:
			frappe.flags[RUN_FLAG] = previous
