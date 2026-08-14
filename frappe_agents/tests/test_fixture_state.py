# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The fixtures repair the state the suite runs on.

A site the tests share is a site somebody else can leave switched off. When the
provider or a model profile is disabled — by a test that died halfway, or by a
person clicking around the site — every run that needs it fails on state, and the
failure names the profile rather than the cause. Setup puts those rows back, says
which ones it had to put back, and can undo exactly that.
"""

import frappe
from frappe.utils import cint

from frappe_agents.runner.providers import call_model_stream
from frappe_agents.tests.fixtures import (
	EXTRACT_PROFILE,
	PROFILE,
	PROVIDER,
	AgentTestCase,
	enablement_changes,
	ensure_fixtures,
	restore_enablement,
)

PROFILE_DT = "LLM Model Profile"
PROVIDER_DT = "LLM Provider"
MESSAGES = [{"role": "user", "content": "How many?"}]


class TestFixtureState(AgentTestCase):
	def disable(self, doctype: str, name: str) -> None:
		frappe.db.set_value(doctype, name, "enabled", 0, update_modified=False)
		frappe.clear_document_cache(doctype, name)

	def enabled(self, doctype: str, name: str) -> bool:
		return bool(cint(frappe.db.get_value(doctype, name, "enabled")))

	def assert_usable(self, profile: str) -> None:
		"""The provider layer accepts the profile. Nothing is pulled, so nothing dials out."""
		stream = call_model_stream(profile, MESSAGES)
		stream.close()

	def test_fixture_setup_restores_existing_profile_enablement(self):
		rows = [(PROVIDER_DT, PROVIDER), (PROFILE_DT, PROFILE), (PROFILE_DT, EXTRACT_PROFILE)]

		# Twice: setup that repairs the site once and not again is setup that
		# leaves the second run of the suite red.
		for _ in range(2):
			for doctype, name in rows:
				self.disable(doctype, name)

			ensure_fixtures()

			for doctype, name in rows:
				self.assertTrue(self.enabled(doctype, name), f"{doctype} {name} is still disabled")
			self.assert_usable(PROFILE)
			self.assert_usable(EXTRACT_PROFILE)

		# And it says so rather than doing it quietly.
		self.assertEqual(sorted(enablement_changes()), sorted(rows))

		restore_enablement()
		for doctype, name in rows:
			self.assertFalse(self.enabled(doctype, name), f"{doctype} {name} was not put back")
