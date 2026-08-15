# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

import frappe

from frappe_agents.tests.fixtures import AgentTestCase


class TestProviderFieldLengths(AgentTestCase):
	def test_api_key_accepts_modern_provider_keys(self):
		# frappe's default Data/Password length is 140; OpenAI project keys
		# (sk-proj-...) already exceed 160 chars and providers keep growing
		# them. The field must never bounce a real key again.
		meta = frappe.get_meta("LLM Provider")
		self.assertGreaterEqual(meta.get_field("api_key").length or 140, 512)
		self.assertGreaterEqual(meta.get_field("base_url").length or 140, 255)
