# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Skills, and the approval that turns text into instructions.

A skill is a written instruction someone signed off. Until that signature exists
the text is a draft, and a draft never reaches the model — which is the whole
point of the status. The signature covers one specific body, so changing the body
spends it, and it has to come from someone other than the author.

The assertion is always the built system prompt: that is the only place the
distinction between an approved skill and a draft can be observed.

Two layers refuse an approval, and a test that cannot tell them apart proves
nothing about either: the framework refuses a user who may not write the skill at
all, and the controller refuses a user who may write it but holds no Agent
Manager role. Each has its own user.
"""

import frappe

from frappe_agents.frappe_agents.doctype.agent_skill.agent_skill import APPROVER_ROLE
from frappe_agents.runner.run import SKILLS_HEADING, build_system_prompt
from frappe_agents.tests.fixtures import (
	OPEN_USER,
	ORDER_DT,
	ORDER_LIVE,
	PROFILE,
	RESTRICTED_USER,
	SKILL_APPROVER,
	SKILL_AUTHOR,
	SKILL_WRITER,
	TICKET_ALPHA,
	TICKET_DT,
	AgentTestCase,
	as_user,
	make_run,
)

DRAFT_BODY = "When a ticket stalls, escalate it to the duty manager the same day."
APPROVED_BODY = "Quote the order total from the order, never from the quotation."
REWRITTEN_BODY = "Quote the order total from the invoice instead."
SCOPED_BODY = "Tickets are closed only after the customer confirms."


class TestAgentSkills(AgentTestCase):
	def make_skill(self, body: str, doctypes: list[str] | None = None, user: str = SKILL_AUTHOR) -> str:
		with as_user(user):
			skill = frappe.get_doc(
				{
					"doctype": "Agent Skill",
					"skill_title": f"FA Skill {frappe.generate_hash(length=6)}",
					"body": body,
					"applies_to_doctypes": [{"document_type": doctype} for doctype in doctypes or []],
				}
			)
			skill.insert()
		return skill.name

	def set_status(self, name: str, status: str, user: str = SKILL_APPROVER) -> None:
		with as_user(user):
			skill = frappe.get_doc("Agent Skill", name)
			skill.status = status
			skill.save()
		frappe.clear_document_cache("Agent Skill", name)

	def rewrite_body(self, name: str, body: str, user: str = SKILL_AUTHOR) -> None:
		with as_user(user):
			skill = frappe.get_doc("Agent Skill", name)
			skill.body = body
			skill.save()
		frappe.clear_document_cache("Agent Skill", name)

	def make_agent(self, *skills: str):
		agent = frappe.get_doc(
			{
				"doctype": "Agent",
				"agent_name": f"FA Skilled Agent {frappe.generate_hash(length=6)}",
				"enabled": 1,
				"run_as": "Session User",
				"model_profile": PROFILE,
				"autonomy": "Suggest",
				"instructions": "Answer questions about orders.",
				"max_steps": 3,
				"skills": [{"skill": skill} for skill in skills],
			}
		)
		agent.flags.ignore_permissions = True
		agent.insert(ignore_permissions=True)
		return agent

	def prompt_for(self, agent, context_doctype: str | None = None, context_name: str | None = None) -> str:
		run = make_run(
			effective_user=RESTRICTED_USER,
			agent=agent.name,
			context_doctype=context_doctype,
			context_name=context_name,
		)
		return build_system_prompt(agent, run)

	def test_a_skill_is_born_a_draft(self):
		"""Imports and API payloads do not get to declare themselves approved."""
		with as_user(SKILL_AUTHOR):
			skill = frappe.get_doc(
				{
					"doctype": "Agent Skill",
					"skill_title": f"FA Skill {frappe.generate_hash(length=6)}",
					"body": DRAFT_BODY,
					"status": "Approved",
					"approved_by": SKILL_AUTHOR,
				}
			)
			skill.insert()

		values = frappe.db.get_value("Agent Skill", skill.name, ["status", "approved_by"], as_dict=True)
		self.assertEqual(values.status, "Draft")
		self.assertIsNone(values.approved_by)

	def test_a_draft_skill_never_reaches_the_model(self):
		agent = self.make_agent(self.make_skill(DRAFT_BODY))
		prompt = self.prompt_for(agent)

		self.assertNotIn(DRAFT_BODY, prompt)
		self.assertNotIn(SKILLS_HEADING, prompt)
		self.assertIn("Answer questions about orders.", prompt)

	def test_an_approved_skill_reaches_the_model(self):
		name = self.make_skill(APPROVED_BODY)
		self.set_status(name, "Approved")

		agent = self.make_agent(name)
		prompt = self.prompt_for(agent)

		self.assertIn(SKILLS_HEADING, prompt)
		self.assertIn(APPROVED_BODY, prompt)
		self.assertIn(name, prompt)
		self.assertEqual(frappe.db.get_value("Agent Skill", name, "approved_by"), SKILL_APPROVER)

	def test_editing_an_approved_body_takes_the_skill_back_out(self):
		"""An approval covers one text. Change the text and it is spent."""
		name = self.make_skill(APPROVED_BODY)
		self.set_status(name, "Approved")
		agent = self.make_agent(name)
		self.assertIn(APPROVED_BODY, self.prompt_for(agent))

		self.rewrite_body(name, REWRITTEN_BODY)

		values = frappe.db.get_value("Agent Skill", name, ["status", "approved_by"], as_dict=True)
		self.assertEqual(values.status, "Draft")
		self.assertIsNone(values.approved_by)

		prompt = self.prompt_for(agent)
		self.assertNotIn(REWRITTEN_BODY, prompt)
		self.assertNotIn(APPROVED_BODY, prompt)
		self.assertNotIn(SKILLS_HEADING, prompt)

	def test_a_retired_skill_stops_reaching_the_model(self):
		name = self.make_skill(APPROVED_BODY)
		self.set_status(name, "Approved")
		agent = self.make_agent(name)

		self.set_status(name, "Retired")

		self.assertNotIn(APPROVED_BODY, self.prompt_for(agent))

	def test_a_scoped_skill_waits_for_its_doctype(self):
		name = self.make_skill(SCOPED_BODY, doctypes=[TICKET_DT])
		self.set_status(name, "Approved")
		agent = self.make_agent(name)

		self.assertIn(SCOPED_BODY, self.prompt_for(agent, TICKET_DT, TICKET_ALPHA))
		self.assertNotIn(SCOPED_BODY, self.prompt_for(agent, ORDER_DT, ORDER_LIVE))
		self.assertNotIn(SCOPED_BODY, self.prompt_for(agent))

	def test_an_unscoped_skill_applies_everywhere(self):
		"""The positive control for scoping: no scope rows means every doctype."""
		name = self.make_skill(APPROVED_BODY)
		self.set_status(name, "Approved")
		agent = self.make_agent(name)

		self.assertIn(APPROVED_BODY, self.prompt_for(agent, ORDER_DT, ORDER_LIVE))
		self.assertIn(APPROVED_BODY, self.prompt_for(agent))

	def test_an_author_cannot_approve_their_own_skill(self):
		"""Separation of duties: the author holds the role and still cannot sign it off."""
		name = self.make_skill(APPROVED_BODY)

		with self.assertRaises(frappe.PermissionError):
			self.set_status(name, "Approved", user=SKILL_AUTHOR)

		frappe.clear_document_cache("Agent Skill", name)
		self.assertEqual(frappe.db.get_value("Agent Skill", name, "status"), "Draft")

	def test_a_user_who_may_write_a_skill_still_cannot_approve_it(self):
		"""The controller's own check.

		This user may save the skill — the save below proves it — so the denial on the
		second save can only come from the role check in validate_approval, not from
		the framework refusing the write.
		"""
		name = self.make_skill(APPROVED_BODY)

		with as_user(SKILL_WRITER):
			self.assertNotIn(APPROVER_ROLE, frappe.get_roles(SKILL_WRITER))

			skill = frappe.get_doc("Agent Skill", name)
			skill.notes = "A note from someone who is allowed to write this skill."
			skill.save()

			skill.status = "Approved"
			with self.assertRaises(frappe.PermissionError) as raised:
				skill.save()

		self.assertIn(APPROVER_ROLE, str(raised.exception))
		frappe.clear_document_cache("Agent Skill", name)
		values = frappe.db.get_value("Agent Skill", name, ["status", "approved_by"], as_dict=True)
		self.assertEqual(values.status, "Draft")
		self.assertIsNone(values.approved_by)

	def test_a_user_without_write_access_cannot_touch_a_skill(self):
		"""The outer layer: a reader is stopped by the framework, before any controller runs."""
		name = self.make_skill(APPROVED_BODY)

		with self.assertRaises(frappe.PermissionError):
			self.set_status(name, "Approved", user=OPEN_USER)

		frappe.clear_document_cache("Agent Skill", name)
		self.assertEqual(frappe.db.get_value("Agent Skill", name, "status"), "Draft")

	def test_the_focal_document_is_named_in_the_prompt(self):
		agent = self.make_agent()
		prompt = self.prompt_for(agent, ORDER_DT, ORDER_LIVE)

		self.assertIn(f"{ORDER_DT} {ORDER_LIVE}", prompt)
		self.assertIn("get_document_context", prompt)
