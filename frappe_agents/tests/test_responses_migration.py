# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""What the v0_6_0 flip does to the provider rows already on a site.

This patch is the app's one sanctioned exception to never-clobber: it rewrites a
field on a row an administrator may have typed by hand. So the release-blocking
question is not "does it work" but "does it stay inside its own remit", and both
halves of that are asserted here.

* Rows at OpenAI's own API root move — including the ones a human edited, and
  including the ones whose base URL is blank, because blank means the default
  and the default is that same address.
* Everything else is untouched: OpenRouter, DeepSeek, xAI and Gemini's compat
  endpoint keep the compat wire they need, an Anthropic row is not this patch's
  business, and a row already on Responses is left exactly as found.

Idempotency is asserted the honest way — by running `execute` and then asking
the patch again what work is left, not merely by checking the end state still
looks right. A patch that rewrote the same row on every migrate would pass the
weaker test.

Every assertion names the row it is about rather than comparing the whole return
list, because the site under test is a real site: it already carries the seeded
catalog and the runner fixtures, and a test that pinned the exact set of flipped
rows would be pinning its neighbours' data as well as its own.
"""

import frappe

from frappe_agents.patches.v0_6_0.flip_openai_to_responses_wire import (
	execute,
	flip_openai_providers,
)
from frappe_agents.runner.providers import (
	OPENAI_BASE_URL,
	PROVIDER_ANTHROPIC,
	PROVIDER_OPENAI,
	PROVIDER_RESPONSES,
)
from frappe_agents.tests.fixtures import AgentTestCase

# The compat rows the fresh catalog seeds beside OpenAI. All of them serve
# /v1/chat/completions and none of them serve /v1/responses.
THIRD_PARTIES = (
	("FA Migration OpenRouter", "https://openrouter.ai/api/v1"),
	("FA Migration DeepSeek", "https://api.deepseek.com/v1"),
	("FA Migration xAI", "https://api.x.ai/v1"),
	("FA Migration Gemini", "https://generativelanguage.googleapis.com/v1beta/openai"),
)


class ResponsesMigrationTest(AgentTestCase):
	def provider(
		self,
		name: str,
		base_url: str | None = OPENAI_BASE_URL,
		provider_type: str = PROVIDER_OPENAI,
		**values,
	):
		"""One provider row, gone again when this test rolls back."""
		doc = frappe.get_doc(
			{
				"doctype": "LLM Provider",
				"provider_name": name,
				"provider_type": provider_type,
				"base_url": base_url,
				"enabled": 0,
				**values,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(frappe.clear_document_cache, "LLM Provider", doc.name)
		return doc

	def wire(self, name: str) -> str:
		return frappe.db.get_value("LLM Provider", name, "provider_type")

	def assertMoved(self, row) -> None:
		self.assertIn(row.name, flip_openai_providers())
		self.assertEqual(self.wire(row.name), PROVIDER_RESPONSES)

	def assertUntouched(self, row) -> None:
		was = self.wire(row.name)
		self.assertNotIn(row.name, flip_openai_providers())
		self.assertEqual(self.wire(row.name), was)

	def test_the_openai_row_moves_to_the_responses_wire(self):
		self.assertMoved(self.provider("FA Migration OpenAI"))

	def test_a_blank_base_url_is_openai_too(self):
		# Empty means "use the provider default", and that default is OpenAI.
		# The row was already sending its prompts there without saying so.
		self.assertMoved(self.provider("FA Migration Default", base_url=None))

	def test_a_trailing_slash_is_the_same_address(self):
		self.assertMoved(self.provider("FA Migration Slash", base_url=f"{OPENAI_BASE_URL}/"))

	def test_a_row_a_human_edited_moves_as_well(self):
		# The deliberate exception: this is the row the flip exists for, because
		# it is the one an agent is actually running on.
		self.assertMoved(self.provider("FA Migration Live", enabled=1, api_key="fa-migration-key"))

	def test_nothing_but_the_wire_field_is_written(self):
		row = self.provider("FA Migration Kept", enabled=1, self_hosted=0, api_key="fa-migration-key")

		flip_openai_providers()

		after = frappe.get_doc("LLM Provider", row.name)
		self.assertEqual(after.provider_type, PROVIDER_RESPONSES)
		self.assertEqual(after.base_url, OPENAI_BASE_URL)
		self.assertEqual(after.enabled, 1)
		self.assertEqual(after.self_hosted, 0)
		self.assertEqual(after.get_password("api_key", raise_exception=False), "fa-migration-key")

	def test_running_it_again_finds_nothing_left_to_do(self):
		row = self.provider("FA Migration Twice", enabled=1)

		execute()
		self.assertEqual(self.wire(row.name), PROVIDER_RESPONSES)

		# Not just "the end state still looks right": after a full pass there
		# must be no work left anywhere on the site, or the patch rewrites rows
		# on every migrate.
		self.assertEqual(flip_openai_providers(), [])
		self.assertEqual(self.wire(row.name), PROVIDER_RESPONSES)

	def test_a_row_already_on_the_responses_wire_is_left_alone(self):
		self.assertUntouched(self.provider("FA Migration Ahead", provider_type=PROVIDER_RESPONSES))

	def test_an_anthropic_row_is_left_alone(self):
		self.assertUntouched(
			self.provider(
				"FA Migration Anthropic",
				base_url="https://api.anthropic.com",
				provider_type=PROVIDER_ANTHROPIC,
			)
		)

	def test_openai_compatible_third_parties_keep_the_compat_wire(self):
		rows = [self.provider(name, base_url=base) for name, base in THIRD_PARTIES]

		flipped = flip_openai_providers()
		for row in rows:
			self.assertNotIn(row.name, flipped)
			self.assertEqual(self.wire(row.name), PROVIDER_OPENAI, msg=row.name)

	def test_a_row_is_judged_by_its_address_not_its_name(self):
		# "OpenAI" in the provider name means nothing: this one is a local
		# gateway, it does not serve /v1/responses, and moving it would break it.
		self.assertUntouched(
			self.provider("FA Migration OpenAI Gateway", base_url="http://localhost:1/v1", self_hosted=1)
		)

	def test_it_moves_the_openai_row_out_of_a_full_catalog_and_no_other(self):
		openai = self.provider("FA Migration Catalog OpenAI")
		others = [self.provider(name, base_url=base) for name, base in THIRD_PARTIES]
		anthropic = self.provider(
			"FA Migration Catalog Anthropic",
			base_url="https://api.anthropic.com",
			provider_type=PROVIDER_ANTHROPIC,
		)

		flipped = flip_openai_providers()

		self.assertIn(openai.name, flipped)
		self.assertNotIn(anthropic.name, flipped)
		self.assertEqual(self.wire(anthropic.name), PROVIDER_ANTHROPIC)
		for row in others:
			self.assertEqual(self.wire(row.name), PROVIDER_OPENAI, msg=row.name)
