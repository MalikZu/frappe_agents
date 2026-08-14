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

`resolve_file` sits here too, immediately above the permission check it hands
every answer to. Any tool that takes a file uses it, so that a File name, a URL
and a filename all mean the same thing, and so that turning what a person typed
into a File row happens once rather than once per tool.

Caps use pypdf, Pillow and filetype — all pinned frappe core dependencies, so this
adds nothing to install. There is no rasterisation anywhere: nothing in the frappe
environment can turn a PDF page into an image, so the document goes to the
provider as the document, or not at all.
"""

from io import BytesIO
from typing import Any
from urllib.parse import quote, unquote, urlsplit

import filetype
import frappe
from frappe import _
from frappe.utils import cint, flt, get_url, now_datetime, strip_html, today

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

# What a reference to something that is not in this site gets back. One sentence,
# because an agent repeating it to a person should be a sentence they can act on.
EXTERNAL_REFERENCE = "Only files already in this system can be read. Upload it or attach it first."

# How many File rows one reference may pull back before it is simply ambiguous.
MAX_FILE_CANDIDATES = 20


def queue_extraction(file_name: str, target_doctype: str, model_profile: str | None = None) -> str:
	"""Record one extraction request and queue it. Returns the extraction name.

	Every refusal happens here, in the request, where the caller gets a sentence
	back instead of a status field to go and read later. The job checks the same
	things again as the user it runs as — a queued row is not a promise.
	"""
	settings = frappe.get_cached_doc("Agent Settings")
	if not cint(settings.global_enabled):
		frappe.throw(_("The agent runtime is switched off."))

	file_doc = readable_file(file_name)
	target_doctype = validate_target(target_doctype)
	profile = pick_profile(model_profile)
	check_daily_cap(settings)
	# Reads the bytes and throws them away: the size, type and page caps should be
	# a sentence to the person who asked, not a Failed row they find later.
	read_source(file_doc, settings)

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


def resolve_file(ref: str) -> Any:
	"""The File a person meant, written any of the ways people write one.

	A File record name, a site-relative URL (`/private/files/CV.pdf`), a full URL
	whose host is this site's, or a stored filename that matches exactly one File.
	Anything on another host is refused: nothing here fetches anything, so a file
	that is not already in this system is not a file this system can read.

	This is identifier sugar and nothing else. Whatever it finds goes through
	`readable_file` exactly as a File name handed in directly would, and the callers
	that require provenance still require it afterwards. A reference the user may
	not read is refused, and one that names several Files is refused with their
	names, so the model asks which was meant instead of picking one.
	"""
	ref = ref.strip() if isinstance(ref, str) else ""
	if not ref:
		frappe.throw(_("No file given."))

	lookup = _file_lookup(ref)
	if lookup is None:
		return readable_file(ref)

	field, values = lookup
	names = _readable_files(field, values)
	if not names:
		# Not found and not readable answer the same way on purpose: a reference is a
		# guess, and "no such file" must not become a way to ask whether one exists.
		frappe.throw(_("No such file: {0}").format(ref))
	if len(names) > 1:
		frappe.throw(
			_("More than one file matches {0}. Say which one by File name: {1}.").format(
				ref, ", ".join(names)
			)
		)
	return readable_file(names[0])


def _file_lookup(ref: str) -> tuple[str, list[str]] | None:
	"""What to look a reference up by, or None when it is already a File name.

	The parsing is deliberately hostile-minded. A URL's host is read with
	`urlsplit().hostname`, which is the only reading of it that survives
	`https://this-site@evil.example/…`; a scheme that is not http(s) never reaches
	the host comparison at all; and every path ends as an exact match against a
	stored `file_url`, never as a path on disk — so a reference full of `..` finds
	nothing rather than climbing out of the files directory.
	"""
	if "://" in ref or ref.startswith("//"):
		split = urlsplit(ref)
		if (split.scheme or "").lower() not in ("http", "https"):
			frappe.throw(_(EXTERNAL_REFERENCE))
		host = (split.hostname or "").lower().rstrip(".")
		if not host or host not in _site_hosts():
			frappe.throw(_(EXTERNAL_REFERENCE))
		return "file_url", _ref_forms(split.path)

	if ref.startswith("/"):
		return "file_url", _ref_forms(urlsplit(ref).path)

	if frappe.db.exists("File", ref):
		return None
	return "file_name", _ref_forms(ref)


def _site_hosts() -> set[str]:
	"""Every name this site answers to, lowercased and without its port.

	`get_url` is what the site tells the world it is; `frappe.local.site` is what
	bench calls it, which is the host in most development setups. A reference on
	any other host is refused not because following it would be dangerous —
	nothing follows it — but because there is no File row behind it.
	"""
	hosts = set()
	for value in (get_url() or "", getattr(frappe.local, "site", "") or ""):
		host = urlsplit(value).hostname if "://" in value else value.split(":")[0]
		if host:
			hosts.add(host.strip().lower().rstrip("."))
	return hosts


def _ref_forms(value: str) -> list[str]:
	"""The forms one reference could be stored as, most literal first.

	Frappe unquotes `file_url` before it saves it, so a URL copied out of a browser
	arrives percent-encoded and matches nothing until it is decoded. The encoded
	form is tried too, because a file saved with a literal `%20` in its name is a
	different file from one saved with a space.
	"""
	forms = []
	value = (value or "").strip()
	decoded = unquote(value)
	for form in (value, decoded, quote(decoded, safe="/")):
		if form and form not in forms:
			forms.append(form)
	return forms


def _readable_files(field: str, values: list[str]) -> list[str]:
	"""Files matching the reference that this user may actually read.

	`get_list` narrows to what the user may list, and every row is then re-checked
	with `has_permission`, because `get_list` does not run File's own per-row hook —
	and that hook is the whole permission model for a private file. Filtering before
	the ambiguity message is the point: that list is shown to the model, so it may
	only ever name files the user could have opened themselves.
	"""
	names = frappe.get_list(
		"File",
		filters={field: ("in", values), "is_folder": 0},
		pluck="name",
		order_by="creation asc",
		limit_page_length=MAX_FILE_CANDIDATES,
	)
	return [name for name in names if frappe.has_permission("File", "read", doc=name)]


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

	record_fields(doc, {"status": STATUS_RUNNING, "error": None})
	publish_extraction(doc, status=STATUS_RUNNING)

	try:
		file_doc = readable_file(doc.source_file)
		target_doctype = validate_target(doc.target_doctype)
		source = read_source(file_doc, settings, doc)
		if not doc.model_profile:
			record_fields(doc, {"model_profile": pick_profile(None)})
	except Exception as exc:
		return _fail(doc, _text(exc))

	spec = build_extraction_schema(target_doctype, user)
	instructions = _instructions(target_doctype, file_doc, source)

	try:
		reply = _ask(doc, spec, source, file_doc, instructions)
	except ExtractionRefused as exc:
		# A refusal is an answer. It is recorded in the model's own words and never
		# re-asked: the second no costs the same as the first. It was billed, so its
		# tokens are added to the row like any other call's.
		record_fields(
			doc,
			{
				"tokens_in": cint(doc.tokens_in) + cint(getattr(exc, "tokens_in", 0)),
				"tokens_out": cint(doc.tokens_out) + cint(getattr(exc, "tokens_out", 0)),
				"parser_engine": getattr(exc, "engine", None),
			},
		)
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

	record_fields(
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

	record_fields(doc, {"created_doc": created, "status": STATUS_NEEDS_REVIEW})
	publish_extraction(
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
		record_fields(doc, {"tokens_in": tokens_in, "tokens_out": tokens_out})

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
				record_fields(doc, {"pages_capped": 1})
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
	_apply_site_defaults(doc)
	doc.flags.ignore_mandatory = True
	doc.insert()
	return doc.name


def _apply_site_defaults(doc: Any) -> None:
	"""Site context the document cannot state about itself.

	A form fills company, cost centre and their kin from the user's defaults; a
	server-side insert gets none of that, and a target doctype's validate logic
	may refuse outright without them. For an empty Link field whose fieldname has
	a user or global default naming a real record, take the default. An extracted
	value is never overwritten — this fills holes the document could not speak to.
	"""
	for df in doc.meta.get("fields", {"fieldtype": "Link"}):
		if doc.get(df.fieldname):
			continue
		default = frappe.defaults.get_user_default(df.fieldname) or frappe.defaults.get_global_default(
			df.fieldname
		)
		if default and frappe.db.exists(df.options, default):
			doc.set(df.fieldname, default)


def _fail(doc: Any, message: str) -> None:
	record_fields(doc, {"status": STATUS_FAILED, "error": (message or "")[:ERROR_CHARS]})
	publish_extraction(doc, status=STATUS_FAILED, error=(message or "")[:ERROR_CHARS])


def record_fields(doc: Any, values: dict) -> None:
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


def publish_extraction(doc: Any, **payload: Any) -> None:
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
