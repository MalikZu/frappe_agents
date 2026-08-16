# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The Agents Builder: what it may see, what it may write, and who applies it.

The Builder is the one agent that talks about the whole site, so the tests here
are mostly about the line it does not cross. It reads names — doctypes, reports,
fieldnames — and never a document; the site's exclusions apply to what it may
name; the session user's own permissions apply on top; and fields above
permlevel 0 are named but never described.

One table sits deliberately on both sides of that line. The blueprint's own
suggested-rules table is described, because a blueprint cannot be written
without its fieldnames — and is still not listed, not grantable and not
readable, because describing a shape is not reaching a record.

That hole is asserted as a *route*, not as a destination. The Builder is told to
describe the blueprint and then the table its Suggested Rules names, so the test
that matters walks it with nothing written down: opening the destination while
the blueprint's answer still did not carry the name left the fix half-landed and
the Builder still guessing.

The other half is the blueprint. A blueprint is paper: drafting one grants
nobody anything, and turning it into an agent is a human act by an Agent
Manager. What that act produces is asserted here too — a *disabled* agent, a
profile carrying exactly the suggested rules, and a blueprint marked Applied so
the next reader can see the proposal already became something.
"""

import frappe

from frappe_agents.access.builder import (
	BUILDER_AGENT,
	BUILDER_INSTRUCTIONS,
	BUILDER_PROFILE,
	seed_agents_builder,
)
from frappe_agents.access.exclusions import BLUEPRINT, describable_child_tables
from frappe_agents.access.grants import BUILDER_TOOLS, compiled_grants, exposed_tool_names
from frappe_agents.frappe_agents.doctype.agent_blueprint.agent_blueprint import (
	STATUS_APPLIED,
	STATUS_DRAFT,
)
from frappe_agents.tests.fixtures import (
	OPEN_USER,
	ORDER_DT,
	ORDER_ITEM_DT,
	PROJECT_DT,
	RESTRICTED_USER,
	SKILL_AUTHOR,
	SKILL_WRITER,
	TEST_REPORT,
	TICKET_DT,
	VAULT_DT,
	AgentTestCase,
	as_user,
	call_tool,
	make_matrix_agent,
	make_run,
	rule,
	tool_calls_for,
)
from frappe_agents.tools.base import RUN_FLAG, ToolDenied
from frappe_agents.tools.builder_tools import NO_SUCH_DOCTYPE, list_site_doctypes

BUILDER_RULE = rule(BLUEPRINT, can_read=1, can_create_draft=1, can_update_draft=1)

APP_REPORT = "Agent Action Review Quality"
SITE_REPORT = TEST_REPORT

# The table a blueprint's suggested rules live in, and the fields a blueprint is
# written out of. The Builder guessed at these once and could not do otherwise.
RULE_DT = "Agent Access Rule"
RULE_FIELDS = {
	"target_type",
	"target",
	"can_read",
	"can_create_draft",
	"can_update_draft",
	"can_propose",
	"can_extract",
	"update_any_draft",
	"max_rows_per_call",
}
# Child tables of ours that the blueprint does not name. An agent's own tool
# selection and role gating are exactly what it may not be shown the shape of.
OTHER_CHILD_TABLES = ("Agent Selected Tool", "Agent Allowed Role", "Agent Access Profile Link")

SECRET_FIELD = "secret_note"


class BuilderCase(AgentTestCase):
	"""A builder agent, and one call through the tool layer as a given user."""

	def builder(self, rules: list | None = None, **kwargs):
		return make_matrix_agent([BUILDER_RULE] if rules is None else rules, **kwargs)

	def ask(self, tool: str, payload: dict | None = None, user: str = RESTRICTED_USER, agent=None) -> dict:
		agent = agent if agent is not None else self.builder()
		result, _ = call_tool(user, tool, payload or {}, agent=agent.name)
		self.assertTrue(result["ok"], result["error"])
		return result["result"]

	def refusal(self, tool: str, payload: dict, user: str = RESTRICTED_USER, agent=None) -> str:
		agent = agent if agent is not None else self.builder()
		result, _ = call_tool(user, tool, payload, agent=agent.name)
		self.assertFalse(result["ok"], result["result"])
		return result["error"]

	def described(self, doctype: str, user: str = RESTRICTED_USER) -> dict:
		return self.ask("describe_site_doctype", {"doctype": doctype}, user=user)


class TestBuilderToolExposure(BuilderCase):
	def test_the_builder_tools_ride_the_blueprint_grant(self):
		"""Not a selection and not a wildcard: may it draft a blueprint, yes or no."""
		reader = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")
		self.assertFalse(BUILDER_TOOLS & exposed_tool_names(reader))

		self.assertTrue(BUILDER_TOOLS <= exposed_tool_names(self.builder()))

	def test_reading_a_blueprint_is_not_drafting_one(self):
		agent = make_matrix_agent([rule(BLUEPRINT, can_read=1)], autonomy="Suggest")

		self.assertFalse(BUILDER_TOOLS & exposed_tool_names(agent))

	def test_the_handler_refuses_without_the_grant_too(self):
		"""Exposure is the first gate, not the only one: the handler asks again."""
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], autonomy="Suggest")
		run = make_run(effective_user=RESTRICTED_USER, agent=agent.name)

		previous = frappe.flags.get(RUN_FLAG)
		frappe.flags[RUN_FLAG] = run
		try:
			with as_user(RESTRICTED_USER), self.assertRaises(ToolDenied) as caught:
				list_site_doctypes({})
		finally:
			frappe.flags[RUN_FLAG] = previous

		self.assertIn("designing agents", str(caught.exception))

	def test_the_seeded_profile_is_what_grants_it(self):
		agent = make_matrix_agent([], profiles=[BUILDER_PROFILE])

		self.assertTrue(BUILDER_TOOLS <= exposed_tool_names(agent))


class TestListingTheSite(BuilderCase):
	def listed(self, **payload) -> list[str]:
		result = self.ask("list_site_doctypes", payload)
		return [row["doctype"] for row in result["doctypes"]]

	def test_it_lists_what_this_user_may_read(self):
		listed = self.listed(limit=100)

		self.assertIn(TICKET_DT, listed)
		self.assertIn(PROJECT_DT, listed)
		self.assertNotIn(VAULT_DT, listed)

	def test_an_excluded_doctype_is_never_named(self):
		listed = self.listed(query="agent", limit=100)

		self.assertNotIn("Agent", listed)
		self.assertNotIn("Agent Run", listed)
		self.assertNotIn("User", listed)

	def test_the_blueprint_is_the_one_thing_of_ours_it_can_name(self):
		"""Deliberate hole: the Builder has to be able to talk about its own output."""
		self.assertIn(BLUEPRINT, self.listed(query="blueprint"))

	def test_child_tables_are_left_out(self):
		"""Rows are granted through the parent, so a child table is not a target."""
		self.assertNotIn(ORDER_ITEM_DT, self.listed(query="fa test", limit=100))

	def test_the_table_it_may_describe_is_still_not_on_the_list(self):
		"""The list is targets a rule could name. Describing one did not add it.

		The two questions stay apart: the Builder reads this table's fields so it
		can fill them in, and the list stays what it always was — the doctypes a
		suggested rule may point at.
		"""
		self.assertNotIn(RULE_DT, self.listed(query="agent", limit=100))

	def test_a_query_narrows_and_the_page_says_how_far_it_got(self):
		everything = self.ask("list_site_doctypes", {"limit": 100})
		narrowed = self.ask("list_site_doctypes", {"query": "fa test", "limit": 100})

		self.assertLess(narrowed["total"], everything["total"])
		self.assertTrue(all(row["doctype"].startswith("FA Test") for row in narrowed["doctypes"]))

	def test_it_pages(self):
		first = self.ask("list_site_doctypes", {"limit": 1})
		second = self.ask("list_site_doctypes", {"limit": 1, "start": 1})

		self.assertEqual(first["count"], 1)
		self.assertTrue(first["truncated"])
		self.assertNotEqual(first["doctypes"][0]["doctype"], second["doctypes"][0]["doctype"])

	def test_it_answers_with_shape_and_never_with_documents(self):
		result = self.ask("list_site_doctypes", {"query": "fa test ticket"})

		self.assertEqual(
			set(result["doctypes"][0]), {"doctype", "module", "description", "is_submittable", "is_single"}
		)


class TestDescribingADoctype(BuilderCase):
	def test_it_describes_the_fields_a_blueprint_would_name(self):
		described = self.described(TICKET_DT)

		self.assertEqual({field["fieldname"] for field in described["fields"]}, {"subject", "project"})
		self.assertEqual(described["module"], "Custom")

	def test_fields_above_permlevel_zero_are_named_and_not_described(self):
		"""True for every user: the matrix has no way to grant them anyway."""
		for user in (RESTRICTED_USER, OPEN_USER):
			described = self.described(TICKET_DT, user=user)
			self.assertNotIn(SECRET_FIELD, [field["fieldname"] for field in described["fields"]])
			self.assertEqual(described["restricted_fields"], [SECRET_FIELD])

	def test_it_describes_the_table_its_own_suggested_rules_live_in(self):
		"""Without this the Builder cannot write a blueprint at all.

		A blueprint's suggested rules are rows of this table. The Builder used to
		have to invent the fieldnames, and `create_draft` refused every guess.

		This asserts the destination only. It says nothing about whether the
		Builder can get here from the blueprint — the name is written down at the
		top of this file — so the walk is asserted separately below.
		"""
		described = self.described(RULE_DT)

		self.assertLessEqual(RULE_FIELDS, {field["fieldname"] for field in described["fields"]})

	def test_the_builder_walks_from_the_blueprint_to_its_rules_table(self):
		"""The path the instructions describe, with no name hard-coded on it.

		The Builder is told to describe Agent Blueprint and then "the doctype its
		Suggested Rules table names". It could not: `Table` is one of frappe's
		`no_value_fields`, and those were dropped before anything else looked at a
		field, so the blueprint described as seven fields with the table — and the
		only place that name appears — missing. Opening the destination while the
		route to it was still a dead end left the Builder inventing fieldnames,
		which is the failure this release set out to fix.

		So the child's name is taken out of the first result and never written
		here. Checking it against RULE_DT afterwards is what says the path led
		somewhere right rather than merely somewhere.
		"""
		blueprint = self.described(BLUEPRINT)

		tables = [field for field in blueprint["fields"] if field["fieldtype"] == "Table"]
		self.assertEqual(len(tables), 1, blueprint["fields"])
		# The label the instructions send it looking for, and the name it needs.
		self.assertEqual(tables[0]["label"], "Suggested Rules")
		named = tables[0]["options"]

		described = self.described(named)

		self.assertEqual(described["doctype"], named)
		self.assertLessEqual(RULE_FIELDS, {field["fieldname"] for field in described["fields"]})
		self.assertEqual(named, RULE_DT)

	def test_a_child_table_it_could_not_describe_is_not_named_either(self):
		"""Naming one is exactly as wide as describing one, and not a character wider.

		A Table field is the only place a child doctype's name surfaces — the list
		leaves child tables out — so surfacing them is what makes the walk above
		possible. Surfacing one the caller cannot then describe would make this
		tool an oracle: a name it refuses to confirm exists, printed inside the
		answer to another question.
		"""
		described = self.described(ORDER_DT)

		self.assertNotIn(ORDER_ITEM_DT, [field["options"] for field in described["fields"]])
		self.assertEqual(
			self.refusal("describe_site_doctype", {"doctype": ORDER_ITEM_DT}),
			NO_SUCH_DOCTYPE.format(doctype=ORDER_ITEM_DT),
		)

	def test_it_answers_in_the_sites_own_spelling(self):
		"""What came back names the doctype frappe resolved, not the string asked."""
		described = self.described(TICKET_DT.lower())

		self.assertEqual(described["doctype"], TICKET_DT)
		self.assertEqual({field["fieldname"] for field in described["fields"]}, {"subject", "project"})

	def test_the_hole_is_derived_from_the_blueprint_and_not_named(self):
		"""A second child table added to the blueprint is covered without an edit."""
		self.assertEqual(set(describable_child_tables()), {RULE_DT})

	def test_our_other_child_tables_are_refused_in_the_usual_words(self):
		"""Widened by the blueprint's own tables, not by 'a child table of ours'.

		An agent's selected tools and allowed roles are child tables in the same
		module. A predicate that let those through would hand the Builder the shape
		of the gating that decides what agents may do.
		"""
		agent = self.builder()

		for doctype in OTHER_CHILD_TABLES:
			error = self.refusal("describe_site_doctype", {"doctype": doctype}, agent=agent)
			self.assertEqual(error, NO_SUCH_DOCTYPE.format(doctype=doctype))

	def test_the_matrix_scoped_describe_still_refuses_it(self):
		"""Two describes, two questions, and only one of them was opened.

		`get_doctype_meta` answers for granted targets, so opening it would mean
		either opening the runtime grant check or minting a rule row the validator
		has to keep refusing. One describe path is enough, and this one stays shut.
		"""
		error = self.refusal("get_doctype_meta", {"doctype": RULE_DT})

		self.assertIn("agent framework", error)

	def test_the_three_refusals_are_the_same_refusal(self):
		"""Excluded, unreadable, or not there at all: one answer, word for word.

		A refusal that says which one it was is a probe. "No such DocType" on a
		name nobody has confirms that every name it does not say that about is a
		real doctype, and the builder could map the site by asking.
		"""
		refusals = {
			doctype: self.refusal("describe_site_doctype", {"doctype": doctype})
			for doctype in ("FA Nothing At All", "Agent", VAULT_DT)
		}

		for doctype, error in refusals.items():
			self.assertEqual(error, NO_SUCH_DOCTYPE.format(doctype=doctype))

		# The name the caller supplied is the only thing that differs between them.
		self.assertEqual(
			{error.replace(doctype, "X") for doctype, error in refusals.items()},
			{NO_SUCH_DOCTYPE.format(doctype="X")},
		)

	def test_a_refusal_is_a_denial_and_not_an_error(self):
		"""The outcome on the audit row must not tell them apart either."""
		outcomes = set()
		for doctype in ("FA Nothing At All", "Agent", VAULT_DT):
			_, run = call_tool(
				RESTRICTED_USER,
				"describe_site_doctype",
				{"doctype": doctype},
				agent=self.builder().name,
			)
			outcomes |= {call.outcome for call in tool_calls_for(run.name)}

		self.assertEqual(outcomes, {"Denied"})


class TestListingReports(BuilderCase):
	def listed(self, **payload) -> list[str]:
		result = self.ask("list_site_reports", payload)
		return [row["report"] for row in result["reports"]]

	def test_it_lists_a_report_a_rule_could_grant(self):
		listed = self.listed(query="FA Test Order")

		self.assertIn(SITE_REPORT, listed)

	def test_a_report_on_an_excluded_doctype_is_not_offered(self):
		"""Granting it would be granting a way to read what governs the agent."""
		self.assertNotIn(APP_REPORT, self.listed(limit=100))


class TestDraftingABlueprint(BuilderCase):
	def values(self, title: str) -> dict:
		return {
			"title": title,
			"purpose": "Answer questions about orders for the site office.",
			"suggested_model": "FA Test Profile",
			"suggested_rules": [
				{"target_type": "DocType", "target": ORDER_DT, "can_read": 1, "can_create_draft": 1}
			],
		}

	def draft(self, title: str = "FA Blueprint One") -> str:
		result = self.ask(
			"create_draft",
			{"doctype": BLUEPRINT, "values": self.values(title)},
			user=SKILL_AUTHOR,
		)
		return result["name"]

	def test_the_builder_drafts_a_blueprint(self):
		name = self.draft()

		blueprint = frappe.get_doc(BLUEPRINT, name)
		self.assertEqual(blueprint.status, STATUS_DRAFT)
		self.assertEqual([row.target for row in blueprint.suggested_rules], [ORDER_DT])

	def test_a_blueprint_grants_nobody_anything(self):
		"""Paper. The agent that wrote it still reaches only what its rules say."""
		self.draft()
		agent = self.builder()

		self.assertEqual(set(compiled_grants(agent)["DocType"]), {BLUEPRINT})

	def test_the_builder_revises_its_own_draft(self):
		name = self.draft()

		self.ask(
			"update_draft",
			{"doctype": BLUEPRINT, "name": name, "values": {"purpose": "Narrowed to open orders."}},
			user=SKILL_AUTHOR,
		)

		self.assertEqual(frappe.get_doc(BLUEPRINT, name).purpose, "Narrowed to open orders.")

	def test_it_cannot_write_down_that_it_created_an_agent(self):
		"""The three fields that record the human act are not the agent's to write.

		Applied, and the names of what was made, are what `create_agent` leaves
		behind. A model that could set them would be making exactly the claim the
		Builder is told never to make, and on the record rather than in a sentence.
		"""
		name = self.draft("FA Blueprint Claim")

		self.ask(
			"update_draft",
			{
				"doctype": BLUEPRINT,
				"name": name,
				"values": {
					"purpose": "Still a proposal.",
					"status": STATUS_APPLIED,
					"created_agent": BUILDER_AGENT,
				},
			},
			user=SKILL_AUTHOR,
		)

		blueprint = frappe.get_doc(BLUEPRINT, name)
		self.assertEqual(blueprint.status, STATUS_DRAFT)
		self.assertIsNone(blueprint.created_agent)
		self.assertEqual(blueprint.purpose, "Still a proposal.")

	def test_a_draft_it_writes_starts_out_unapplied(self):
		values = self.values("FA Blueprint Born Applied")
		values["status"] = STATUS_APPLIED

		result = self.ask("create_draft", {"doctype": BLUEPRINT, "values": values}, user=SKILL_AUTHOR)

		self.assertEqual(frappe.get_doc(BLUEPRINT, result["name"]).status, STATUS_DRAFT)

	def test_a_rule_the_matrix_refuses_is_refused_on_paper_too(self):
		"""The blueprint runs the same validation, so a bad row never gets written down."""
		values = self.values("FA Blueprint Excluded")
		values["suggested_rules"] = [{"target_type": "DocType", "target": "User", "can_read": 1}]

		error = self.refusal("create_draft", {"doctype": BLUEPRINT, "values": values}, user=SKILL_AUTHOR)

		self.assertIn("security configuration", error)


class TestApplyingABlueprint(AgentTestCase):
	def blueprint(self, title: str = "FA Blueprint Apply") -> str:
		doc = frappe.get_doc(
			{
				"doctype": BLUEPRINT,
				"title": title,
				"purpose": "Answer questions about orders.",
				"suggested_profiles": f"{BUILDER_PROFILE}\nFA No Such Profile",
				"suggested_rules": [
					{"target_type": "DocType", "target": ORDER_DT, "can_read": 1, "can_create_draft": 1}
				],
			}
		)
		doc.flags.ignore_permissions = True
		doc.insert(ignore_permissions=True)
		return doc.name

	def apply(self, name: str, user: str = SKILL_AUTHOR) -> dict:
		with as_user(user):
			return frappe.get_doc(BLUEPRINT, name).create_agent()

	def test_only_an_agent_manager_may_apply_one(self):
		"""Write permission is not the question. Who is pressing the button is."""
		name = self.blueprint()

		with self.assertRaises(frappe.ValidationError) as caught:
			self.apply(name, user=SKILL_WRITER)

		self.assertIn("Agent Manager", str(caught.exception))
		self.assertEqual(frappe.get_doc(BLUEPRINT, name).status, STATUS_DRAFT)

	def test_it_creates_a_disabled_agent_and_marks_the_blueprint_applied(self):
		name = self.blueprint()

		created = self.apply(name)

		agent = frappe.get_doc("Agent", created["agent"])
		self.assertFalse(agent.enabled)
		self.assertEqual(agent.autonomy, "Suggest")
		self.assertFalse(agent.model_profile)

		blueprint = frappe.get_doc(BLUEPRINT, name)
		self.assertEqual(blueprint.status, STATUS_APPLIED)
		self.assertEqual(blueprint.created_agent, agent.name)
		self.assertEqual(blueprint.created_profile, created["profile"])

	def test_the_suggested_rules_become_the_agents_profile(self):
		created = self.apply(self.blueprint())

		profile = frappe.get_doc("Agent Access Profile", created["profile"])
		self.assertEqual([row.target for row in profile.rules], [ORDER_DT])

		agent = frappe.get_doc("Agent", created["agent"])
		self.assertEqual(set(compiled_grants(agent)["DocType"]), {ORDER_DT, BLUEPRINT})

	def test_an_existing_profile_named_in_the_blueprint_is_attached(self):
		created = self.apply(self.blueprint())

		agent = frappe.get_doc("Agent", created["agent"])
		self.assertEqual(
			[row.access_profile for row in agent.access_profiles], [created["profile"], BUILDER_PROFILE]
		)

	def test_applying_it_twice_is_refused(self):
		name = self.blueprint()
		self.apply(name)

		with self.assertRaises(frappe.ValidationError) as caught:
			self.apply(name)

		self.assertIn("already applied", str(caught.exception))


class TestTheSeededBuilder(AgentTestCase):
	def agent(self):
		return frappe.get_doc("Agent", BUILDER_AGENT)

	def test_it_is_seeded_switched_off_and_without_a_model(self):
		agent = self.agent()

		self.assertFalse(agent.enabled)
		self.assertFalse(agent.model_profile)
		self.assertEqual(agent.autonomy, "Draft")

	def test_its_whole_grant_is_the_blueprint(self):
		grants = compiled_grants(self.agent())

		self.assertEqual(set(grants["DocType"]), {BLUEPRINT})
		self.assertEqual(grants["Report"], {})
		self.assertFalse(grants["DocType"][BLUEPRINT]["update_any_draft"])

	def test_it_holds_the_builder_tools_and_no_files(self):
		names = exposed_tool_names(self.agent())

		self.assertTrue(BUILDER_TOOLS <= names)
		self.assertIn("create_draft", names)
		self.assertNotIn("read_document", names)
		self.assertNotIn("run_report", names)

	def test_its_instructions_send_the_manager_to_the_button(self):
		"""The Builder drafts and points. Claiming to have created an agent is the failure mode."""
		instructions = self.agent().instructions

		self.assertEqual(instructions, BUILDER_INSTRUCTIONS)
		self.assertIn("press Create Agent", instructions)
		self.assertIn("Never say that you created an agent", instructions)

	def test_seeding_again_creates_nothing(self):
		self.assertEqual(seed_agents_builder(), [])
