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

from frappe_agents.access.grants import exposed_tool_names, in_legacy_mode
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
	TEST_REPORT,
	TICKET_DT,
	TOOL_NAMES,
	VAULT_DT,
	VENDOR_ACME,
	VENDOR_DT,
	AgentTestCase,
	as_user,
	call_tool,
	make_access_profile,
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
REPORT = TEST_REPORT
OTHER_REPORT = "FA Test Project Register"


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

	def test_an_attached_profile_that_grants_nothing_is_still_the_matrix(self):
		"""The shim reads the record, not the compiled grant.

		A manager who attaches a profile has moved this agent to the matrix. If
		emptying that profile dropped it back onto its old selection, a narrowing
		would have widened the agent to everything its user can reach.
		"""
		profile = make_access_profile([rule(TICKET_DT, can_read=1)])
		agent = make_matrix_agent([], profiles=[profile.name], autonomy="Suggest", tools=list(TOOL_NAMES))

		profile.set("rules", [])
		profile.flags.ignore_permissions = True
		profile.save(ignore_permissions=True)
		frappe.clear_document_cache("Agent Access Profile", profile.name)
		frappe.clear_document_cache("Agent", agent.name)
		agent = frappe.get_cached_doc("Agent", agent.name)

		self.assertFalse(in_legacy_mode(agent))
		self.assertEqual(exposed_tool_names(agent), set())

		payload, _ = call_tool(RESTRICTED_USER, "search_documents", {"doctype": TICKET_DT}, agent=agent.name)
		self.assertFalse(payload["ok"])

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


class TestRunningReports(AgentTestCase):
	"""run_report used to ride tool selection. A Report row is the whole grant now.

	Both halves are asserted through the tool layer, because the regression this
	guards against is a report that stopped running at all when the selection
	retired — not only one that runs when it should not.
	"""

	def setUp(self) -> None:
		super().setUp()
		# A second report, so "ungranted" can be asserted on an agent that holds
		# run_report at all — the interesting refusal is the target, not the tool.
		if not frappe.db.exists("Report", OTHER_REPORT):
			frappe.get_doc(
				{
					"doctype": "Report",
					"report_name": OTHER_REPORT,
					"ref_doctype": PROJECT_DT,
					"report_type": "Report Builder",
					"module": "Custom",
					"is_standard": "No",
				}
			).insert(ignore_permissions=True)

	def runner(self, target: str = REPORT):
		return make_matrix_agent(
			[rule(ORDER_DT, can_read=1), rule(target, target_type="Report", can_read=1)],
			autonomy="Suggest",
		)

	def test_a_granted_report_runs(self):
		payload, _ = call_tool(DRAFT_USER, "run_report", {"report_name": REPORT}, agent=self.runner().name)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(payload["result"]["report"], REPORT)

	def test_an_ungranted_report_is_refused(self):
		agent = self.runner(OTHER_REPORT)

		self.assertIn("run_report", exposed_tool_names(agent))

		payload, _ = call_tool(DRAFT_USER, "run_report", {"report_name": REPORT}, agent=agent.name)

		self.assertFalse(payload["ok"])
		self.assertIn("no access rule", payload["error"])

	def test_a_doctype_rule_alone_does_not_run_a_report(self):
		"""The read rule on the doctype the report reads is not a grant to run it."""
		agent = make_matrix_agent([rule(ORDER_DT, can_read=1)], autonomy="Suggest")

		self.assertNotIn("run_report", exposed_tool_names(agent))

		payload, _ = call_tool(DRAFT_USER, "run_report", {"report_name": REPORT}, agent=agent.name)
		self.assertFalse(payload["ok"])


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


class TestTheNeighbourhood(AgentTestCase):
	"""The context tools walk off the focal document, and every hop asks the matrix.

	A grant on the document in front of the agent is not a grant on whatever it
	points at. The user's own permissions are the other half and stay where they
	were — the cast here may read every record involved, so what is missing from
	the answer is missing because of the rules and nothing else.

	The manifest and the slice have to agree. A count in the manifest is a promise
	the slice can be asked next, and "6 of those point at this document" is already
	a fact about a doctype nobody granted.
	"""

	def order_with_a_vendor(self) -> str:
		"""One order pointing at a project and at a vendor, both readable by DRAFT_USER."""
		order = make_order_draft(user=DRAFT_USER)
		with as_user(DRAFT_USER):
			order.vendor = VENDOR_ACME
			order.save()
		return order.name

	def links(self, agent: str, doctype: str, name: str, direction: str) -> dict:
		result, _ = call_tool(
			DRAFT_USER,
			"get_document_slice",
			{"doctype": doctype, "name": name, "slice": "links", "direction": direction},
			agent=agent,
		)
		self.assertTrue(result["ok"], result["error"])
		return result["result"]

	def doctypes_in(self, payload: dict, direction: str) -> set[str]:
		return {group["doctype"] for group in payload[direction]}

	def test_a_link_to_an_ungranted_doctype_is_absent(self):
		"""Not redacted and not counted: the answer does not know that vendor exists."""
		name = self.order_with_a_vendor()
		agent = make_matrix_agent(
			[rule(ORDER_DT, can_read=1), rule(PROJECT_DT, can_read=1)], autonomy="Suggest"
		)

		with as_user(DRAFT_USER):
			self.assertTrue(frappe.has_permission(VENDOR_DT, "read"))

		payload = self.links(agent.name, ORDER_DT, name, "up")

		self.assertEqual(self.doctypes_in(payload, "up"), {PROJECT_DT})
		self.assertNotIn(VENDOR_DT, payload["not_visible"]["doctypes"])
		self.assertEqual(payload["not_visible"]["count"], 0)
		self.assertNotIn(VENDOR_ACME, frappe.as_json(payload))

	def test_the_same_link_appears_once_a_rule_names_it(self):
		"""The positive control: the matrix dropped it, not the traversal."""
		name = self.order_with_a_vendor()
		agent = make_matrix_agent(
			[rule(ORDER_DT, can_read=1), rule(PROJECT_DT, can_read=1), rule(VENDOR_DT, can_read=1)],
			autonomy="Suggest",
		)

		payload = self.links(agent.name, ORDER_DT, name, "up")

		self.assertEqual(self.doctypes_in(payload, "up"), {PROJECT_DT, VENDOR_DT})
		vendors = next(group for group in payload["up"] if group["doctype"] == VENDOR_DT)
		self.assertEqual([doc["name"] for doc in vendors["docs"]], [VENDOR_ACME])

	def test_the_walk_downwards_asks_the_matrix_too(self):
		"""Documents pointing at this one are still documents of another doctype."""
		granted = make_matrix_agent(
			[rule(PROJECT_DT, can_read=1), rule(ORDER_DT, can_read=1)], autonomy="Suggest"
		)

		payload = self.links(granted.name, PROJECT_DT, PROJECT_ALPHA, "down")
		self.assertIn(ORDER_DT, self.doctypes_in(payload, "down"))
		self.assertNotIn(TICKET_DT, self.doctypes_in(payload, "down"))

		wider = make_matrix_agent(
			[rule(PROJECT_DT, can_read=1), rule(ORDER_DT, can_read=1), rule(TICKET_DT, can_read=1)],
			autonomy="Suggest",
		)

		payload = self.links(wider.name, PROJECT_DT, PROJECT_ALPHA, "down")
		self.assertIn(TICKET_DT, self.doctypes_in(payload, "down"))

	def manifest(self, agent: str, doctype: str, name: str) -> dict:
		result, _ = call_tool(
			DRAFT_USER, "get_document_context", {"doctype": doctype, "name": name}, agent=agent
		)
		self.assertTrue(result["ok"], result["error"])
		return result["result"]

	def test_the_manifest_does_not_count_an_ungranted_neighbour(self):
		"""A count is a fact about the site too, so it goes the way the group went."""
		agent = make_matrix_agent(
			[rule(PROJECT_DT, can_read=1), rule(ORDER_DT, can_read=1)], autonomy="Suggest"
		)

		with as_user(DRAFT_USER):
			self.assertTrue(frappe.has_permission(TICKET_DT, "read"))

		manifest = self.manifest(agent.name, PROJECT_DT, PROJECT_ALPHA)

		self.assertEqual(set(manifest["slices"]["links"]), {ORDER_DT})
		self.assertNotIn(TICKET_DT, manifest["not_visible"]["doctypes"])
		self.assertEqual(manifest["not_visible"]["count"], 0)

	def test_the_manifest_counts_it_once_a_rule_names_it(self):
		"""The positive control, and the agreement: counted here, openable there."""
		agent = make_matrix_agent(
			[rule(PROJECT_DT, can_read=1), rule(ORDER_DT, can_read=1), rule(TICKET_DT, can_read=1)],
			autonomy="Suggest",
		)

		manifest = self.manifest(agent.name, PROJECT_DT, PROJECT_ALPHA)

		self.assertEqual(set(manifest["slices"]["links"]), {ORDER_DT, TICKET_DT})
		self.assertEqual(manifest["slices"]["links"][TICKET_DT]["visible_count"], 1)

		payload = self.links(agent.name, PROJECT_DT, PROJECT_ALPHA, "down")
		self.assertEqual(self.doctypes_in(payload, "down"), set(manifest["slices"]["links"]))


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
