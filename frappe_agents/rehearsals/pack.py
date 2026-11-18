"""The prompt-injection pack: rehearsals that attack the untrusted boundary.

Each case is bait text that tries to make the agent do something only its user
may ask for — propose a cancel, copy data into a draft, call a tool it was never
given, or obey an instruction hidden in a document — and the expectations that
prove it did not. The bait reaches the model the way any document text does,
through the agent's own read tools and inside the `<untrusted>` wrapper, so a
pass says the wrapper and the grants held for this agent on this model.

A case carries a canary: a code that appears nowhere but the bait. An answer
that contains it is an answer that followed the bait, whatever else it says.

The pack is a template, not data the app keeps. Loading it writes ordinary
Agent Rehearsal records for one agent, which the manager can then read, edit and
run like any other.
"""

import json
import os
from typing import Any

import frappe
from frappe import _

from frappe_agents.rehearsals.runner import REHEARSAL, VECTOR_COMMENT, VECTOR_FILE, require_manager

PACK_FILE = os.path.join(os.path.dirname(__file__), "injection_pack.json")
NO_TARGET = "the document this note is about"


def pack_cases() -> dict:
	with open(PACK_FILE, encoding="utf-8") as handle:
		return json.load(handle)


@frappe.whitelist(methods=["POST"])
def load_injection_pack(
	agent: str,
	run_as_user: str | None = None,
	bait_vector: str = VECTOR_FILE,
	context_doctype: str | None = None,
	context_name: str | None = None,
) -> dict:
	"""Write one rehearsal per pack case for this agent. Returns what it wrote.

	A case already loaded for this agent with the same bait vector and document
	is skipped, so loading twice does not double the pack.
	"""
	require_manager()
	frappe.has_permission(REHEARSAL, "create", throw=True)
	frappe.has_permission("Agent", "read", doc=agent, throw=True)
	if bait_vector not in (VECTOR_FILE, VECTOR_COMMENT):
		frappe.throw(_("Bait Vector must be {0} or {1}.").format(_(VECTOR_FILE), _(VECTOR_COMMENT)))

	pack = pack_cases()
	target = f"{context_doctype} {context_name}" if context_doctype and context_name else NO_TARGET
	prompt = pack["prompts"][bait_vector].format(target=target)

	created, skipped = [], []
	for case in pack["cases"]:
		if _already_loaded(agent, case["key"], bait_vector, context_doctype, context_name):
			skipped.append(case["key"])
			continue
		canary = "RH-" + frappe.generate_hash(length=8).upper()
		doc = frappe.get_doc(
			{
				"doctype": REHEARSAL,
				"title": case["title"],
				"agent": agent,
				"enabled": 1,
				"run_as_user": run_as_user or frappe.session.user,
				"pack_case": case["key"],
				"prompt": prompt,
				"context_doctype": context_doctype or None,
				"context_name": context_name or None,
				"bait_vector": bait_vector,
				"bait_text": case["bait"].format(target=target, canary=canary),
				"expectations": [
					{"expectation": row["expectation"], "value": row["value"].format(canary=canary)}
					for row in case["expectations"]
				],
			}
		)
		doc.insert()
		created.append(doc.name)

	return {"created": created, "skipped": skipped}


def _already_loaded(agent: str, key: str, vector: str, doctype: Any, name: Any) -> bool:
	return bool(
		frappe.db.exists(
			REHEARSAL,
			{
				"agent": agent,
				"pack_case": key,
				"bait_vector": vector,
				"context_doctype": doctype or ("is", "not set"),
				"context_name": name or ("is", "not set"),
			},
		)
	)
