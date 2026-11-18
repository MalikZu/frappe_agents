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
from decimal import Decimal, InvalidOperation
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

# The keys of one version change, whose values belong to the field it names.
CHANGE_VALUE_KEYS = ("from", "to")

# Authored text as the read tools hand it over. See `frappe_agents.context.untrusted`.
_UNTRUSTED = re.compile(r'<untrusted source="[^"]*">(.*)</untrusted>', re.DOTALL)

# What a stored result says when its value was cut at a sensitive key.
CUT_NOTE = "\n... (truncated)"

# A held-back value that is a number: 98765, 98765.0, 98,765.00.
_NUMERIC = re.compile(r"-?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?")
# A number as prose writes it, whole: never the middle of a longer one.
_NUMBER_IN_TEXT = re.compile(r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d+)(?:\.(\d+))?(?!\d)")
# Parts joined by hyphens or slashes: a date (2025-01-02), a reference (ORD-2025-0001).
_CHAIN = re.compile(r"[A-Za-z0-9]+(?:[-/][A-Za-z0-9]+)+")
# A number below this many significant digits is not taken out of prose. 100 or
# 12 is in every sentence; replacing each one would corrupt what an auditor reads.
NUMBER_MIN_DIGITS = 4

# A doctype named anywhere in a result's JSON text, for a result whose call is unknown.
_DOCTYPE = re.compile(r'"doctype"\s*:\s*"((?:[^"\\]|\\.)*)"')


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
		changed = _changed_field_is_sensitive(value, keys)
		clean = {}
		for key, item in value.items():
			if isinstance(key, str) and (key.lower() in keys or (changed and key in CHANGE_VALUE_KEYS)):
				_collect(item, secrets)
				clean[key] = REDACTED
				continue
			clean[key] = _walk(item, keys, secrets)
		return clean
	if isinstance(value, (list, tuple)):
		return [_walk(item, keys, secrets) for item in value]
	return value


def _changed_field_is_sensitive(value: dict, keys: set[str]) -> bool:
	"""Whether this is a version change of a sensitive field.

	A version names the field as a value — `{"field": "iban", "from": ..., "to":
	...}` — so a walk by key alone never sees it. A child-row change is named
	`items[0].iban`; the field is the part after the last dot.
	"""
	field = value.get("field")
	if not isinstance(field, str) or not any(key in value for key in CHANGE_VALUE_KEYS):
		return False
	return field.rsplit(".", 1)[-1].lower() in keys


def _collect(value: Any, secrets: set[str]) -> None:
	"""Every value under a redacted key, as text, so prose can be scrubbed of it later.

	Numbers count: a salary is a secret whatever its type. A value wrapped as
	untrusted text is also kept without its wrapper, because that is the form a
	model repeats it in.
	"""
	if isinstance(value, bool) or value is None:
		return
	if isinstance(value, str):
		secrets.add(value)
		unwrapped = _UNTRUSTED.fullmatch(value)
		if unwrapped:
			secrets.add(unwrapped.group(1))
	elif isinstance(value, (int, float)):
		secrets.add(str(value))
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
	numbers = {number for number in map(_number, secrets) if number is not None}
	for secret in secrets:
		if len(secret) >= SCRUB_MIN_LENGTH and not _NUMERIC.fullmatch(secret) and secret in text:
			text = text.replace(secret, REDACTED)
	if numbers:
		text = _scrub_numbers(text, numbers)
	return text


def _number(secret: str) -> Decimal | None:
	"""A held-back value as the number it is, when it is one worth scrubbing."""
	if not _NUMERIC.fullmatch(secret):
		return None
	try:
		value = abs(Decimal(secret.replace(",", "")))
	except InvalidOperation:
		return None
	digits = format(value.normalize(), "f").replace(".", "").lstrip("0")
	return value if len(digits) >= NUMBER_MIN_DIGITS else None


def _scrub_numbers(text: str, numbers: set[Decimal]) -> str:
	"""Prose with every number equal to a held-back one taken out of it.

	Compared by value, so the form does not matter: 98765, 98765.00, 98,765.00,
	AED98765, 98765/month, amount:98765. A number is kept only where it is part
	of something else — a longer number (12025, 2025.5), or a date or reference
	joined by hyphens or slashes (2025-01-02, ORD-2025-0001) — see `_in_chain`.
	"""
	chains = [match.span() for match in _CHAIN.finditer(text)]
	out: list[str] = []
	copied = 0
	for match in _NUMBER_IN_TEXT.finditer(text):
		whole, fraction = match.group(1), match.group(2)
		value = Decimal(whole.replace(",", "") + (f".{fraction}" if fraction else ""))
		if value not in numbers or _in_chain(text, match.span(), chains):
			continue
		out.append(text[copied : match.start()])
		out.append(REDACTED)
		copied = match.end()
	out.append(text[copied:])
	return "".join(out)


def _in_chain(text: str, span: tuple[int, int], chains: list[tuple[int, int]]) -> bool:
	"""Whether a number is one part of a date or a reference, and not a value.

	Three or more parts joined by hyphens or slashes is one: 2025-01-02,
	02/01/2025, ORD-2025-0001. Two parts are not, because two parts are how a
	value is written too — a range (98765-99000), an amount and its unit
	(98765/month), a currency and an amount (AED-98765). Those are held back.
	"""
	start, end = span
	for chain_start, chain_end in chains:
		if chain_start <= start < chain_end or chain_start < end <= chain_end:
			return len(re.split(r"[-/]", text[chain_start:chain_end])) >= 3
	return False


def scrub_values(value: Any, secrets: set[str]) -> Any:
	"""A structure with every string in it scrubbed. Keys, numbers and shape stay.

	For JSON that was already redacted by key: a value-scrub of its serialised
	text would also hit bare numbers and break the JSON, so only the strings in it
	are prose to clean.
	"""
	if not secrets:
		return value
	if isinstance(value, str):
		return scrub(value, secrets)
	if isinstance(value, dict):
		return {key: scrub_values(item, secrets) for key, item in value.items()}
	if isinstance(value, (list, tuple)):
		return [scrub_values(item, secrets) for item in value]
	return value


def body_withheld(payload: dict) -> dict:
	"""An event that could not be redacted: what it was and when, without its body."""
	kept = ("type", "seq", "toolCallId", "toolName")
	return {key: payload[key] for key in kept if key in payload}


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
		# Tool-call id -> (the text the model was handed, the text the log keeps).
		self.results: dict[str, tuple[str, str]] = {}
		# Set once an event or a result had to be withheld. Its values were never
		# learned, so nothing the model writes after it can be scrubbed of them:
		# that prose is withheld too. Fail closed.
		self.blind = False

	def withhold(self, payload: dict) -> dict:
		"""An event that could not be redacted, stored without its body."""
		self.blind = True
		return body_withheld(payload)

	def record_result(self, call_id: str, args: Any, result: Any, raw_text: str, serialise: Any) -> None:
		"""Redact a tool's result while it is still a structure.

		The text the model is handed is cut at its limit, and a cut JSON string can
		no longer be redacted by key with certainty. So the executor hands the result
		over before it is serialised: it is redacted here and serialised the same
		way, and the log keeps that text wherever the model's text appears.
		"""
		keys = sensitive_keys(args)
		clean, secrets = redact(result, keys)
		self._remember(call_id, keys, secrets)
		_, known = self._call(call_id)
		self.results[call_id] = (raw_text, serialise(scrub_values(clean, known)))

	def hold_back_result(self, call_id: str, raw_text: str) -> None:
		"""A result that could not be redacted is stored as the marker alone."""
		self.blind = True
		self.results[call_id] = (raw_text, REDACTED)

	def scrub(self, text: str | None) -> str | None:
		"""Prose with every value held back in this run taken out of it."""
		if self.blind and text:
			return REDACTED
		return scrub(text, self.secrets)

	def event(self, payload: dict) -> dict:
		"""One event as it may be stored."""
		event = copy.deepcopy(payload)
		kind = event.get("type")

		if kind == "tool_execution_start":
			event["args"] = self._arguments(event.get("toolCallId"), event.get("args"))
		elif kind == "tool_execution_update":
			event["args"] = self._arguments(event.get("toolCallId"), event.get("args"))
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
		keys = self._result_keys(call_id, result)
		_, known = self._call(call_id)
		raw_text, stored_text = self.results.get(call_id) or (None, None)
		found: set[str] = set()
		if "details" in result:
			details, details_secrets = redact(result["details"], keys)
			result["details"] = scrub_values(details, (known | details_secrets) - {REDACTED})
			found |= details_secrets
		blocks = result.get("content")
		if isinstance(blocks, list):
			for block in blocks:
				if not isinstance(block, dict) or not isinstance(block.get("text"), str):
					continue
				if raw_text is not None and block["text"] == raw_text:
					block["text"] = stored_text
					continue
				block["text"], text_secrets = redact_json_text(block["text"], keys, known)
				found |= text_secrets
		self._remember(call_id, keys, found)
		return result

	def _result_keys(self, call_id: Any, result: dict) -> set[str]:
		"""The keys to redact a result by.

		Known from the call's arguments when the log still holds them. When it does
		not — the call was dropped from the middle of a long log — the doctype the
		result itself names is the next best answer, and the fallback names the last.
		"""
		if call_id in self.calls:
			return self.calls[call_id][0]
		keys = set(FALLBACK_SENSITIVE_KEYS)
		for block in result.get("content") or []:
			text = block.get("text") if isinstance(block, dict) else None
			found = _DOCTYPE.search(text) if isinstance(text, str) else None
			if found:
				keys |= sensitive_keys({"doctype": json.loads(f'"{found.group(1)}"')})
		return keys

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
		self._scrub_blocks(blocks, self.secrets, self.blind)
		if isinstance(message.get("errorMessage"), str):
			message["errorMessage"] = self.scrub(message["errorMessage"])
		return message

	@staticmethod
	def _scrub_blocks(blocks: Any, secrets: set[str], blind: bool = False) -> None:
		if not isinstance(blocks, list) or not (secrets or blind):
			return
		for block in blocks:
			if not isinstance(block, dict):
				continue
			for field in ("text", "thinking"):
				if isinstance(block.get(field), str) and block[field]:
					block[field] = REDACTED if blind else scrub(block[field], secrets)


def redact_json_text(text: str, keys: set[str], known: set[str] = frozenset()) -> tuple[str, set[str]]:
	"""Text that holds a tool's JSON result, with sensitive values held back.

	A tool result reaches the model as JSON text, so it is redacted by key like
	any other result. Values already held back from the same call (`known`) are
	scrubbed out of the strings in it, never out of the text as a whole, so it
	stays the JSON it was. Text that is left unchanged keeps every byte it had.
	Text that does not parse — a result cut short at the model's limit — is
	scanned instead, see `_redact_json_scan`.
	"""
	try:
		parsed = json.loads(text)
	except ValueError:
		scanned, secrets = _redact_json_scan(text, keys)
		return scrub(scanned, (set(known) | secrets) - {REDACTED}), secrets
	if not isinstance(parsed, (dict, list)):
		return text, set()
	clean, secrets = redact(parsed, keys)
	clean = scrub_values(clean, (set(known) | secrets) - {REDACTED})
	if clean == parsed:
		return text, secrets
	return frappe.as_json(clean), secrets


def _field_value_is_sensitive(decoder: json.JSONDecoder, text: str, start: int, keys: set[str]) -> bool:
	try:
		field, _ = decoder.raw_decode(text, start)
	except ValueError:
		return False
	return isinstance(field, str) and field.rsplit(".", 1)[-1].lower() in keys


def _redact_json_scan(text: str, keys: set[str]) -> tuple[str, set[str]]:
	"""JSON text that does not parse, with the value of every sensitive key held back.

	It reads the text string by string, so a key is only ever a key and never a
	piece of some other value. A value under a sensitive key is held back whatever
	its type — string, number, object. A value the cut lands inside runs to the
	end of the text, so everything from its start is held back: fail closed.

	A version change names its field as a value, the way `_walk` reads it. The
	serialiser sorts keys, so `field` comes before the `from` and `to` it governs:
	once a sensitive `field` is read, the `from` and `to` after it are held back
	until the next `field`.
	"""
	if not keys:
		return text, set()
	decoder = json.JSONDecoder()
	secrets: set[str] = set()
	out: list[str] = []
	copied = at = 0
	changed_is_sensitive = False
	while True:
		at = text.find('"', at)
		if at < 0:
			break
		try:
			name, end = json.decoder.scanstring(text, at + 1)
		except ValueError:
			# The cut is inside a string. If it was a value, its key was handled.
			break
		colon = end
		while colon < len(text) and text[colon] in " \t\r\n":
			colon += 1
		if colon >= len(text) or text[colon] != ":":
			at = end
			continue
		start = colon + 1
		while start < len(text) and text[start] in " \t\r\n":
			start += 1
		if name == "field":
			changed_is_sensitive = _field_value_is_sensitive(decoder, text, start, keys)
		held = name.lower() in keys or (changed_is_sensitive and name in CHANGE_VALUE_KEYS)
		if not held:
			at = end
			continue
		out.append(text[copied:start])
		try:
			value, stop = decoder.raw_decode(text, start)
		except ValueError:
			# The value runs into the cut. None of it is kept.
			secrets.add(text[start:].strip().strip('"'))
			out.append(f'"{REDACTED}"{CUT_NOTE}')
			return "".join(out), secrets - {""}
		_collect(value, secrets)
		out.append(f'"{REDACTED}"')
		copied = at = stop
	out.append(text[copied:])
	return "".join(out), secrets
