"""The one tool that starts an extraction.

An agent may ask for a document to be read. It does not get to see the result and
it does not get to act on it: the tool queues a job and returns a name. What comes
back is a Document Extraction row and a draft for a human, on the other side of a
review the agent has no part in.

Two narrowings that are not in the general File permission model:

* The file must be **attached to a document the user can read**. A file that hangs
  off a record has a provenance a reviewer can check; a loose private file has
  none, and an agent asking to read one is asking for something it cannot explain.
* The payload is a file and a doctype. There is no instruction field, so nothing a
  model writes can reach the extraction call — the prompt around the document is
  fixed in `pipeline.py`.
"""

from typing import Any

import frappe

from frappe_agents.extraction.pipeline import queue_extraction, resolve_file
from frappe_agents.tools.base import CAPABILITY_DRAFT, ToolDenied, current_run


def extract_document(payload: dict) -> dict:
	"""Queue an extraction of one attached file into one doctype."""
	file_ref = _require_str(payload, "file")
	target_doctype = _require_str(payload, "target_doctype")

	# A File name, a link to a file on this site, or the filename itself — all three
	# end as one File row, checked for permission the way a File name always was.
	file_doc = resolve_file(file_ref)
	_require_provenance(file_doc)

	name = queue_extraction(file_doc.name, target_doctype, model_profile=_run_profile())

	return {
		"extraction": name,
		"target_doctype": target_doctype,
		"status": "Pending",
		"note": (
			f"Queued. The document will be read and turned into a draft {target_doctype} for "
			"review. You will not see the result: a person checks it, and anything marked "
			"sensitive stays off the draft until they confirm it by hand."
		),
		"_docs_touched": f"Document Extraction: {name}",
	}


def _require_provenance(file_doc: Any) -> None:
	"""The file has to hang off a record, and one this user may read.

	An Agent Conversation is an acceptable anchor like any other record: it says who
	supplied the file and in what context, which is what a reviewer needs to check.
	"""
	if not file_doc.attached_to_doctype or not file_doc.attached_to_name:
		raise ToolDenied(
			f"{file_doc.file_name or file_doc.name} is not attached to any record. Attach it to "
			"the document it belongs to first, so a reviewer can see where it came from."
		)
	if not frappe.has_permission(file_doc.attached_to_doctype, "read", doc=file_doc.attached_to_name):
		raise ToolDenied(
			f"You are not allowed to read {file_doc.attached_to_doctype} "
			f"{file_doc.attached_to_name}, which this file is attached to."
		)


def _run_profile() -> str | None:
	"""Extract with the model this run is on, which is the agent's unless it was overridden."""
	run = current_run()
	if run is None or not run.get("agent"):
		return None
	if run.get("model_profile"):
		return run.model_profile
	agent = frappe.get_cached_doc("Agent", run.agent)
	return agent.model_profile or None


def _require_str(payload: dict, key: str) -> str:
	value = payload.get(key)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f"{key} is required and must be a string.")
	return value.strip()


TOOLS = [
	{
		"tool_name": "extract_document",
		"handler_path": "frappe_agents.tools.extraction_tools.extract_document",
		"capability": CAPABILITY_DRAFT,
		"description": (
			"Turn an attached PDF or image into a draft document for a person to review. "
			"Only use this when the document plausibly IS the target doctype — an identity "
			"document is not an order. If someone just asks what a document says or contains, "
			"say you cannot read documents yet instead of queueing an extraction. "
			"Give the file and the doctype the draft should be. The file "
			"must already be attached to a record you can read. This returns straight away "
			"with an extraction name: the reading happens in the background, you never see "
			"the values, and fields the site marks sensitive — bank and payment details — "
			"are deliberately left off the draft until a human confirms them one by one."
		),
		"args_schema": {
			"type": "object",
			"properties": {
				"file": {
					"type": "string",
					"description": (
						"The file to read: a File record name, a link to it on this site "
						"(/private/files/CV.pdf), or its filename when only one file has it. "
						"Files elsewhere on the internet cannot be read."
					),
				},
				"target_doctype": {
					"type": "string",
					"description": "DocType the draft should be created as, e.g. 'Purchase Invoice'.",
				},
			},
			"required": ["file", "target_doctype"],
		},
	}
]
