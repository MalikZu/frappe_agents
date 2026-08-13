# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What the chat surface is told an agent can do.

Two lists ride with every agent. The tools are what the executor would actually
run — granted, enabled, and inside the agent's autonomy — because a tool the
capability gate refuses is not something the agent can do, and naming it would
promise the user an outcome that never arrives.

The models are the agent owner's list narrowed to this user's roles. Both halves
of that wall are the point: an agent lists what it may run, a profile lists who
may run it, and the picker offers the intersection.
"""

from frappe_agents.api import list_agents
from frappe_agents.tests.fixtures import (
	AGENT,
	ALT_PROFILE,
	DRAFT_AGENT,
	DRAFT_TOOL_NAMES,
	GATED_PROFILE,
	OPEN_USER,
	PROFILE,
	READ_TOOL_NAMES,
	RESTRICTED_USER,
	AgentTestCase,
	as_user,
)


class TestAgentCapabilities(AgentTestCase):
	def agent(self, user: str, name: str) -> dict:
		with as_user(user):
			for row in list_agents():
				if row["name"] == name:
					return row
		raise AssertionError(f"{name} is not listed for {user}")

	def test_a_suggest_agent_lists_only_the_tools_it_could_call(self):
		"""It is granted every tool. Autonomy is what decides, and it decides here too."""
		tools = self.agent(RESTRICTED_USER, AGENT)["tools"]

		names = {tool["tool_name"] for tool in tools}
		self.assertEqual(names, set(READ_TOOL_NAMES))
		self.assertFalse(names & set(DRAFT_TOOL_NAMES))
		self.assertTrue(all(tool["capability"] == "Read" for tool in tools))

	def test_a_draft_agent_lists_its_draft_tools_with_their_capability(self):
		tools = {tool["tool_name"]: tool for tool in self.agent(RESTRICTED_USER, DRAFT_AGENT)["tools"]}

		self.assertEqual(set(tools), set(READ_TOOL_NAMES) | set(DRAFT_TOOL_NAMES))
		self.assertEqual(tools["propose_submit"]["capability"], "Draft")
		self.assertEqual(tools["search_documents"]["capability"], "Read")

	def test_every_tool_carries_one_line_of_description(self):
		tools = self.agent(RESTRICTED_USER, AGENT)["tools"]

		for tool in tools:
			self.assertTrue(tool["description"], f"{tool['tool_name']} has no description")
			self.assertNotIn("\n", tool["description"])

	def test_the_models_offered_are_the_ones_this_user_may_run(self):
		mine = self.agent(RESTRICTED_USER, AGENT)
		theirs = self.agent(OPEN_USER, AGENT)

		self.assertEqual([choice["name"] for choice in mine["model_choices"]], [PROFILE, ALT_PROFILE])
		self.assertEqual(
			[choice["name"] for choice in theirs["model_choices"]],
			[PROFILE, ALT_PROFILE, GATED_PROFILE],
		)

	def test_the_agents_own_profile_is_the_default_choice(self):
		row = self.agent(RESTRICTED_USER, AGENT)

		self.assertEqual(row["model_profile"], PROFILE)
		self.assertEqual(row["model_choices"][0]["default"], 1)
		self.assertEqual(row["model_choices"][0]["name"], PROFILE)
		self.assertTrue(row["model_choices"][0]["model_id"])
		self.assertEqual([choice["default"] for choice in row["model_choices"][1:]], [0])

	def test_an_agent_with_no_alternates_offers_only_its_own(self):
		row = self.agent(RESTRICTED_USER, DRAFT_AGENT)

		self.assertEqual([choice["name"] for choice in row["model_choices"]], [PROFILE])
