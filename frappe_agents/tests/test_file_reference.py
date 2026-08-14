# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""How a person may name a file, and what naming one is still not allowed to do.

`resolve_file` is identifier sugar: a File name, a link on this site and a bare
filename all mean the same row. So the tests come in two halves. The first says
the forms agree — every one of them lands on the same File. The second says the
sugar opened no door: a file on another host is not a file here, a name that
means several files is answered with all of them rather than a guess, and a file
this user may not read is refused however cleverly it is written.
"""

from unittest.mock import patch
from urllib.parse import urlsplit

import frappe
from frappe.utils import get_url

from frappe_agents.extraction.pipeline import EXTERNAL_REFERENCE, resolve_file
from frappe_agents.tests.fixtures import (
	DRAFT_USER,
	ORDER_CANCELLED,
	ORDER_DT,
	ORDER_LIVE,
	VAULT_DT,
	VAULT_RECORD,
	AgentTestCase,
	as_user,
	call_tool,
	make_pdf,
	make_pdf_attachment,
)

FOREIGN = "https://files.example.com/private/files/fa-cv.pdf"


def unique_pdf(label: str = "fa ref") -> bytes:
	"""Bytes nothing else in the site shares.

	Uniqueness is not decoration: frappe reuses the `file_url` of an identical blob,
	so two rows over the same bytes really do share one URL. That is the ambiguity
	these tests raise deliberately, and it must not turn up by accident anywhere else.
	"""
	return make_pdf(f"{label} {frappe.generate_hash(length=8)}")


def unique_pdf_attachment(
	doctype: str = ORDER_DT,
	name: str = ORDER_LIVE,
	file_name: str | None = None,
	content: bytes | None = None,
):
	return make_pdf_attachment(
		doctype,
		name,
		file_name=file_name or f"fa-cv-{frappe.generate_hash(length=6)}.pdf",
		content=content if content is not None else unique_pdf(),
	)


class TestFileReference(AgentTestCase):
	def setUp(self) -> None:
		super().setUp()
		self.content = unique_pdf("cv")
		self.file = unique_pdf_attachment(content=self.content)

	def twin(self, doctype: str = ORDER_DT, name: str = ORDER_CANCELLED):
		"""The same bytes under the same name on another record.

		This is what a second File row over one document really looks like: frappe
		dedupes on the content hash, so a file named differently gets a different
		name back and only the identical blob keeps both the name and the URL.
		"""
		return unique_pdf_attachment(doctype, name, file_name=self.file.file_name, content=self.content)

	def resolve(self, ref: str, user: str = DRAFT_USER):
		with as_user(user):
			return resolve_file(ref)

	def refuse(self, ref: str | None, user: str = DRAFT_USER) -> str:
		"""Resolve, and return the refusal. Not resolving is the test."""
		with as_user(user), self.assertRaises(Exception) as caught:
			resolve_file(ref)
		return str(caught.exception)

	def extract(self, ref: str):
		with patch("frappe.enqueue"):
			return call_tool(DRAFT_USER, "extract_document", {"file": ref, "target_doctype": ORDER_DT})

	# --- the forms agree -----------------------------------------------------

	def test_every_form_of_a_reference_finds_the_same_file(self):
		refs = (
			self.file.name,
			self.file.file_url,
			get_url(self.file.file_url),
			self.file.file_name,
		)

		for ref in refs:
			with self.subTest(ref=ref):
				self.assertEqual(self.resolve(ref).name, self.file.name)

	def test_a_link_that_arrives_percent_encoded_is_the_same_link(self):
		encoded = self.file.file_url.replace("/private/files/", "/private/%66iles/")

		self.assertEqual(self.resolve(encoded).name, self.file.name)

	def test_the_query_string_a_link_carries_is_not_part_of_the_file(self):
		self.assertEqual(self.resolve(f"{self.file.file_url}?v=2").name, self.file.name)

	def test_a_public_file_is_named_by_the_link_a_public_file_has(self):
		"""`/files/…` and `/private/files/…` are the same kind of reference."""
		public = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"fa-logo-{frappe.generate_hash(length=6)}.pdf",
				"attached_to_doctype": ORDER_DT,
				"attached_to_name": ORDER_LIVE,
				"is_private": 0,
				"content": unique_pdf("logo"),
			}
		)
		public.flags.ignore_permissions = True
		public.insert(ignore_permissions=True)

		self.assertTrue(public.file_url.startswith("/files/"))
		self.assertEqual(self.resolve(public.file_url).name, public.name)

	def test_the_tool_takes_a_link_where_it_used_to_take_a_name(self):
		payload, _ = self.extract(self.file.file_url)

		self.assertTrue(payload["ok"], payload["error"])
		self.assertEqual(
			frappe.db.get_value("Document Extraction", payload["result"]["extraction"], "source_file"),
			self.file.name,
		)

	# --- and open no door ----------------------------------------------------

	def test_a_file_on_another_host_is_not_a_file_here(self):
		self.assertIn(EXTERNAL_REFERENCE, self.refuse(FOREIGN))

	def test_a_host_that_only_reads_like_this_site_is_still_another_host(self):
		host = urlsplit(get_url()).hostname or "localhost"
		tricks = (
			f"https://{host}@files.example.com{self.file.file_url}",
			f"https://{host}.evil.example.com{self.file.file_url}",
			f"ftp://{host}{self.file.file_url}",
			"file:///etc/passwd",
			# No scheme at all is still a host, and still not this one.
			f"//files.example.com{self.file.file_url}",
		)

		for ref in tricks:
			with self.subTest(ref=ref):
				self.assertIn(EXTERNAL_REFERENCE, self.refuse(ref))

	def test_the_tool_refuses_a_link_to_somewhere_else_and_queues_nothing(self):
		payload, _ = self.extract(FOREIGN)

		self.assertFalse(payload["ok"])
		self.assertIn(EXTERNAL_REFERENCE, payload["error"])
		self.assertFalse(frappe.db.exists("Document Extraction", {"source_file": self.file.name}))

	def test_a_path_that_climbs_out_of_the_files_directory_finds_nothing(self):
		"""A reference is matched against stored URLs, never walked on disk."""
		for ref in ("/private/files/../../../etc/passwd", "/private/files/..%2f..%2fetc/passwd"):
			with self.subTest(ref=ref):
				self.assertIn("No such file", self.refuse(ref))

	def test_a_reference_two_files_answer_to_is_refused_with_both_names(self):
		"""Refused, and refused with the names — the model has to ask which was meant."""
		twin = self.twin()

		for ref in (self.file.file_name, self.file.file_url):
			with self.subTest(ref=ref):
				error = self.refuse(ref)
				self.assertIn(self.file.name, error)
				self.assertIn(twin.name, error)

	def test_a_file_the_user_may_not_read_is_refused_however_it_is_named(self):
		"""Nobody in the cast may read the vault. Naming its file differently changes nothing."""
		vaulted = unique_pdf_attachment(VAULT_DT, VAULT_RECORD)

		for ref in (vaulted.name, vaulted.file_url, get_url(vaulted.file_url), vaulted.file_name):
			with self.subTest(ref=ref):
				self.refuse(ref)

	def test_a_file_the_user_may_not_read_is_not_named_in_an_ambiguous_answer(self):
		"""The candidate list is read by a model, so it may only ever name readable files."""
		vaulted = self.twin(VAULT_DT, VAULT_RECORD)

		# Two files carry the name; one of them is not this user's to know about, so
		# the reference is not ambiguous to them and no answer mentions the other.
		resolved = self.resolve(self.file.file_name)

		self.assertEqual(resolved.name, self.file.name)
		self.assertNotEqual(resolved.name, vaulted.name)

	def test_a_folder_is_not_something_to_read(self):
		self.assertIn("folder", self.refuse("Home"))

	def test_a_file_whose_contents_live_elsewhere_is_still_not_readable_here(self):
		"""A File row is not the file: this one only holds a link to another host."""
		remote = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"fa-remote-{frappe.generate_hash(length=6)}.pdf",
				"attached_to_doctype": ORDER_DT,
				"attached_to_name": ORDER_LIVE,
				"file_url": "https://files.example.com/fa-remote.pdf",
			}
		)
		remote.flags.ignore_permissions = True
		remote.insert(ignore_permissions=True)

		for ref in (remote.name, remote.file_name):
			with self.subTest(ref=ref):
				self.assertIn("stored somewhere else", self.refuse(ref))

	def test_nothing_is_not_a_reference(self):
		for ref in ("   ", "", None):
			with self.subTest(ref=ref):
				self.assertIn("No file given", self.refuse(ref))


class TestFileReferenceProvenance(AgentTestCase):
	"""The anchor rule sits downstream of the resolver and did not move."""

	def test_a_link_to_a_loose_file_is_resolved_and_then_refused(self):
		loose = frappe.get_doc(
			{
				"doctype": "File",
				"file_name": f"fa-loose-{frappe.generate_hash(length=6)}.pdf",
				"is_private": 1,
				"content": make_pdf(f"loose {frappe.generate_hash(length=8)}"),
			}
		)
		loose.flags.ignore_permissions = True
		loose.insert(ignore_permissions=True)
		# Readable — they uploaded it — and still refused: a loose private file has no
		# provenance to check, whichever way it was named.
		frappe.db.set_value("File", loose.name, "owner", DRAFT_USER, update_modified=False)
		frappe.clear_document_cache("File", loose.name)

		with patch("frappe.enqueue"):
			payload, _ = call_tool(
				DRAFT_USER,
				"extract_document",
				{"file": loose.file_url, "target_doctype": ORDER_DT},
			)

		self.assertFalse(payload["ok"])
		self.assertIn("not attached to any record", payload["error"])
		self.assertFalse(frappe.db.exists("Document Extraction", {"source_file": loose.name}))
