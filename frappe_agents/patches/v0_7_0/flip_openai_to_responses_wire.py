# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Move OpenAI's own endpoint onto the Responses wire.

Every other patch in this app leaves an administrator's edits alone. This one
does not, deliberately, and it is the only sanctioned exception: a provider row
that points at `https://api.openai.com/v1` is talking to OpenAI, and on OpenAI
the compat wire is now strictly worse than the Responses wire. GPT-5.4 and later
refuse to call tools with reasoning off on `/v1/chat/completions`, and every
agent this app runs calls tools — so the row a human typed by hand is exactly
the row that starts returning 400s. Preserving it unchanged would preserve a
break.

The narrowness is the safety. Only rows that satisfy both halves move:

* `provider_type` is still `OpenAI Compatible` — a row already on Responses, or
  on Anthropic, is not this patch's business; and
* the base URL resolves to OpenAI's own API root — either it is literally
  `https://api.openai.com/v1` (trailing slash or not, the same address typed two
  ways) or it is blank, which means "use the provider default" and the default
  is that same constant. A blank row is already sending its prompts to OpenAI;
  it just does not say so on the form.

That leaves every OpenAI-compatible third party — OpenRouter, DeepSeek, xAI,
Gemini's compat endpoint, a self-hosted vLLM — exactly where it is. Those hosts
serve `/v1/chat/completions` and mostly do not serve `/v1/responses` at all, so
flipping them would break working rows to fix nothing.

Nothing but `provider_type` is written. The key, the enabled flag, the base URL
and the self-hosted flag are the administrator's and stay untouched, and
`endpoint_refusal` is unchanged by the move — same host, same scheme, same
rules. Re-running is a no-op: the second pass finds no `OpenAI Compatible` row
left at that address.
"""

import frappe

from frappe_agents.runner.providers import OPENAI_BASE_URL, PROVIDER_OPENAI, PROVIDER_RESPONSES


def execute() -> None:
	"""Flip every OpenAI row still on the compat wire, and say which."""
	for name in flip_openai_providers():
		print(f"frappe_agents: {name} now uses the {PROVIDER_RESPONSES} wire")


def flip_openai_providers() -> list[str]:
	"""Move the qualifying rows and return their names, in the order moved."""
	flipped = []
	for row in frappe.get_all(
		"LLM Provider", filters={"provider_type": PROVIDER_OPENAI}, fields=["name", "base_url"]
	):
		if not is_openai_endpoint(row.base_url):
			continue
		frappe.db.set_value("LLM Provider", row.name, "provider_type", PROVIDER_RESPONSES)
		frappe.clear_document_cache("LLM Provider", row.name)
		flipped.append(row.name)

	return flipped


def is_openai_endpoint(base_url: str | None) -> bool:
	"""Whether this base URL sends its requests to OpenAI's own API root.

	Blank counts: the wire falls back to `OPENAI_BASE_URL` when the field is
	empty, so a blank row is at OpenAI whether or not it says so. Everything else
	is compared after trimming whitespace and trailing slashes, because
	`https://api.openai.com/v1/` and `https://api.openai.com/v1` are one address
	and an admin will have typed either.
	"""
	base_url = (base_url or "").strip().rstrip("/")
	return not base_url or base_url.lower() == OPENAI_BASE_URL.rstrip("/").lower()
