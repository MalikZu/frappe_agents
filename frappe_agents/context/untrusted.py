"""The untrusted wrapper.

Anything a human typed — a comment, an email body, a description field — is
data the model reads, never instruction the model follows. It reaches the model
inside `<untrusted source="...">…</untrusted>`.

A wrapper is only worth something if the content cannot close it early, so every
`<untrusted` and `</untrusted` sequence inside the text is neutralised first.
"""

import re

# Fieldtypes whose value is free text a user authored. Data and Select values are
# short and structured; these are the ones long enough to hide an instruction.
UNTRUSTED_FIELDTYPES = frozenset(
	{
		"Text",
		"Small Text",
		"Long Text",
		"Text Editor",
		"HTML Editor",
		"Markdown Editor",
		"Code",
	}
)

_TAG = re.compile(r"<\s*/?\s*untrusted", re.IGNORECASE)
_SOURCE_UNSAFE = re.compile(r"[<>\"'\r\n\t]")
SOURCE_CHARS = 140


def wrap(text: str | None, source: str) -> str:
	"""Wrap authored text as untrusted content, naming where it came from."""
	body = "" if text is None else neutralise(str(text))
	return f'<untrusted source="{_source(source)}">{body}</untrusted>'


def neutralise(text: str) -> str:
	"""Defuse any untrusted tag inside the text so the wrapper cannot be closed early."""
	return _TAG.sub(lambda match: match.group(0).replace("<", "&lt;", 1), str(text))


def is_untrusted_fieldtype(fieldtype: str | None) -> bool:
	"""True when a field of this type holds text a user authored."""
	return fieldtype in UNTRUSTED_FIELDTYPES


def _source(source: str) -> str:
	cleaned = _SOURCE_UNSAFE.sub(" ", str(source or "")).strip()
	return cleaned[:SOURCE_CHARS] or "unknown"
