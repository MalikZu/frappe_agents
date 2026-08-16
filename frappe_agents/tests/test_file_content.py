"""Reading a file's bytes across two framework versions.

`File.get_content` is not the same callable on v15 and v16: v16 takes an
`encodings` list and can return a str, v15 takes no arguments and always returns
bytes. Everything that reads a document goes through `file_bytes`, so these pin
both shapes — the wrong one is a TypeError on every extraction and every read.
"""

from typing import Any

from frappe_agents.extraction.pipeline import file_bytes
from frappe_agents.tests.compat import IntegrationTestCase

PAYLOAD = b"%PDF-1.4 not really, but bytes"


class OldFile:
	"""v15: no keyword, always bytes."""

	def get_content(self) -> bytes:
		return PAYLOAD


class NewFile:
	"""v16: takes encodings, and hands back bytes when asked not to decode."""

	def __init__(self) -> None:
		self.called_with: Any = "never called"

	def get_content(self, encodings=None) -> bytes:
		self.called_with = encodings
		return PAYLOAD


class DecodingFile:
	"""A framework that decoded anyway. The bytes still have to survive."""

	def get_content(self, encodings=None) -> str:
		return PAYLOAD.decode("utf-8", "surrogateescape")


class TestFileBytes(IntegrationTestCase):
	def test_a_get_content_without_the_keyword_is_still_read(self):
		self.assertEqual(file_bytes(OldFile()), PAYLOAD)

	def test_a_get_content_with_the_keyword_is_asked_not_to_decode(self):
		doc = NewFile()
		self.assertEqual(file_bytes(doc), PAYLOAD)
		self.assertEqual(doc.called_with, [], "the empty list is what says 'do not decode'")

	def test_a_decoded_answer_is_returned_to_bytes(self):
		self.assertEqual(file_bytes(DecodingFile()), PAYLOAD)

	def test_the_bytes_are_never_a_str(self):
		for doc in (OldFile(), NewFile(), DecodingFile()):
			self.assertIsInstance(file_bytes(doc), bytes)
