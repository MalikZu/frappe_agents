# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""A reusable set of access rules, the way a Role Profile is a set of roles.

A profile has no enabled flag on purpose: it is inert until an agent attaches
it. Writing one grants nothing to anybody.
"""

from frappe.model.document import Document

from frappe_agents.frappe_agents.doctype.agent_access_rule.agent_access_rule import validate_rules


class AgentAccessProfile(Document):
	def validate(self) -> None:
		validate_rules(self, "rules")
