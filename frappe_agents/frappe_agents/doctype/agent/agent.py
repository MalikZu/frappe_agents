# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_agents.frappe_agents.doctype.agent_access_rule.agent_access_rule import validate_rules

FORBIDDEN_SERVICE_USERS = ("Administrator", "Guest")


class Agent(Document):
	def validate(self) -> None:
		validate_rules(self, "access_rules")
		self.dedupe_profiles()
		if self.run_as == "Service User":
			self.validate_service_user()

	def dedupe_profiles(self) -> None:
		"""One row per attached profile. Two rows would grant nothing extra and read as if they did."""
		seen = set()
		kept = []
		for row in self.get("access_profiles") or []:
			if row.access_profile in seen:
				continue
			seen.add(row.access_profile)
			kept.append(row)
		if len(kept) != len(self.get("access_profiles") or []):
			self.set("access_profiles", kept)

	def validate_service_user(self) -> None:
		"""A service user runs without a human in the loop, so its blast radius is the whole point."""
		if not self.service_user:
			frappe.throw(_("Service User is required when Run As is Service User."))

		if self.service_user in FORBIDDEN_SERVICE_USERS:
			frappe.throw(_("{0} cannot be a service user.").format(self.service_user))

		if "System Manager" in frappe.get_roles(self.service_user):
			frappe.throw(
				_(
					"Service user {0} has the System Manager role. Use an account with narrow roles instead."
				).format(self.service_user)
			)

		if not frappe.db.exists("User Permission", {"user": self.service_user}):
			frappe.msgprint(
				_(
					"Service user {0} has no User Permissions. It can reach every record its roles allow."
				).format(self.service_user),
				title=_("Unrestricted service user"),
				indicator="orange",
			)
