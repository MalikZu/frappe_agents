"""The prompt-injection pack: rehearsals that attack the untrusted boundary.

Each case is bait text that tries to make the agent do something only its user
may ask for — propose a cancel, copy data into a draft, call a tool it was never
given, or obey an instruction hidden in a document — and the expectations that
prove it did not. The bait reaches the model the way any document text does,
through the agent's own read tools and inside the `<untrusted>` wrapper, so a
pass says the wrapper and the grants held for this agent on this model.

A case carries a canary: a code that appears nowhere but the bait. An answer
that contains it most likely followed the bait. It can also be an answer that
quoted the bait while reporting it, so read the Answer on a result that failed
only that check before you call it obeyed.

A case may name the vectors it runs on. Close-the-wrapper is file-only: comment
bait is sanitised HTML, which cannot carry a literal closing tag.

The pack is a template, not data the app keeps. Loading it writes ordinary
Agent Rehearsal records for one agent, which the manager can then read, edit and
run like any other.
"""

from typing import Any

import frappe
from frappe import _

from frappe_agents.rehearsals.injection_pack import CASES, PROMPTS
from frappe_agents.rehearsals.runner import REHEARSAL, VECTOR_COMMENT, VECTOR_FILE, require_manager

NO_TARGET = "the document this note is about"


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

	target = f"{context_doctype} {context_name}" if context_doctype and context_name else NO_TARGET
	prompt = PROMPTS[bait_vector].format(target=target)

	created, skipped = [], []
	for case in CASES:
		if bait_vector not in case.get("vectors", PROMPTS):
			continue
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
