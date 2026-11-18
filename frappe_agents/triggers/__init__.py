"""Agent Triggers: an agent run started by a document event, writing one field.

A trigger names a doctype, an event, what the agent reads, and the one field it
writes. Three rules shape everything in this package:

* **The run is bound to the person whose action fired it.** Never Administrator,
  never Guest, never a service account. The run can do no more than that person
  could do by hand, narrowed by the agent's access rules.
* **The model holds no tools.** The runtime reads the input — one field of the
  document, or the attached file — and the runtime writes the one target field,
  through `update_draft` and its audit row. The most a hostile document can do is
  change the text that lands in that one field.
* **An agent never fires a trigger.** A save made inside any agent tool call,
  this package's own write included, is ignored by the dispatcher. One trigger
  never starts another.

`dispatch` is the doc_events hook and the job it queues. `execution` is what a
triggered run does inside `execute_run`: read the input, then write the answer.
"""

import frappe
from frappe.model import no_value_fields, table_fields
from frappe.model.workflow import get_workflow_name
from frappe.utils import cint

TRIGGER = "Agent Trigger"
SURFACE = "Trigger"

EVENT_ATTACHMENT = "Attachment Added"
EVENT_CHANGED = "Field Changed"
EVENT_INSERTED = "Document Inserted"

WRITES_ANSWER = "Agent Answer"
WRITES_FILE_TEXT = "File Text"

# What a trigger may write into. Free-text types are the ones every later reader
# wraps as untrusted, which matters: the value was made from a document a person
# supplied, and an unwrapped Data field would hand it to the next chat run as if
# it were trusted. Select is safe the other way round — the answer must be one of
# the options exactly, or nothing is written. Markdown Editor is left out: the
# desk renders a markdown image, so a hostile document could steer the answer
# into one that calls out to another site when somebody opens the form.
TARGET_FIELDTYPES = frozenset({"Small Text", "Text", "Long Text", "Text Editor", "Select"})
HTML_TARGETS = frozenset({"Text Editor"})

# Values a trigger may not read: they are secrets, or they are not values.
UNREADABLE_FIELDTYPES = frozenset({"Password", *no_value_fields, *table_fields})

# The only Run As a trigger accepts. A service user fired by somebody else's
# document is a deputy that document can steer, so it waits for its own design.
SESSION_USER = "Session User"


class TriggerRefused(Exception):
	"""A triggered run may not go ahead, or may not write. The message says why."""


def workflow_state_field(doctype: str) -> str | None:
	"""The field a Workflow keeps its state in. A trigger never writes it."""
	workflow = get_workflow_name(doctype)
	if not workflow:
		return None
	return frappe.get_cached_value("Workflow", workflow, "workflow_state_field")


def is_masked(df) -> bool:
	"""A field the framework masks for people without the mask right.

	Refused as a source and as a target whoever the run is for. A run that may see
	the value would still put what it made from it into a field everyone sees; a
	run that may not would have its write quietly undone by the save. Version 15
	has no masking, so nothing there is masked.
	"""
	return bool(cint(df.get("mask")))


def declassifies(source, target) -> bool:
	"""Whether an answer made from `source` would land at a lower permission level.

	A summary of a level-1 field in a level-0 field hands the level-1 data to
	everyone who may read level 0.
	"""
	return cint(target.permlevel) < cint(source.permlevel)
