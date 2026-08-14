# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The Agent form's effective-access preview: does it tell the truth.

The preview exists to answer "why can't my agent see X", so the two things it
must never get wrong are what the matrix grants and whether the person reading
the form could use that grant themselves. The second one is the interesting
half: a rule is an intersection with the reader's own frappe permissions, so the
same agent honestly reads differently to two managers, and a row the reader
cannot use comes back flagged rather than quietly listed as access.

It is a configuration answer, not a chat one, so a non-manager is refused
outright — an Agent User may read the Agent record without being handed a map of
what the site's managers have wired up.
"""

import frappe

from frappe_agents.api import effective_access
from frappe_agents.tests.fixtures import (
	ORDER_DT,
	PROJECT_DT,
	RESTRICTED_USER,
	SKILL_AUTHOR,
	TICKET_DT,
	VAULT_DT,
	AgentTestCase,
	as_user,
	make_access_profile,
	make_matrix_agent,
	rule,
)

REPORT = "Agent Action Review Quality"


class PreviewCase(AgentTestCase):
	def preview(self, agent, user: str = "Administrator") -> dict:
		with as_user(user):
			return effective_access(agent.name)

	def row(self, access: dict, target: str) -> dict:
		rows = [row for row in access["rows"] if row["target"] == target]
		self.assertEqual(len(rows), 1, f"{target} appears {len(rows)} times")
		return rows[0]

	def verbs(self, access: dict, target: str) -> set[str]:
		return {verb["verb"] for verb in self.row(access, target)["verbs"]}


class TestWhatThePreviewSays(PreviewCase):
	def test_it_lists_every_granted_target_with_its_verbs(self):
		agent = make_matrix_agent(
			[
				rule(PROJECT_DT, can_read=1),
				rule(ORDER_DT, can_read=1, can_create_draft=1, max_rows_per_call=5),
				rule(REPORT, target_type="Report", can_read=1),
			]
		)

		access = self.preview(agent)

		self.assertEqual({row["target"] for row in access["rows"]}, {PROJECT_DT, ORDER_DT, REPORT})
		self.assertEqual(self.verbs(access, PROJECT_DT), {"read"})
		self.assertEqual(self.verbs(access, ORDER_DT), {"read", "create_draft"})
		self.assertEqual(self.row(access, ORDER_DT)["max_rows_per_call"], 5)
		self.assertEqual(self.row(access, REPORT)["target_type"], "Report")

	def test_it_names_where_each_grant_came_from(self):
		"""The compiled grant forgets the profile. A person asking why must not."""
		profile = make_access_profile([rule(PROJECT_DT, can_read=1)])
		agent = make_matrix_agent([rule(TICKET_DT, can_read=1)], profiles=[profile.name])

		access = self.preview(agent)

		self.assertEqual(self.row(access, PROJECT_DT)["sources"], [profile.name])
		self.assertEqual(self.row(access, TICKET_DT)["sources"], ["This agent"])

	def test_it_carries_the_tools_the_matrix_offers(self):
		agent = make_matrix_agent([rule(PROJECT_DT, can_read=1)], may_read_files=1)

		access = self.preview(agent)

		self.assertIn("search_documents", access["tools"])
		self.assertIn("read_document", access["tools"])
		self.assertNotIn("create_draft", access["tools"])
		self.assertEqual(access["may_read_files"], 1)

	def test_an_empty_matrix_previews_as_nothing(self):
		agent = make_matrix_agent([])

		access = self.preview(agent)

		self.assertEqual(access["rows"], [])
		self.assertEqual(access["tools"], [])
		self.assertFalse(access["legacy"])


class TestWhatTheUserCannotUse(PreviewCase):
	"""The rules narrow, so a rule the reader cannot reach grants them nothing."""

	def test_a_doctype_the_reader_cannot_read_is_flagged(self):
		agent = make_matrix_agent([rule(VAULT_DT, can_read=1), rule(PROJECT_DT, can_read=1)])

		access = self.preview(agent, user=SKILL_AUTHOR)

		self.assertTrue(self.row(access, VAULT_DT)["nullified_for_user"])
		self.assertFalse(self.row(access, PROJECT_DT)["nullified_for_user"])

	def test_the_flag_is_answered_verb_by_verb(self):
		"""This reader may read orders and may not create them. Both, on one row."""
		agent = make_matrix_agent([rule(ORDER_DT, can_read=1, can_create_draft=1)])

		access = self.preview(agent, user=SKILL_AUTHOR)
		flags = {verb["verb"]: verb["nullified_for_user"] for verb in self.row(access, ORDER_DT)["verbs"]}

		self.assertEqual(flags, {"read": False, "create_draft": True})
		self.assertFalse(self.row(access, ORDER_DT)["nullified_for_user"])

	def test_the_same_agent_reads_differently_to_a_manager_who_may(self):
		agent = make_matrix_agent([rule(VAULT_DT, can_read=1)])

		self.assertFalse(self.row(self.preview(agent), VAULT_DT)["nullified_for_user"])


class TestWhoMayAsk(PreviewCase):
	def test_a_non_manager_is_refused(self):
		agent = make_matrix_agent([rule(PROJECT_DT, can_read=1)])

		with as_user(RESTRICTED_USER), self.assertRaises(frappe.PermissionError):
			effective_access(agent.name)

	def test_a_manager_is_answered(self):
		agent = make_matrix_agent([rule(PROJECT_DT, can_read=1)])

		access = self.preview(agent, user=SKILL_AUTHOR)

		self.assertEqual(access["agent"], agent.name)
		self.assertEqual(access["user"], SKILL_AUTHOR)
