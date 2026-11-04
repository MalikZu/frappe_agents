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

import copy
import json
import re
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


class EventRedactor:
	"""A run's event log, with the same values held back as its audit rows.

	The log stores every tool call a second time: the arguments in the model's
	tool-call block and in `tool_execution_start`, the result in
	`tool_execution_end` and in the tool-result message. Each copy is redacted by
	the rules `_log_call` uses — keys from the doctype the call names — and the
	values found are remembered by tool-call id, so the result of that call, and
	any later prose, can be scrubbed of them too.

	One redactor reads one run's events in order. It never changes what it is
	handed: the live frame keeps the raw values, because the person who ran the
	turn already holds them. What it returns is a new copy for the log.
	"""

	def __init__(self) -> None:
		# Tool-call id -> (the keys its doctype marks sensitive, the values held back).
		self.calls: dict[str, tuple[set[str], set[str]]] = {}
		# Every value held back so far, for the prose the model writes later.
		self.secrets: set[str] = set()

	def event(self, payload: dict) -> dict:
		"""One event as it may be stored."""
		event = copy.deepcopy(payload)
		kind = event.get("type")

		if kind == "tool_execution_start":
			event["args"] = self._arguments(event.get("toolCallId"), event.get("args"))
		elif kind == "tool_execution_update":
			event["partialResult"] = self._result(event.get("toolCallId"), event.get("partialResult"))
		elif kind == "tool_execution_end":
			event["result"] = self._result(event.get("toolCallId"), event.get("result"))

		if isinstance(event.get("message"), dict):
			event["message"] = self._message(event["message"])
		if isinstance(event.get("toolResults"), list):
			event["toolResults"] = [self._message(item) for item in event["toolResults"]]
		return event

	def _call(self, call_id: Any) -> tuple[set[str], set[str]]:
		return self.calls.get(call_id) or (set(FALLBACK_SENSITIVE_KEYS), set())

	def _remember(self, call_id: Any, keys: set[str], secrets: set[str]) -> None:
		# A log redacted once already hands back the marker as a "value". It is not one.
		secrets = secrets - {REDACTED}
		known_keys, known_secrets = self.calls.get(call_id) or (set(), set())
		self.calls[call_id] = (known_keys | keys, known_secrets | secrets)
		self.secrets |= secrets

	def _arguments(self, call_id: Any, args: Any) -> Any:
		keys = sensitive_keys(args)
		clean, secrets = redact(args, keys)
		self._remember(call_id, keys, secrets)
		return clean

	def _result(self, call_id: Any, result: Any) -> Any:
		"""A tool result: its text blocks and its details."""
		if not isinstance(result, dict):
			return result
		keys, _ = self._call(call_id)
		found: set[str] = set()
		if "details" in result:
			result["details"], details_secrets = redact(result["details"], keys)
			found |= details_secrets
		blocks = result.get("content")
		if isinstance(blocks, list):
			for block in blocks:
				if isinstance(block, dict) and isinstance(block.get("text"), str):
					block["text"], text_secrets = redact_json_text(block["text"], keys)
					found |= text_secrets
		self._remember(call_id, keys, found)
		_, secrets = self._call(call_id)
		self._scrub_blocks(blocks, secrets)
		return result

	def _message(self, message: Any) -> Any:
		if not isinstance(message, dict):
			return message
		role = message.get("role")
		if role == "toolResult":
			return self._result(message.get("toolCallId"), message)
		if role != "assistant":
			# The person's own words are theirs and are stored as they typed them.
			return message

		blocks = message.get("content")
		if isinstance(blocks, list):
			for block in blocks:
				if isinstance(block, dict) and block.get("type") == "toolCall":
					block["arguments"] = self._arguments(block.get("id"), block.get("arguments"))
		# A model that read a value can repeat it. What it says is prose, so the
		# values held back anywhere in the run so far are taken out of it.
		self._scrub_blocks(blocks, self.secrets)
		if isinstance(message.get("errorMessage"), str):
			message["errorMessage"] = scrub(message["errorMessage"], self.secrets)
		return message

	@staticmethod
	def _scrub_blocks(blocks: Any, secrets: set[str]) -> None:
		if not isinstance(blocks, list) or not secrets:
			return
		for block in blocks:
			if not isinstance(block, dict):
				continue
			for field in ("text", "thinking"):
				if isinstance(block.get(field), str):
					block[field] = scrub(block[field], secrets)


def redact_events(events: list) -> list:
	"""A whole stored log's events, redacted in order — for logs written before this."""
	redactor = EventRedactor()
	return [redactor.event(event) if isinstance(event, dict) else event for event in events]


def redact_json_text(text: str, keys: set[str]) -> tuple[str, set[str]]:
	"""Text that holds a tool's JSON result, with sensitive values held back.

	A tool result reaches the model as JSON text, so it is redacted by key like
	any other result. Text that is left unchanged keeps every byte it had. Text
	that does not parse — a result cut short at the model's limit — is redacted
	by pattern instead, which finds the same keys with string values.
	"""
	try:
		parsed = json.loads(text)
	except ValueError:
		return _redact_json_pattern(text, keys)
	if not isinstance(parsed, (dict, list)):
		return text, set()
	clean, secrets = redact(parsed, keys)
	if clean == parsed:
		return text, secrets
	return frappe.as_json(clean), secrets


def _redact_json_pattern(text: str, keys: set[str]) -> tuple[str, set[str]]:
	if not keys:
		return text, set()
	names = "|".join(re.escape(key) for key in sorted(keys))
	pattern = re.compile(rf'("(?:{names})"\s*:\s*)"((?:[^"\\]|\\.)*)"', re.IGNORECASE)
	secrets: set[str] = set()

	def hold_back(match: re.Match) -> str:
		try:
			secrets.add(json.loads(f'"{match.group(2)}"'))
		except ValueError:
			secrets.add(match.group(2))
		return f'{match.group(1)}"{REDACTED}"'

	return pattern.sub(hold_back, text), secrets
