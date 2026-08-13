# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.model.document import Document

from frappe_agents.model_profiles import check_profile


class AgentConversation(Document):
	def validate(self) -> None:
		self.check_model_profile()

	def check_model_profile(self) -> None:
		"""A conversation may only be pinned to a model its agent offers.

		The check lives on the document rather than beside the chat endpoint
		because the field is writable by its owner: `frappe.client.set_value` is a
		door too, and a wall with one gate guarded is a fence.
		"""
		if not self.model_profile:
			return
		agent = frappe.get_cached_doc("Agent", self.agent)
		self.model_profile = check_profile(agent, self.model_profile)
