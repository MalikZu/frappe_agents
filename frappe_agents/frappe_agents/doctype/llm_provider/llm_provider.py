# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe
from frappe.model.document import Document

from frappe_agents.runner.providers import endpoint_refusal


class LLMProvider(Document):
	def validate(self):
		self.check_base_url()

	def check_base_url(self):
		"""Refuse a Base URL that is not a safe place to send prompts and the key.

		The rule itself lives with the code that makes the request, and the
		request checks it again — this is the gate that says so while somebody is
		still looking at the form.
		"""
		refusal = endpoint_refusal(self.base_url, self.self_hosted)
		if refusal:
			frappe.throw(refusal, title=frappe._("Unsafe Base URL"))
