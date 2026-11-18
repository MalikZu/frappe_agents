"""Fill from file: an extraction whose result fills a form somebody has open.

This is the extraction pipeline with a different last step, and nothing else. The
request goes through `queue_extraction` with every gate it has — file permission,
create or write permission, size, page and daily caps — and the job runs `_run`
as the person who clicked, up to the point where a Draft reading would insert a
document. A Fill Form reading stops there instead, at **Ready**, with the values
on its row. Nothing is inserted, saved or submitted on anybody's behalf.

Three calls make up the rest, all as the person who clicked and only for them:

* `start_fill` asks. Which model reads the file, and on which doctypes the button
  exists at all, is an Agent's to say: one with `show_in_forms` and an **Extract**
  rule on the doctype. That is the same record and the same rule an agent needs to
  extract in chat, so a site has one place that decides where documents may go.
* `fill_values` hands the values to the form. It re-asks every question a stale
  row could have the wrong answer to — status, age, permission on the doctype or
  draft, which fields are still writable, which linked records are still readable
  — because a reading done this morning is not a permission held now.
* `record_filled` is called after the person saves. It ties the reading to the
  document they saved, attaches the source file to it under their own permission,
  and records what became of each sensitive field, read from the saved document
  itself rather than from anything the browser says.

Sensitive values are handed to the form as information, never as values. The form
shows what the document printed and what the master record says, and the person
types the field or chooses to use the printed value. Saving goes through the
framework's own form save, so this is the one place a sensitive value reaches a
document without `api.apply_extraction` — which is why `record_filled` reads the
saved document back and stamps every flag.
"""

import re
from typing import Any

import frappe
from frappe import _
from frappe.utils import add_to_date, cint, get_datetime, now_datetime

from frappe_agents.access.grants import VERB_EXTRACT, grant_for, granted_targets, in_legacy_mode
from frappe_agents.extraction import gate
from frappe_agents.extraction.pipeline import (
	EXTRACTION,
	PURPOSE_FILL,
	STATUS_ACCEPTED,
	STATUS_DISCARDED,
	STATUS_FAILED,
	STATUS_PENDING,
	STATUS_READY,
	STATUS_RUNNING,
	publish_extraction,
	queue_extraction,
	readable_file,
	record_fields,
	resolve_file,
	stored_status,
	validate_target,
)
from frappe_agents.extraction.schema import build_extraction_schema, reviewed_values

# How long a reading may wait for its form. A person who walks away from the form
# leaves a Ready row behind; past this it is swept to Discarded, and a form that
# comes back to it is told to read the file again.
FILL_MAX_AGE_HOURS = 24

# A sensitive value inside a child row, keyed the way `gate.withhold` keys it.
SENSITIVE_ROW = re.compile(r"^(?P<table>[a-z_][a-z0-9_]*)\[(?P<index>\d+)\]\.(?P<field>[a-z_][a-z0-9_]*)$")

# What became of a sensitive field once the form was saved.
SAVED_EMPTY = "empty"
SAVED_PRINTED = "printed"
SAVED_TYPED = "typed"

STALE_NOTE = "Never tied to a saved document within {0} hours, so it was closed."

# The key a filled form carries on its document until it is saved. It names the
# reading, so the save itself can record it: see `stamp_on_save`.
TOKEN_KEY = "__fa_fill"


def extract_doctypes(agent: Any) -> list[str]:
	"""The doctypes this agent may fill forms of. Empty when it may fill none.

	Read by the desk boot, so it must stay cheap and never raise. An agent still on
	the legacy tool selection has no Extract rows to read, and is given none rather
	than all: a button on every form is not a default anyone chose.
	"""
	if not agent.get("model_profile") or in_legacy_mode(agent):
		return []
	return granted_targets(agent, VERB_EXTRACT)


def require_fill_grant(agent: Any, doctype: str) -> None:
	"""Refuse unless this agent is one a form may fill from, for this doctype."""
	if not cint(agent.get("show_in_forms")):
		frappe.throw(_("Agent {0} is not offered on forms.").format(agent.name))
	if not agent.get("model_profile"):
		frappe.throw(_("Agent {0} has no model profile to read files with.").format(agent.name))
	if not doctype or in_legacy_mode(agent) or not grant_for(agent, doctype).get(VERB_EXTRACT):
		raise frappe.PermissionError(
			_("Agent {0} has no access rule that lets it extract into {1}.").format(agent.name, doctype)
		)


def start_fill(agent: Any, file: str, doctype: str, docname: str | None = None) -> dict:
	"""Queue a reading of `file` for a form of `doctype`. The caller checked the agent."""
	doctype = (doctype or "").strip()
	require_fill_grant(agent, doctype)
	file_doc = resolve_file(file)

	name = queue_extraction(
		file_doc.name,
		doctype,
		model_profile=agent.model_profile,
		purpose=PURPOSE_FILL,
		target_name=(docname or "").strip() or None,
		agent=agent.name,
	)
	return {"extraction": name, "status": STATUS_PENDING, "file_name": file_doc.file_name}


def fill_row(name: str) -> Any:
	"""The reading, if it is a fill and it is this person's.

	Owner only — not System Manager as on the review screen. The links in it were
	resolved under the owner's permissions, and the values are meant for the form
	the owner has open, not for anybody else's.
	"""
	# Missing and somebody else's answer alike, and name nobody: a reading's name
	# is not a way to learn who else has been reading files.
	if not name or frappe.db.get_value(EXTRACTION, name, "owner") != frappe.session.user:
		raise frappe.PermissionError(_("No such reading of yours: {0}").format(name))
	doc = frappe.get_doc(EXTRACTION, name)
	if doc.get("purpose") != PURPOSE_FILL:
		frappe.throw(_("{0} is not a Fill from file reading.").format(name))
	return doc


def fill_values(doc: Any) -> dict:
	"""Where the reading is, and once it is Ready, what the form may be filled with."""
	status = doc.status
	answer: dict[str, Any] = {"extraction": doc.name, "status": status}
	if status in (STATUS_PENDING, STATUS_RUNNING):
		return answer
	if status == STATUS_FAILED:
		answer["error"] = doc.error or _("The reading failed.")
		return answer
	if status != STATUS_READY:
		frappe.throw(_("This reading is {0}. Read the file again to fill the form.").format(status))
	if is_stale(doc):
		frappe.throw(
			_("This reading is more than {0} hours old. Read the file again.").format(FILL_MAX_AGE_HOURS)
		)

	doctype = validate_target(doc.target_doctype, doc.get("target_name"))
	spec = build_extraction_schema(doctype, frappe.session.user)
	flags = _parsed(doc.sensitive_flags)

	values, ignored = reviewed_values(spec, _parsed(doc.extracted_json), flags)
	dropped: dict[str, dict] = {}
	values = _readable_links(spec, values, dropped)

	candidates = _readable_candidates(_parsed(doc.link_candidates))
	candidates.update(dropped)

	answer.update(
		{
			"doctype": doctype,
			"target_name": doc.get("target_name") or None,
			"values": values,
			"sensitive": _sensitive_view(doctype, flags),
			"candidates": candidates,
			"notes": _parsed(doc.field_confidence),
			"duplicates": _readable_duplicates(doctype, _parsed(doc.duplicate_flags), doc.get("target_name")),
			"ignored": sorted(ignored),
			"source": _source(doc.source_file),
		}
	)
	return answer


def record_filled(doc: Any, docname: str, saved: Any = None) -> dict:
	"""Tie a Ready reading to the document the person saved from it.

	The saved document is read back here, as the person, and every sensitive flag
	is stamped from what it actually holds: left empty, the printed value, or a
	value they typed. The browser's word is not part of that record.
	"""
	# Locked: a sweep or a discard landing at the same moment waits, then finds the
	# row decided, or this request does.
	status = stored_status(doc)
	docname = (docname or "").strip()
	if status == STATUS_ACCEPTED and docname and doc.get("created_doc") == docname:
		# The save already recorded it (`stamp_on_save`); the form's own call after
		# the save is then only asking how it went.
		return _recorded(doc, docname)
	if status != STATUS_READY:
		frappe.throw(_("This reading is {0}, so it cannot be tied to a document.").format(status))

	doctype = doc.target_doctype
	target = doc.get("target_name")
	if target and docname != target:
		frappe.throw(_("This reading was for {0} {1}.").format(doctype, target))
	if not _may_record_against(doctype, docname, target):
		# Missing and not permitted answer alike.
		raise frappe.PermissionError(_("You are not allowed to change {0} {1}.").format(doctype, docname))

	saved = saved if saved is not None else frappe.get_doc(doctype, docname)
	if not target and (
		saved.owner != frappe.session.user or get_datetime(saved.creation) < get_datetime(doc.creation)
	):
		# A reading for a new form belongs to the document that form became, and to
		# nothing older or anybody else's.
		frappe.throw(_("{0} {1} was not created from this reading.").format(doctype, docname))

	flags = _stamp_saved(doctype, saved, _parsed(doc.sensitive_flags))
	record_fields(
		doc,
		{
			"status": STATUS_ACCEPTED,
			"created_doc": docname,
			"reviewed_by": frappe.session.user,
			"sensitive_flags": frappe.as_json(flags),
		},
	)
	doc.status = STATUS_ACCEPTED
	doc.created_doc = docname
	doc.sensitive_flags = frappe.as_json(flags)
	_attach_source(doc, doctype, docname)
	publish_extraction(doc, status=STATUS_ACCEPTED, created_doc=docname)
	return _recorded(doc, docname)


def _may_record_against(doctype: str, docname: str, target: str | None) -> bool:
	"""Whether this user may tie a reading to this document.

	A saved draft that was filled needs write on it, which the fill needed too. A
	new form's document needs no more than having been created by this person:
	a role may create a doctype without being allowed to change it afterwards,
	and recording what they just created is not changing it. Anything else they
	did not create must at least be readable, so that "not yours" is not a way to
	learn that a record exists.
	"""
	if not docname or not frappe.db.exists(doctype, docname):
		return False
	if target:
		return bool(frappe.has_permission(doctype, "write", doc=docname))
	if frappe.db.get_value(doctype, docname, "owner") == frappe.session.user:
		return True
	return bool(frappe.has_permission(doctype, "read", doc=docname))


def stamp_on_save(doc: Any, method: str | None = None) -> None:
	"""`on_update` for every doctype: record a fill when the form saved from it.

	The form puts the reading's name on its document as `__fa_fill` when it fills
	it, and the key travels with the save. So the save itself records the reading,
	whatever the browser does next: a closed tab, a failed request, or a person
	who looked at another record and came back. Every other save pays one
	attribute lookup and nothing else.

	It never breaks the save it rides on. A token that names somebody else's
	reading, a reading already decided, or a different document is ignored; any
	failure is logged and rolled back to before this hook.
	"""
	reading = doc.get(TOKEN_KEY)
	if not reading or not isinstance(reading, str):
		return
	savepoint = "fa_fill_stamp"
	messages = _message_count()
	frappe.db.savepoint(savepoint)
	try:
		row = fill_row(reading)
		if row.target_doctype == doc.doctype and stored_status(row) == STATUS_READY:
			record_filled(row, doc.name, saved=doc)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		# A refusal raised with `frappe.throw` has already queued its message for the
		# browser. The save succeeded, so the person must not see an error dialog.
		_drop_messages_since(messages)
		frappe.logger("frappe_agents").warning(
			f"could not record fill {reading} against {doc.doctype} {doc.name}", exc_info=True
		)
	finally:
		doc.__dict__.pop(TOKEN_KEY, None)


def _message_count() -> int:
	return len(getattr(frappe.local, "message_log", None) or [])


def _drop_messages_since(count: int) -> None:
	log = getattr(frappe.local, "message_log", None)
	if log is not None and len(log) > count:
		del log[count:]


def _recorded(doc: Any, docname: str) -> dict:
	"""What a recorded fill reports: the document, the attachment, each sensitive field."""
	flags = _parsed(doc.sensitive_flags)
	return {
		"extraction": doc.name,
		"status": STATUS_ACCEPTED,
		"created_doc": docname,
		"source_attached": _source_attached(doc, doc.target_doctype, docname),
		"sensitive": {key: flag.get("saved") for key, flag in flags.items() if isinstance(flag, dict)},
	}


def discard_stale_fills() -> int:
	"""Close readings no form came back for. Daily job, and safe to run by hand."""
	if not frappe.get_meta(EXTRACTION).has_field("purpose"):
		return 0
	filters = {
		"purpose": PURPOSE_FILL,
		"status": STATUS_READY,
		"creation": ("<", add_to_date(now_datetime(), hours=-FILL_MAX_AGE_HOURS)),
	}
	stale = frappe.get_all(EXTRACTION, filters=filters, pluck="name")
	if not stale:
		return 0
	frappe.db.set_value(
		EXTRACTION,
		filters,
		{"status": STATUS_DISCARDED, "error": _(STALE_NOTE).format(FILL_MAX_AGE_HOURS)},
		update_modified=False,
	)
	return len(stale)


def is_stale(doc: Any) -> bool:
	cutoff = add_to_date(now_datetime(), hours=-FILL_MAX_AGE_HOURS)
	return get_datetime(doc.creation) < get_datetime(cutoff)


# -- what the form is given --------------------------------------------------


def _readable_links(spec: dict, values: dict, dropped: dict) -> dict:
	"""Values with every Link the user can no longer read taken out.

	The links were resolved when the file was read. A record deleted or put out of
	reach since then is not one to put in a form: it goes back to being text the
	person has to match by hand, which is what `dropped` carries.
	"""
	clean: dict[str, Any] = {}
	fields = spec.get("fields") or {}
	tables = spec.get("child_tables") or {}
	for key, value in values.items():
		if key in tables and isinstance(value, list):
			child_fields = tables[key].get("fields") or {}
			clean[key] = [
				_readable_row(child_fields, row, f"{key}[{index}].", dropped)
				for index, row in enumerate(value)
			]
			continue
		if _unreadable_link(fields.get(key), value):
			dropped[key] = _dropped_link(fields[key])
			continue
		clean[key] = value
	return clean


def _readable_row(fields: dict, row: dict, prefix: str, dropped: dict) -> dict:
	clean = {}
	for key, value in row.items():
		if _unreadable_link(fields.get(key), value):
			dropped[f"{prefix}{key}"] = _dropped_link(fields[key])
			continue
		clean[key] = value
	return clean


def _unreadable_link(meta: dict | None, value: Any) -> bool:
	if not meta or meta.get("fieldtype") != "Link" or not meta.get("options"):
		return False
	if not isinstance(value, str) or not value:
		return False
	return not gate.readable(meta["options"], value)


def _dropped_link(meta: dict) -> dict:
	return {
		"doctype": meta.get("options"),
		"text": "",
		"options": [],
		"note": _("The {0} this was matched to is no longer one you can open.").format(meta.get("options")),
	}


def _readable_candidates(candidates: dict) -> dict:
	"""Link candidates with any option the user can no longer read taken out."""
	clean: dict[str, dict] = {}
	for key, entry in candidates.items():
		if not isinstance(entry, dict):
			continue
		doctype = entry.get("doctype")
		options = [
			option
			for option in entry.get("options") or []
			if isinstance(option, dict) and doctype and gate.readable(doctype, option.get("name"))
		]
		clean[key] = {
			"doctype": doctype,
			"text": entry.get("text") or "",
			"options": options,
			"note": entry.get("note") or "",
		}
	return clean


def _sensitive_view(doctype: str, flags: dict) -> dict:
	"""What the form shows beside each sensitive field. Never a value to set.

	The printed value is returned because the owner can already read it on their
	own extraction row; withholding it here would protect nothing. The comparison
	with the master is made again, now, as this user: the master's value may have
	changed since the reading, and so may whether they may see it (read access to
	the record, its permlevel, its masking). A master out of reach keeps any
	mismatch found at reading time and loses everything else.
	"""
	view: dict[str, dict] = {}
	for key, flag in flags.items():
		if not isinstance(flag, dict):
			continue
		fresh = _compare_now(doctype, key, flag)
		if fresh:
			compared = cint(fresh.get("compared"))
			mismatch = cint(fresh.get("mismatch"))
			view[key] = {
				"value": flag.get("value"),
				"master_doctype": fresh.get("master_doctype"),
				"master_name": fresh.get("master_name"),
				"master_field": fresh.get("master_field"),
				"master_value": fresh.get("master_value") if cint(fresh.get("master_visible")) else None,
				"compared": compared,
				"mismatch": mismatch,
				# One click only when the record this document links to holds the same
				# value. Anything else is typed by the person after checking elsewhere.
				"blocked": 0 if (compared and not mismatch) else 1,
			}
			continue
		view[key] = {
			"value": flag.get("value"),
			"master_doctype": None,
			"master_name": None,
			"master_field": None,
			"master_value": None,
			"compared": 0,
			# Losing sight of a record is not agreeing with it.
			"mismatch": cint(flag.get("mismatch")),
			"blocked": 1,
		}
	return view


def _compare_now(doctype: str, key: str, flag: dict) -> dict | None:
	"""The reading's master comparison, made again now. None when there is no master."""
	master_field = flag.get("master_field")
	master_name = flag.get("master_name")
	if SENSITIVE_ROW.match(key) or not master_field or not master_name:
		return None
	fresh = gate.compare_with_master(doctype, key, flag.get("value"), {master_field: master_name})
	return fresh if fresh.get("master_name") else None


def _readable_duplicates(doctype: str, duplicates: dict, target_name: str | None) -> dict:
	"""Duplicate flags re-checked for what this user may open now.

	A name that has gone out of reach is counted instead, the way the gate counts
	the ones that were out of reach from the start. A saved draft does not collide
	with itself.
	"""
	same_file, hidden_files = [], cint(duplicates.get("same_file_hidden_count"))
	for row in duplicates.get("same_file") or []:
		if not isinstance(row, dict):
			continue
		if not gate.readable(EXTRACTION, row.get("name")):
			hidden_files += 1
			continue
		created = row.get("created_doc")
		same_file.append({**row, "created_doc": created if gate.readable(doctype, created) else None})

	collisions, hidden_collisions = [], cint(duplicates.get("unique_collisions_hidden_count"))
	for row in duplicates.get("unique_collisions") or []:
		if not isinstance(row, dict) or (target_name and row.get("name") == target_name):
			continue
		if not gate.readable(doctype, row.get("name")):
			hidden_collisions += 1
			continue
		collisions.append(row)

	return {
		"same_file": same_file,
		"same_file_hidden_count": hidden_files,
		"unique_collisions": collisions,
		"unique_collisions_hidden_count": hidden_collisions,
	}


def _source(file_name: str | None) -> dict:
	"""The file's name, and its url when the user may still open it."""
	if not file_name or not frappe.db.exists("File", file_name):
		return {}
	try:
		file_doc = readable_file(file_name)
	except frappe.PermissionError:
		return {}
	return {"file_name": file_doc.file_name, "file_url": file_doc.file_url}


# -- after the save ----------------------------------------------------------


def _stamp_saved(doctype: str, saved: Any, flags: dict) -> dict:
	"""Record on each flag what the saved document holds for it, and who saved it.

	The value itself is not copied onto the flag: it is on the document, under the
	document's own permissions, and a second copy here would be one more place a
	bank detail is readable from. What is kept is how it compares — to the printed
	value, and to the master the saved document links to now.
	"""
	meta = frappe.get_meta(doctype)
	links = {df.fieldname: saved.get(df.fieldname) for df in meta.get_link_fields()}
	stamp = str(now_datetime())

	stamped = dict(flags)
	for key, flag in stamped.items():
		if not isinstance(flag, dict):
			continue
		value = _saved_value(saved, key, flag.get("value"))
		if value in (None, ""):
			state = SAVED_EMPTY
		elif gate.same_value(value, flag.get("value")):
			state = SAVED_PRINTED
		else:
			state = SAVED_TYPED
		flag["saved"] = state
		flag["confirmed"] = 0 if state == SAVED_EMPTY else 1
		flag["confirmed_by"] = frappe.session.user
		flag["confirmed_at"] = stamp
		if state != SAVED_EMPTY and not SENSITIVE_ROW.match(key):
			check = gate.compare_with_master(doctype, key, value, links)
			flag["saved_master_name"] = check.get("master_name")
			flag["saved_mismatch"] = cint(check.get("mismatch"))
	return stamped


def _saved_value(saved: Any, key: str, printed: Any) -> Any:
	"""The saved value a flag is about.

	For a value in a child row, the row the reading numbered may not be the row it
	became: the person can add rows before it, or reorder them. So any row holding
	the printed value answers for it first, and the numbered row only otherwise.
	"""
	match = SENSITIVE_ROW.match(key)
	if not match:
		return saved.get(key)
	rows = saved.get(match.group("table")) or []
	field = match.group("field")
	if printed not in (None, ""):
		for row in rows:
			if gate.same_value(row.get(field), printed):
				return row.get(field)
	index = int(match.group("index"))
	if index >= len(rows):
		return None
	return rows[index].get(field)


def _attach_source(doc: Any, doctype: str, docname: str) -> bool:
	"""Attach the file the form was filled from to the saved document.

	Under the person's own permission and nothing else: `record_filled` has
	already required write on the document, and the File insert makes its own
	checks. A failure is a missing attachment, not a failed save, so it is logged
	and reported, never raised.
	"""
	savepoint = "fa_fill_attach"
	messages = _message_count()
	frappe.db.savepoint(savepoint)
	try:
		if not _source_attached(doc, doctype, docname):
			readable_file(doc.source_file).create_attachment_copy(doctype, docname)
		return True
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		_drop_messages_since(messages)
		frappe.logger("frappe_agents").warning(
			f"could not attach the source of {doc.name} to {doctype} {docname}", exc_info=True
		)
		return False


def _source_attached(doc: Any, doctype: str, docname: str) -> bool:
	"""Whether the reading's file hangs off the document, as far as this user can see."""
	file_url = frappe.db.get_value("File", doc.source_file, "file_url") if doc.source_file else None
	if not file_url:
		return False
	return bool(
		frappe.get_list(
			"File",
			filters={"file_url": file_url, "attached_to_doctype": doctype, "attached_to_name": docname},
			pluck="name",
			limit_page_length=1,
		)
	)


def _parsed(value: Any) -> dict:
	parsed = frappe.parse_json(value) if isinstance(value, str) else value
	return parsed if isinstance(parsed, dict) else {}
