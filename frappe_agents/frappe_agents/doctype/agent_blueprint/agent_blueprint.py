# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What the Agents Builder produces: a proposal for an agent, in plain data.

A blueprint governs nothing. It holds a purpose, a suggested model and the rules
an agent would need, and it sits there until a human with Agent Manager presses
Create Agent — which is why this is the one doctype in the app an agent may
write. The materialisation is the human's act, and it is recorded as one.
"""

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_agents.frappe_agents.doctype.agent_access_rule.agent_access_rule import validate_rules

STATUS_DRAFT = "Draft"
STATUS_APPLIED = "Applied"

MANAGER_ROLE = "Agent Manager"


class AgentBlueprint(Document):
	def validate(self) -> None:
		validate_rules(self, "suggested_rules")

	@frappe.whitelist()
	def create_agent(self) -> dict:
		"""Materialise this blueprint into a disabled Agent and its access profile.

		Only a human ever reaches this: the check below is on the role, not on the
		record, because the question is who is pressing the button.
		"""
		self.check_permission("write")
		if MANAGER_ROLE not in frappe.get_roles():
			frappe.throw(_("Only an Agent Manager can turn a blueprint into an agent."))

		raise NotImplementedError("Blueprint materialisation lands with the Agents Builder.")
