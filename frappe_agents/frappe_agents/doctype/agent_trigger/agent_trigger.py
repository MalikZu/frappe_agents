# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""A trigger is refused at save for anything that would only fail at run time.

The run checks all of this again, as the user it runs for, because a doctype, an
agent and a person's permissions all change after a trigger is saved. Refusing
here is so the person configuring it hears about a mistake once, in words, and
not as a Failed run on every document afterwards.
"""

import frappe
from frappe import _
from frappe.model import default_fields, no_value_fields, table_fields
from frappe.model.document import Document
from frappe.utils import cint

from frappe_agents.access.exclusions import is_excluded
from frappe_agents.access.grants import VERB_UPDATE_DRAFT, grant_for, in_legacy_mode
from frappe_agents.extraction.gate import sensitive_fieldnames
from frappe_agents.tools.base import AUTONOMY_CAPABILITIES, CAPABILITY_DRAFT
from frappe_agents.tools.registry import get_agent_tool_names
from frappe_agents.triggers import (
	EVENT_ATTACHMENT,
	SESSION_USER,
	TARGET_FIELDTYPES,
	WRITES_ANSWER,
	WRITES_FILE_TEXT,
)
from frappe_agents.triggers.dispatch import clear_index, skips_today
from frappe_agents.triggers.execution import workflow_state_field

# Values a trigger may not read: they are secrets, or they are not values.
UNREADABLE_FIELDTYPES = frozenset({"Password", *no_value_fields, *table_fields})


class AgentTrigger(Document):
	def validate(self) -> None:
		self.validate_document_type()
		self.validate_agent()
		self.validate_source()
		self.validate_target()
		self.validate_conditions()

	def on_update(self) -> None:
		clear_index()

	def on_trash(self) -> None:
		clear_index()

	def after_rename(self, old: str, new: str, merge: bool = False) -> None:
		clear_index()

	def onload(self) -> None:
		# Said on the form, because a trigger that stopped firing has no other place
		# to say why.
		self.set_onload("skips_today", skips_today(self.name))

	def validate_document_type(self) -> None:
		meta = frappe.get_meta(self.document_type)
		if meta.istable or meta.issingle:
			frappe.throw(
				_("A trigger watches documents. {0} is a child table or a single.").format(self.document_type)
			)
		if self.document_type == "File" or is_excluded(self.document_type):
			frappe.throw(_("{0} cannot have an agent trigger.").format(self.document_type))

	def validate_agent(self) -> None:
		agent = frappe.get_doc("Agent", self.agent)
		if agent.run_as != SESSION_USER:
			frappe.throw(
				_(
					"Agent {0} runs as a service user. A trigger runs as the person whose action "
					"fired it, so its agent must run as Session User."
				).format(agent.name)
			)
		if CAPABILITY_DRAFT not in AUTONOMY_CAPABILITIES.get(agent.autonomy, set()):
			frappe.throw(_("Agent {0} needs Draft autonomy to write a field.").format(agent.name))

		tools = get_agent_tool_names(agent)
		if "update_draft" not in tools:
			frappe.throw(
				_(
					"Agent {0} may not edit drafts. Give it an access rule that allows Update Draft on {1}."
				).format(agent.name, self.document_type)
			)
		if not in_legacy_mode(agent) and not grant_for(agent, self.document_type).get(VERB_UPDATE_DRAFT):
			frappe.throw(
				_("Agent {0} has no access rule that allows Update Draft on {1}.").format(
					agent.name, self.document_type
				)
			)
		if self.event == EVENT_ATTACHMENT and "read_document" not in tools:
			frappe.throw(
				_("Agent {0} may not read files. Turn on May Read Files on the agent.").format(agent.name)
			)

	def validate_source(self) -> None:
		if self.event == EVENT_ATTACHMENT:
			self.source_field = None
		else:
			self.file_extensions = None
			if self.writes == WRITES_FILE_TEXT:
				frappe.throw(_("File Text needs the Attachment Added event."))
			df = self._field(self.source_field, _("Source Field"))
			if df.fieldtype in UNREADABLE_FIELDTYPES or df.fieldname in sensitive_fieldnames(
				self.document_type
			):
				frappe.throw(_("The field {0} cannot be the source of a trigger.").format(df.fieldname))

		if self.writes == WRITES_ANSWER and not (self.instruction or "").strip():
			frappe.throw(_("Write an instruction: it is what the agent does with the input."))

	def validate_target(self) -> None:
		df = self._field(self.target_field, _("Target Field"))
		if df.fieldtype not in TARGET_FIELDTYPES:
			frappe.throw(
				_("The target field must be one of: {0}. {1} is {2}.").format(
					", ".join(sorted(TARGET_FIELDTYPES)), df.fieldname, df.fieldtype
				)
			)
		if cint(df.read_only):
			frappe.throw(_("The target field {0} is read-only.").format(df.fieldname))
		if df.fieldname in sensitive_fieldnames(self.document_type):
			frappe.throw(
				_("The target field {0} is marked sensitive in Agent Settings.").format(df.fieldname)
			)
		if df.fieldname == self.source_field:
			frappe.throw(_("The target field cannot be the source field."))
		if df.fieldname == workflow_state_field(self.document_type):
			frappe.throw(_("The target field cannot be the workflow state."))
		if df.fieldtype == "Select" and self.writes == WRITES_FILE_TEXT:
			frappe.throw(_("File Text cannot go into a Select field."))

	def validate_conditions(self) -> None:
		meta = frappe.get_meta(self.document_type)
		for row in self.get("conditions") or []:
			if row.fieldname not in default_fields and not meta.get_field(row.fieldname):
				frappe.throw(
					_("Row {0}: {1} has no field {2}.").format(row.idx, self.document_type, row.fieldname)
				)

	def _field(self, fieldname: str | None, label: str):
		df = frappe.get_meta(self.document_type).get_field(fieldname) if fieldname else None
		if not df:
			frappe.throw(_("{0}: {1} has no field {2}.").format(label, self.document_type, fieldname or ""))
		return df
