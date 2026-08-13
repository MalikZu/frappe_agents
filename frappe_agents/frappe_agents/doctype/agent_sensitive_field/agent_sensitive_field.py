# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe import _
from frappe.model.document import Document


class AgentSensitiveField(Document):
	def validate(self) -> None:
		"""A typo here is a field that quietly stops being sensitive, so refuse one."""
		self.fieldname = (self.fieldname or "").strip()

		if not self.document_type or not self.fieldname:
			return

		if not frappe.get_meta(self.document_type).get_field(self.fieldname):
			frappe.throw(
				_("{0} has no field named {1}.").format(self.document_type, self.fieldname),
				title=_("Unknown field"),
			)
