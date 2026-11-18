# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe import _
from frappe.model.document import Document

from frappe_agents.rehearsals import scoring
from frappe_agents.rehearsals.runner import (
	VECTOR_COMMENT,
	VECTOR_FILE,
	VECTOR_NONE,
	check_run_as,
	validate_prompt,
)


class AgentRehearsal(Document):
	"""A saved test case. Saving it runs nothing; the Run rehearsal button does.

	The checks here are the ones a manager should hear about while writing the
	case, not hours later on a failed result. The run-as rule is asked again
	when the rehearsal runs, because a role can be taken away in between.
	"""

	def validate(self) -> None:
		validate_prompt(self.prompt)
		agent = frappe.get_doc("Agent", self.agent)
		self.run_as_user = check_run_as(agent, self.run_as_user)
		self.validate_context()
		self.validate_bait()
		self.validate_expectations()

	def validate_context(self) -> None:
		if not self.context_doctype and not self.context_name:
			return
		if not self.context_doctype or not self.context_name:
			frappe.throw(_("Give both the document type and the document, or neither."))
		if not frappe.db.exists(self.context_doctype, self.context_name):
			frappe.throw(_("No such document: {0} {1}").format(self.context_doctype, self.context_name))
		frappe.has_permission(self.context_doctype, "read", doc=self.context_name, throw=True)

	def validate_bait(self) -> None:
		vector = self.bait_vector or VECTOR_NONE
		if vector == VECTOR_NONE:
			return
		if not (self.bait_text or "").strip():
			frappe.throw(_("Bait Vector is {0}, so Bait Text is required.").format(_(vector)))
		if vector == VECTOR_COMMENT and not self.context_name:
			frappe.throw(_("A comment needs a document to go on. Set the document."))
		if vector == VECTOR_FILE and not frappe.db.get_value("Agent", self.agent, "may_read_files"):
			frappe.msgprint(
				_(
					"Agent {0} may not read files, so it cannot read the bait. "
					"This rehearsal will be Inconclusive until the agent may read files."
				).format(self.agent),
				indicator="orange",
				alert=True,
			)

	def validate_expectations(self) -> None:
		if not self.expectations:
			frappe.throw(_("Add at least one expectation. A rehearsal with none cannot pass or fail."))
		for row in self.expectations:
			row.value = (row.value or "").strip()
			if row.expectation in scoring.VALUELESS:
				row.value = ""
				continue
			if not row.value:
				frappe.throw(_("Row {0}: {1} needs a value.").format(row.idx, _(row.expectation)))
			if row.expectation == scoring.DOCTYPES_WITHIN:
				for doctype in (part.strip() for part in row.value.split(",")):
					if doctype and not frappe.db.exists("DocType", doctype):
						frappe.throw(_("Row {0}: no such document type: {1}").format(row.idx, doctype))
