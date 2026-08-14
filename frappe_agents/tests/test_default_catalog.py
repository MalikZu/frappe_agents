# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The seeded catalog: present, disabled, and never in an admin's way.

The dangerous failure here is not a missing row — it is a seeded row arriving
ENABLED (the fixture-leak class of bug: something with model access lands on a
site uninvited), or a re-run of the seed stomping an admin's key and edits.
Every test therefore runs the seed at least twice.

The suite's site may already carry the catalog (the patch runs on migrate), so
each test builds its own certainty: it deletes the seeded names inside the
test transaction and reseeds, instead of trusting whatever state migrate left.
"""

import frappe

from frappe_agents.default_catalog import PROFILES, PROVIDERS, seed_default_catalog
from frappe_agents.runner.providers import endpoint_refusal
from frappe_agents.tests.fixtures import AgentTestCase

PROVIDER_NAMES = tuple(row["provider_name"] for row in PROVIDERS)
PROFILE_NAMES = tuple(row[0] for row in PROFILES)


def _purge_catalog() -> None:
	# Profiles first — they link to providers. In-transaction; the test
	# rollback puts everything back.
	for name in PROFILE_NAMES:
		if frappe.db.exists("LLM Model Profile", name):
			frappe.delete_doc("LLM Model Profile", name, ignore_permissions=True, force=True)
	for name in PROVIDER_NAMES:
		if frappe.db.exists("LLM Provider", name):
			frappe.delete_doc("LLM Provider", name, ignore_permissions=True, force=True)


class TestDefaultCatalog(AgentTestCase):
	def test_seed_creates_full_catalog_disabled_and_keyless(self):
		_purge_catalog()
		seed_default_catalog()

		for name in PROVIDER_NAMES:
			row = frappe.db.get_value("LLM Provider", name, ["enabled", "self_hosted"], as_dict=True)
			self.assertIsNotNone(row, f"provider {name} not seeded")
			self.assertEqual(row.enabled, 0, f"provider {name} arrived ENABLED")
			self.assertEqual(row.self_hosted, 0)
			# Password fields live in __Auth; an unset one has no row at all.
			doc = frappe.get_doc("LLM Provider", name)
			self.assertFalse(doc.get_password("api_key", raise_exception=False))

		for name in PROFILE_NAMES:
			row = frappe.db.get_value(
				"LLM Model Profile", name, ["enabled", "provider", "model_id"], as_dict=True
			)
			self.assertIsNotNone(row, f"profile {name} not seeded")
			self.assertEqual(row.enabled, 0, f"profile {name} arrived ENABLED")
			self.assertIn(row.provider, PROVIDER_NAMES)
			self.assertTrue(row.model_id)

	def test_seed_is_idempotent(self):
		_purge_catalog()
		seed_default_catalog()
		before = {name: frappe.db.get_value("LLM Model Profile", name, "modified") for name in PROFILE_NAMES}
		seed_default_catalog()
		after = {name: frappe.db.get_value("LLM Model Profile", name, "modified") for name in PROFILE_NAMES}
		self.assertEqual(before, after)
		self.assertEqual(
			frappe.db.count("LLM Provider", {"name": ("in", PROVIDER_NAMES)}),
			len(PROVIDER_NAMES),
		)

	def test_reseed_never_clobbers_admin_edits(self):
		_purge_catalog()
		seed_default_catalog()

		provider = frappe.get_doc("LLM Provider", "OpenAI")
		provider.enabled = 1
		provider.api_key = "sk-admin-set-this"
		provider.save(ignore_permissions=True)

		profile = frappe.get_doc("LLM Model Profile", "GPT-5.6 Luna")
		profile.enabled = 1
		profile.model_id = "gpt-5.6-luna-custom"
		profile.cost_input_per_million = 0.05
		profile.save(ignore_permissions=True)

		seed_default_catalog()

		provider = frappe.get_doc("LLM Provider", "OpenAI")
		self.assertEqual(provider.enabled, 1)
		self.assertEqual(provider.get_password("api_key"), "sk-admin-set-this")

		profile = frappe.get_doc("LLM Model Profile", "GPT-5.6 Luna")
		self.assertEqual(profile.enabled, 1)
		self.assertEqual(profile.model_id, "gpt-5.6-luna-custom")
		self.assertEqual(profile.cost_input_per_million, 0.05)

	def test_seed_leaves_user_created_rows_alone(self):
		_purge_catalog()
		user_provider = frappe.new_doc("LLM Provider")
		user_provider.provider_name = "My Local Router"
		user_provider.provider_type = "OpenAI Compatible"
		user_provider.base_url = "https://router.example.com/v1"
		user_provider.enabled = 1
		user_provider.insert(ignore_permissions=True)
		stamp = frappe.db.get_value("LLM Provider", "My Local Router", "modified")

		seed_default_catalog()

		self.assertEqual(frappe.db.get_value("LLM Provider", "My Local Router", "modified"), stamp)

	def test_every_seeded_base_url_passes_the_trust_boundary(self):
		for row in PROVIDERS:
			self.assertIsNone(
				endpoint_refusal(row["base_url"], 0),
				f"{row['provider_name']}: {row['base_url']} refused",
			)

	def test_every_seeded_wire_format_is_implemented(self):
		implemented = {"OpenAI Compatible", "Anthropic"}
		for row in PROVIDERS:
			self.assertIn(row["provider_type"], implemented)

	def test_every_profile_links_a_seeded_provider(self):
		for row in PROFILES:
			self.assertIn(row[1], PROVIDER_NAMES, f"profile {row[0]} links unknown provider")
