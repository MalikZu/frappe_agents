"""What a stored copy of a tool call may not keep.

A tool call is written down in more than one place: the Agent Tool Call row and
the run's own event log. Both are durable and an auditor reads both, so both go
through the same rules here — one registry of what is sensitive, one walk that
holds it back, one scrub for prose. Two copies with two sets of rules would be
two answers to the same question, and the weaker one is the one that leaks.

What a call *did* stays legible — the tool, the doctype, the field names, the
outcome. Only values that must not become audit content are replaced with
`REDACTED`. See `sensitive_keys` for what counts.
"""

from typing import Any

import frappe

REDACTED = "[redacted]"

# Key names redacted whatever doctype is in play — the answer for arguments that
# name no doctype at all. Deliberately tiny and exact: a rule wide enough to
# catch every secret by its name would redact half the audit trail, and a row an
# auditor cannot read is its own kind of failure.
FALLBACK_SENSITIVE_KEYS = frozenset({"password", "api_key", "token", "secret"})

# Below this length a redacted value is not worth removing from prose. "12" or
# "abc" appears inside ordinary words, and replacing every occurrence would
# corrupt the sentence that tells an auditor what happened.
SCRUB_MIN_LENGTH = 4


def sensitive_keys(payload: Any) -> set[str]:
	"""Argument keys whose values must not become audit content, lowercased.

	Three sources, in the order they are trusted:

	1. **Password fields** on the doctype the call names. A fieldtype the
	   framework itself refuses to hand back in a query is not something to
	   copy into an audit row.
	2. **Masked and administrator-marked fields** on that same doctype — the
	   extraction gate's own configuration, read through `sensitive_fieldnames`
	   so there is one registry of what an administrator called sensitive and
	   not two that can disagree.
	3. **A fallback by name** for arguments tied to no doctype at all.

	Child tables count: a payment detail hidden in a line item is the same
	value with more steps, and the keys are matched at any depth.
	"""
	keys = set(FALLBACK_SENSITIVE_KEYS)
	doctype = payload.get("doctype") if isinstance(payload, dict) else None
	if not isinstance(doctype, str) or not doctype.strip():
		return keys

	try:
		keys |= doctype_sensitive_keys(doctype.strip())
	except frappe.DoesNotExistError:
		# The call named a doctype that does not exist — the tool is about to say
		# so. The fallback keys still apply.
		pass
	except Exception:
		frappe.logger("frappe_agents").warning(
			f"could not resolve sensitive fields on {doctype}", exc_info=True
		)
	return keys


def doctype_sensitive_keys(doctype: str) -> set[str]:
	from frappe.model import table_fields

	from frappe_agents.extraction.gate import sensitive_fieldnames

	meta = frappe.get_meta(doctype)
	metas = [meta]
	metas += [
		frappe.get_meta(df.options) for df in meta.fields if df.fieldtype in table_fields and df.options
	]

	keys: set[str] = set()
	for one in metas:
		keys |= {df.fieldname for df in one.fields if df.fieldtype == "Password"}
		keys |= masked_fieldnames(one)
		keys |= sensitive_fieldnames(one.name)
	return {key.lower() for key in keys if key}


def masking_supported() -> bool:
	"""Whether this framework masks fields per user.

	frappe_agents patch (version-15): `Meta.get_masked_fields` arrived in v16.
	Where it is missing, a field an administrator masked is not automatically
	held out of an audit row, and the extraction gate has one fewer source for
	what to withhold. Nothing crashes — the protection is simply not there, and
	`docs/admin.md` says so. Agent Settings' own sensitive-field list works on
	both and is what to lean on.
	"""
	from frappe.model.meta import Meta

	return hasattr(Meta, "get_masked_fields")


def masked_fieldnames(meta: Any) -> set[str]:
	try:
		return {df.fieldname for df in meta.get_masked_fields()}
	except AttributeError:
		return set()


def redact(value: Any, keys: set[str]) -> tuple[Any, set[str]]:
	"""A copy with sensitive values replaced, and the raw values that were removed.

	Recursive over dicts and lists, so a draft's child rows and a filter object
	are covered by the same pass. Key names survive: what the call touched is the
	part an auditor needs.
	"""
	secrets: set[str] = set()
	return _walk(value, keys, secrets), secrets


def _walk(value: Any, keys: set[str], secrets: set[str]) -> Any:
	if isinstance(value, dict):
		clean = {}
		for key, item in value.items():
			if isinstance(key, str) and key.lower() in keys:
				_collect(item, secrets)
				clean[key] = REDACTED
				continue
			clean[key] = _walk(item, keys, secrets)
		return clean
	if isinstance(value, (list, tuple)):
		return [_walk(item, keys, secrets) for item in value]
	return value


def _collect(value: Any, secrets: set[str]) -> None:
	"""Every string under a redacted key, so prose can be scrubbed of it later."""
	if isinstance(value, str):
		secrets.add(value)
	elif isinstance(value, dict):
		for item in value.values():
			_collect(item, secrets)
	elif isinstance(value, (list, tuple)):
		for item in value:
			_collect(item, secrets)


def scrub(text: str | None, secrets: set[str]) -> str | None:
	"""Prose with every redacted value taken out of it.

	Prose has no keys to redact by, so it is cleaned by value: whatever was held
	back elsewhere in the same call is removed from the text as well.
	"""
	if not text or not secrets:
		return text
	for secret in secrets:
		if len(secret) >= SCRUB_MIN_LENGTH and secret in text:
			text = text.replace(secret, REDACTED)
	return text
