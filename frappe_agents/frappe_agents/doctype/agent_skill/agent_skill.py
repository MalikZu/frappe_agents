# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe import _
from frappe.model.document import Document

APPROVER_ROLE = "Agent Manager"
UNAPPROVED_STATUSES = ("Draft", "In Review")


class AgentSkill(Document):
	def before_insert(self) -> None:
		"""No skill is born approved. Fixtures, imports and API payloads all land as Draft."""
		self.status = "Draft"
		self.approved_by = None

	def validate(self) -> None:
		self.demote_on_body_change()
		self.validate_approval()
		self.clear_stale_approver()

	def demote_on_body_change(self) -> None:
		"""An approval covers one specific text. Change the text and the approval is spent."""
		before = self.get_doc_before_save()
		if not before or before.status != "Approved":
			return

		if self.body == before.body:
			return

		self.status = "Draft"
		self.approved_by = None
		frappe.msgprint(
			_("The body changed, so this skill went back to Draft and needs approving again."),
			title=_("Approval withdrawn"),
			indicator="orange",
		)

	def validate_approval(self) -> None:
		"""Separation of duties: a skill is signed off by someone other than its author."""
		before = self.get_doc_before_save()
		if self.status != "Approved" or (before and before.status == "Approved"):
			return

		user = frappe.session.user
		if APPROVER_ROLE not in frappe.get_roles(user):
			frappe.throw(
				_("Only a user with the {0} role can approve a skill.").format(APPROVER_ROLE),
				frappe.PermissionError,
			)

		if user == self.owner:
			frappe.throw(
				_("A skill has to be approved by someone other than the person who wrote it."),
				frappe.PermissionError,
			)

		self.approved_by = user

	def clear_stale_approver(self) -> None:
		"""approved_by names who signed off the body as it stands. On the way back down to Draft
		or In Review there is no sign-off left to show."""
		if self.status in UNAPPROVED_STATUSES:
			self.approved_by = None
