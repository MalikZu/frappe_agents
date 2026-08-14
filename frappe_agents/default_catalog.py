# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""The out-of-the-box model catalog.

A fresh install knows the famous providers, so an admin's first session is
"paste a key, tick Enabled, pick a model" — not a research project into model
IDs, wire formats, and base URLs. Everything here ships disabled and key-less:
a seeded row is a pre-filled form, never a capability granted.

Seeding is insert-if-missing by name, and nothing else. A row that already
exists — seeded by a past release, edited by an admin, or created by hand
under the same name — is left entirely alone, so running on every install and
migrate is safe and an admin's key, enablement, and price edits are never
clobbered. The flip side: deleting a seeded row brings it back (disabled) on
the next migrate; the supported way to hide one is to leave it disabled.
Catalog changes ship as edits to these tables plus a fresh patch entry —
this function never updates an existing row.

Prices, context limits, and capability flags were verified against live
provider docs on 2026-08-15; internal/default-models-spec.md records the
sources and the judgment calls (pricing tiers, the -preview model ID, which
wire gets native PDF).
"""

import frappe

PROVIDERS = (
	{
		"provider_name": "OpenAI",
		"provider_type": "OpenAI Compatible",
		"base_url": "https://api.openai.com/v1",
	},
	{
		"provider_name": "Anthropic",
		"provider_type": "Anthropic",
		"base_url": "https://api.anthropic.com",
	},
	{
		"provider_name": "Google Gemini",
		"provider_type": "OpenAI Compatible",
		"base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
	},
	{
		"provider_name": "xAI",
		"provider_type": "OpenAI Compatible",
		"base_url": "https://api.x.ai/v1",
	},
	{
		"provider_name": "DeepSeek",
		"provider_type": "OpenAI Compatible",
		"base_url": "https://api.deepseek.com/v1",
	},
)

# (profile_name, provider, model_id, context_limit, images, pdf, $/M in, $/M out)
PROFILES = (
	("GPT-5.6 Sol", "OpenAI", "gpt-5.6-sol", 1_048_576, 1, 0, 5.0, 30.0),
	("GPT-5.6 Terra", "OpenAI", "gpt-5.6-terra", 1_048_576, 1, 0, 2.0, 12.0),
	("GPT-5.6 Luna", "OpenAI", "gpt-5.6-luna", 1_048_576, 1, 0, 0.2, 1.2),
	("Claude Fable 5", "Anthropic", "claude-fable-5", 1_000_000, 1, 1, 10.0, 50.0),
	("Claude Opus 5", "Anthropic", "claude-opus-5", 1_000_000, 1, 1, 5.0, 25.0),
	("Claude Sonnet 5", "Anthropic", "claude-sonnet-5", 1_000_000, 1, 1, 3.0, 15.0),
	("Claude Haiku 4.5", "Anthropic", "claude-haiku-4-5", 200_000, 1, 1, 1.0, 5.0),
	("Gemini 3.7 Flash", "Google Gemini", "gemini-3.7-flash", 1_048_576, 1, 0, 0.75, 3.75),
	("Gemini 3.1 Pro", "Google Gemini", "gemini-3.1-pro-preview", 1_048_576, 1, 0, 2.0, 12.0),
	("Grok 4.6", "xAI", "grok-4.6", 500_000, 1, 0, 2.0, 6.0),
	("DeepSeek V4 Pro", "DeepSeek", "deepseek-v4-pro", 1_048_576, 0, 0, 0.435, 0.87),
	("DeepSeek V4 Flash", "DeepSeek", "deepseek-v4-flash", 1_048_576, 0, 0, 0.14, 0.28),
)


def seed_default_catalog() -> None:
	"""Insert any catalog row that is missing; touch nothing that exists."""
	for row in PROVIDERS:
		if frappe.db.exists("LLM Provider", row["provider_name"]):
			continue
		provider = frappe.new_doc("LLM Provider")
		provider.update(row)
		provider.enabled = 0
		provider.self_hosted = 0
		provider.insert(ignore_permissions=True)

	for name, provider_name, model_id, ctx, images, pdf, cost_in, cost_out in PROFILES:
		if frappe.db.exists("LLM Model Profile", name):
			continue
		profile = frappe.new_doc("LLM Model Profile")
		profile.profile_name = name
		profile.provider = provider_name
		profile.model_id = model_id
		profile.context_limit = ctx
		profile.supports_images = images
		profile.supports_pdf = pdf
		profile.cost_input_per_million = cost_in
		profile.cost_output_per_million = cost_out
		profile.enabled = 0
		profile.insert(ignore_permissions=True)
