"""The extraction job: a file in, a reviewed draft out, and nothing else.

`run_extraction` is the RQ job. It runs as the person who asked for it, which is
what makes every permission check in here mean something, and it never raises: a
failure is recorded on the Document Extraction row where a human can read it.

The order of the steps is the design. Everything that can refuse does so before
the model is called — permission on the file, permission to create the target
doctype, the size cap, the page cap, the daily cap — so a refusal costs nothing
and a hostile file never reaches a provider on someone else's authority.

**The rule this file exists to keep: nothing in here writes a sensitive field.**
The draft is created from `gate.withhold`'s first return value, which cannot
contain one. The extracted sensitive values go onto the extraction row, flagged,
and the only code in the app that can write them into a document is
`api.apply_extraction`, from a list of fieldnames a person named by hand.

Caps use pypdf, Pillow and filetype — all pinned frappe core dependencies, so this
adds nothing to install. There is no rasterisation anywhere: nothing in the frappe
environment can turn a PDF page into an image, so the document goes to the
provider as the document, or not at all.
"""

from io import BytesIO
from typing import Any

import filetype
import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime, strip_html, today

from frappe_agents.extraction import gate, resolve
from frappe_agents.extraction.schema import build_extraction_schema, normalise_values, read_field_notes
from frappe_agents.runner.providers import (
	PDF_ENGINE_NATIVE,
	PDF_MEDIA_TYPE,
	ExtractionRefused,
	ProviderError,
	call_model_extract,
)

EXTRACTION = "Document Extraction"
EVENT = "frappe_agents:extraction_update"

STATUS_PENDING = "Pending"
STATUS_RUNNING = "Running"
STATUS_NEEDS_REVIEW = "Needs Review"
STATUS_ACCEPTED = "Accepted"
STATUS_DISCARDED = "Discarded"
STATUS_FAILED = "Failed"

FORBIDDEN_USERS = ("Administrator", "Guest")

DEFAULT_MAX_PAGES = 20
DEFAULT_MAX_FILE_MB = 10
DEFAULT_DAILY_LIMIT = 50

# Anthropic's ceiling. Above it the request is rejected, and a downscale would be
# us editing evidence, so it is a refusal.
MAX_IMAGE_PIXELS = 8000

JOB_TIMEOUT = 900
ERROR_CHARS = 500


def queue_extraction(file_name: str, target_doctype: str, model_profile: str | None = None) -> str:
	"""Record one extraction request and queue it. Returns the extraction name.

	Everything cheap and refusable happens here, in the request, where the caller
	gets a sentence back instead of a status field to go and read later.
	"""
	settings = frappe.get_cached_doc("Agent Settings")
	if not cint(settings.global_enabled):
		frappe.throw(_("The agent runtime is switched off."))

	file_doc = readable_file(file_name)
	target_doctype = validate_target(target_doctype)
	profile = pick_profile(model_profile)
	check_daily_cap(settings)

	extraction = frappe.get_doc(
		{
			"doctype": EXTRACTION,
			"source_file": file_doc.name,
			"target_doctype": target_doctype,
			"status": STATUS_PENDING,
			"model_profile": profile,
		}
	)
	extraction.insert()

	attach_source_copy(file_doc, extraction.name)

	frappe.enqueue(
		"frappe_agents.extraction.pipeline.run_extraction",
		queue="long",
		timeout=JOB_TIMEOUT,
		enqueue_after_commit=True,
		extraction=extraction.name,
	)

	return extraction.name


def readable_file(file_name: str) -> Any:
	"""The File, if this user may read it. Roles alone grant nothing on a File."""
	if not file_name or not frappe.db.exists("File", file_name):
		frappe.throw(_("No such file: {0}").format(file_name))

	file_doc = frappe.get_doc("File", file_name)
	if not file_doc.has_permission("read"):
		raise frappe.PermissionError(
			_("You are not allowed to read the file {0}.").format(file_doc.file_name or file_name)
		)
	if cint(file_doc.is_folder):
		frappe.throw(_("{0} is a folder, not a document.").format(file_doc.file_name or file_name))
	if file_doc.is_remote_file:
		frappe.throw(
			_("{0} is stored somewhere else, so its contents cannot be read here.").format(
				file_doc.file_name or file_name
			)
		)
	return file_doc


def validate_target(target_doctype: str) -> str:
	"""The doctype, if this user may create one. Checked before any model is called."""
	target_doctype = (target_doctype or "").strip()
	if not target_doctype or not frappe.db.exists("DocType", target_doctype):
		frappe.throw(_("No such DocType: {0}").format(target_doctype))

	meta = frappe.get_meta(target_doctype)
	if cint(meta.istable):
		frappe.throw(_("{0} is a child table. Extract into the parent doctype.").format(target_doctype))
	if cint(meta.issingle):
		frappe.throw(_("{0} is a settings document, not something to extract into.").format(target_doctype))

	if not frappe.has_permission(target_doctype, "create"):
		raise frappe.PermissionError(
			_("You are not allowed to create {0}, so there is nothing to extract into.").format(
				target_doctype
			)
		)
	return target_doctype


def pick_profile(model_profile: str | None) -> str:
	"""The model profile to extract with, or a question the caller can answer."""
	if model_profile:
		profile = frappe.get_cached_doc("LLM Model Profile", model_profile)
		if not cint(profile.enabled):
			frappe.throw(_("Model profile {0} is disabled.").format(profile.name))
		_check_profile_roles(profile)
		return profile.name

	enabled = frappe.get_all("LLM Model Profile", filters={"enabled": 1}, pluck="name")
	allowed = []
	for name in enabled:
		profile = frappe.get_cached_doc("LLM Model Profile", name)
		try:
			_check_profile_roles(profile)
		except frappe.PermissionError:
			continue
		allowed.append(name)

	if not allowed:
		frappe.throw(_("There is no model profile you are allowed to extract with."))
	if len(allowed) > 1:
		frappe.throw(_("Say which model profile to extract with: {0}.").format(", ".join(sorted(allowed))))
	return allowed[0]


def _check_profile_roles(profile: Any) -> None:
	allowed = {row.role for row in (profile.get("allowed_roles") or []) if row.role}
	if allowed and not (allowed & set(frappe.get_roles())):
		raise frappe.PermissionError(
			_("You are not allowed to use the model profile {0}.").format(profile.name)
		)


def check_daily_cap(settings: Any) -> None:
	limit = cint(settings.get("extractions_per_user_per_day")) or DEFAULT_DAILY_LIMIT
	used = frappe.db.count(EXTRACTION, {"owner": frappe.session.user, "creation": (">=", today())})
	if used >= limit:
		frappe.throw(
			_("You have started {0} extractions today, which is the daily limit. Try again tomorrow.").format(
				used
			)
		)


def attach_source_copy(file_doc: Any, extraction: str) -> None:
	"""Point a File row at the same bytes, attached to the extraction.

	Without this the reviewer's preview pane is a blank rectangle: a private file is
	readable by its owner, not by whoever reviews it. The copy reuses `file_url`, so
	nothing is written to disk twice, and it turns "may this user read this file"
	into "may this user read this extraction" — which is the question we can answer.

	`ignore_permissions` is safe here only because `readable_file` checked the
	source as the requesting user first. Keep the two together.
	"""
	if file_doc.attached_to_doctype == EXTRACTION and file_doc.attached_to_name == extraction:
		return
	try:
		file_doc.create_attachment_copy(EXTRACTION, extraction, ignore_permissions=True)
	except Exception:
		# A missing preview is a worse review, not a failed extraction.
		frappe.logger("frappe_agents").warning(
			f"could not attach the source file to extraction {extraction}", exc_info=True
		)


def run_extraction(extraction: str) -> None:
	"""RQ job: extract one document. Never raises."""
	original_user = frappe.session.user
	doc = None
	try:
		doc = frappe.get_doc(EXTRACTION, extraction)
		doc.flags.ignore_permissions = True
		_run(doc)
	except Exception as exc:
		frappe.logger("frappe_agents").error(f"extraction {extraction} failed", exc_info=True)
		if doc is not None:
			_fail(doc, str(exc) or exc.__class__.__name__)
	finally:
		frappe.set_user(original_user)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser


def _run(doc: Any) -> None:
	if doc.status != STATUS_PENDING:
		# A requeued job must not extract a document a person already reviewed.
		return

	user = doc.owner
	if not user or user in FORBIDDEN_USERS:
		return _fail(doc, f"Refusing to extract as {user or 'no user'}.")
	if not cint(frappe.get_cached_value("User", user, "enabled") or 0):
		return _fail(doc, f"The user {user} is disabled or missing.")

	settings = frappe.get_cached_doc("Agent Settings")
	if not cint(settings.global_enabled):
		return _fail(doc, "The agent runtime is switched off.")

	# From here on every check runs as the person who asked, not as the worker.
	frappe.set_user(user)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser

	_set(doc, {"status": STATUS_RUNNING, "error": None})
	_publish(doc, status=STATUS_RUNNING)

	try:
		file_doc = readable_file(doc.source_file)
		target_doctype = validate_target(doc.target_doctype)
		source = read_source(file_doc, settings, doc)
	except Exception as exc:
		return _fail(doc, _text(exc))

	spec = build_extraction_schema(target_doctype, user)
	instructions = _instructions(target_doctype, file_doc, source)

	try:
		reply = _ask(doc, spec, source, file_doc, instructions)
	except ExtractionRefused as exc:
		# A refusal is an answer. It is recorded in the model's own words and never
		# re-asked: the second no costs the same as the first.
		return _fail(doc, _text(exc))
	except ProviderError as exc:
		return _fail(doc, _text(exc))

	if reply.get("data") is None:
		return _fail(doc, reply.get("parse_error") or "The model did not return usable JSON.")

	values = normalise_values(spec, reply["data"])
	notes = read_field_notes(spec, reply["data"])

	resolution = resolve.resolve_links(target_doctype, values, spec)

	# The split. `draft_values` is what may be written; `flags` is what a person has
	# to confirm first. Only the first of these is ever handed to a document.
	#
	# The split happens before resolution is applied, so a sensitive value that did
	# not resolve to a record is still flagged with what the document actually said.
	# Dropping it first would delete the evidence.
	draft_values, flags = gate.withhold(spec, values, resolution.get("resolved"))
	draft_values = resolve.apply_resolution(draft_values, resolution, spec)
	duplicates = gate.find_duplicates(
		target_doctype, file_doc.content_hash, draft_values, spec, exclude=doc.name
	)

	_set(
		doc,
		{
			"extracted_json": frappe.as_json(draft_values),
			"field_confidence": frappe.as_json(notes),
			"sensitive_flags": frappe.as_json(flags),
			"duplicate_flags": frappe.as_json(duplicates),
			"link_candidates": frappe.as_json(resolution.get("candidates") or {}),
			"parser_engine": reply.get("engine"),
		},
	)

	try:
		created = _create_draft(target_doctype, draft_values)
	except Exception as exc:
		# The extraction itself worked; the draft did not. Everything above is kept
		# so the reviewer can see what the document said and why it would not save.
		return _fail(doc, _("Could not create the draft: {0}").format(_text(exc)))

	_set(doc, {"created_doc": created, "status": STATUS_NEEDS_REVIEW})
	_publish(
		doc,
		status=STATUS_NEEDS_REVIEW,
		created_doc=created,
		sensitive_count=len(flags),
		mismatch_count=sum(1 for flag in flags.values() if cint(flag.get("mismatch"))),
	)


def _ask(doc: Any, spec: dict, source: dict, file_doc: Any, instructions: str) -> dict:
	"""One extraction call, and at most one more if the reply was not usable JSON."""
	profile = doc.model_profile
	tokens_in = 0
	tokens_out = 0
	reply: dict = {}

	for attempt in (1, 2):
		text = instructions
		if attempt == 2:
			text = (
				f"{instructions}\n\nYour previous reply could not be used: "
				f"{reply.get('parse_error')}. Reply with JSON that matches the schema exactly."
			)

		reply = call_model_extract(
			profile,
			source["content"],
			source["media_type"],
			file_doc.file_name or "",
			spec["schema"],
			text,
			pdf_engine=_pdf_engine(),
			schema_name=f"{spec['doctype']}_extraction",
		)
		tokens_in += cint(reply.get("tokens_in"))
		tokens_out += cint(reply.get("tokens_out"))
		# Tokens are recorded whether or not the answer was usable — a failed
		# extraction still cost money and still belongs in the budget.
		_set(doc, {"tokens_in": tokens_in, "tokens_out": tokens_out})

		if reply.get("data") is not None:
			break

	return reply


def _pdf_engine() -> str:
	"""Never the router's default: which engine parsed the PDF is a security fact.

	A non-native engine hands the model an OCR text layer while the reviewer reads
	the rendered page, and text invisible on the page is exactly how a document
	lies to one of them.
	"""
	settings = frappe.get_cached_doc("Agent Settings")
	return settings.get("pdf_parser_engine") or PDF_ENGINE_NATIVE


def read_source(file_doc: Any, settings: Any, doc: Any | None = None) -> dict:
	"""The document bytes, its real media type, and the caps checked against them."""
	content = file_doc.get_content(encodings=[])
	if not isinstance(content, bytes):
		# get_content decodes anything that survives a text codec, and the round trip
		# is lossy — a corrupted image would reach the model as plausible garbage.
		content = content.encode("utf-8", "surrogateescape")

	max_mb = cint(settings.get("max_extraction_file_mb")) or DEFAULT_MAX_FILE_MB
	size_mb = len(content) / (1024 * 1024)
	if size_mb > max_mb:
		# Measured on the bytes, never on File.file_size: that field is overwritten
		# from the request's own form data on every save.
		frappe.throw(
			_("This file is {0} MB and extraction is limited to {1} MB.").format(flt(size_mb, 1), max_mb)
		)

	kind = filetype.match(content)
	media_type = (kind.mime if kind else "") or ""

	pages = 0
	if media_type == PDF_MEDIA_TYPE:
		pages = _pdf_pages(content)
		max_pages = cint(settings.get("max_extraction_pages")) or DEFAULT_MAX_PAGES
		if pages > max_pages:
			if doc is not None:
				_set(doc, {"pages_capped": 1})
			frappe.throw(
				_("This PDF has {0} pages and extraction is limited to {1}.").format(pages, max_pages)
			)
	elif media_type.startswith("image/"):
		_check_image(content)
	else:
		frappe.throw(
			_("Extraction reads PDFs and images. {0} is {1}.").format(
				file_doc.file_name or file_doc.name, media_type or _("of an unrecognised type")
			)
		)

	return {"content": content, "media_type": media_type, "pages": pages}


def _pdf_pages(content: bytes) -> int:
	from pypdf import PdfReader
	from pypdf.errors import PdfReadError

	try:
		reader = PdfReader(BytesIO(content))
	except PdfReadError as exc:
		frappe.throw(_("This PDF could not be read ({0}).").format(str(exc)))
	if reader.is_encrypted:
		frappe.throw(_("This PDF is password protected, so its pages cannot be read."))
	return len(reader.pages)


def _check_image(content: bytes) -> None:
	from PIL import Image

	try:
		width, height = Image.open(BytesIO(content)).size
	except Exception:
		frappe.throw(_("This image could not be read."))
	if width > MAX_IMAGE_PIXELS or height > MAX_IMAGE_PIXELS:
		frappe.throw(
			_("This image is {0}x{1}; extraction is limited to {2} pixels on a side.").format(
				width, height, MAX_IMAGE_PIXELS
			)
		)


def _instructions(target_doctype: str, file_doc: Any, source: dict) -> str:
	"""What the model is asked. Fixed text — no caller and no agent writes this.

	The tool that queues an extraction takes a file and a doctype and nothing else,
	so there is no free-text channel from a model into the extraction call.
	"""
	pages = f" It has {source['pages']} page(s)." if source.get("pages") else ""
	return (
		f"Extract this document into the schema. It will become a draft "
		f"{target_doctype} that a person reviews before anything is committed.{pages}\n"
		f"Read only what is printed. Leave anything you cannot find empty rather than "
		f"filling it in from what usually goes there."
	)


def _create_draft(target_doctype: str, values: dict) -> str:
	"""Insert the draft as the requesting user, with no ignore_permissions anywhere.

	Mandatory fields are allowed to be missing and only those: a draft with a hole
	in it is what the review is for, and the alternative is the model filling the
	hole with something plausible. Permissions and validation are untouched.
	"""
	doc = frappe.new_doc(target_doctype)
	doc.update(values)
	doc.flags.ignore_mandatory = True
	doc.insert()
	return doc.name


def _fail(doc: Any, message: str) -> None:
	_set(doc, {"status": STATUS_FAILED, "error": (message or "")[:ERROR_CHARS]})
	_publish(doc, status=STATUS_FAILED, error=(message or "")[:ERROR_CHARS])


def _set(doc: Any, values: dict) -> None:
	"""Write straight to the row, skipping any field this site does not have yet."""
	meta = frappe.get_meta(EXTRACTION)
	writable = {key: value for key, value in values.items() if meta.has_field(key)}
	missing = sorted(set(values) - set(writable))
	if missing:
		frappe.logger("frappe_agents").warning(
			f"{EXTRACTION} has no field(s) {', '.join(missing)}; not recorded"
		)
	if writable:
		doc.flags.ignore_permissions = True
		doc.db_set(writable, update_modified=True)


def _publish(doc: Any, **payload: Any) -> None:
	data = {
		"type": "extraction_update",
		"extraction": doc.name,
		"target_doctype": doc.target_doctype,
		"at": str(now_datetime()),
	}
	data.update(payload)
	frappe.publish_realtime(EVENT, data, user=doc.owner)


def _text(exc: Exception) -> str:
	return strip_html(str(exc) or "").strip() or exc.__class__.__name__
