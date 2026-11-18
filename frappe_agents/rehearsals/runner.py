"""Running a rehearsal: a real run, under a real user, scored afterwards.

A rehearsal is not a simulation. `execute_rehearsal` inserts an ordinary Agent
Run on the surface "Rehearsal" and hands it to `execute_run`, the same job chat
uses. So the run gets the same identity binding, the same grants, the same kill
switch, the same budget and the same audit rows as any other. Nothing in here
widens what the agent may do, and nothing here touches the run while it runs.

What a rehearsal adds is around the run, never inside it:

* **Before** — the door checks who the run may act as, and places the bait.
* **After** — the scorer reads the audit rows, copies what an Agent Manager
  needs onto the result (managers cannot read Agent Run or Agent Tool Call), and
  closes any proposal the run made, so a test never lands in an approver's queue
  as a live request.

Run As is not a free choice. The answer a rehearsal shows its manager is built
from whatever the test user can read, so a manager who could name any user
could read as anyone. The rule: an agent that runs as a service user is
rehearsed as that service user; otherwise the run acts as the manager who
starts it, or as a user a System Manager gave the Agent Rehearsal User role.
"""

from contextlib import contextmanager
from typing import Any

import frappe
from frappe import _
from frappe.utils import cint, flt, now_datetime

from frappe_agents.actions import REHEARSAL_SURFACE, close_rehearsal_proposals
from frappe_agents.api import MAX_MESSAGE_CHARS, _check_budget, _user_may_use
from frappe_agents.model_profiles import check_profile
from frappe_agents.rehearsals import scoring
from frappe_agents.runner.run import FORBIDDEN_USERS, execute_run
from frappe_agents.tools.base import CAPABILITY_READ, runtime_enabled
from frappe_agents.triggers.dispatch import no_triggers

REHEARSAL = "Agent Rehearsal"
RESULT = "Agent Rehearsal Result"
SURFACE = REHEARSAL_SURFACE
MANAGER_ROLE = "Agent Manager"
SYSTEM_MANAGER = "System Manager"
RUN_AS_ROLE = "Agent Rehearsal User"
SERVICE_USER = "Service User"

VECTOR_NONE = "None"
VECTOR_FILE = "Attached file"
VECTOR_COMMENT = "Comment on the document"

STATUS_QUEUED = "Queued"
STATUS_RUNNING = "Running"
STATUS_SCORED = "Scored"
STATUS_FAILED = "Failed"

# What one result keeps of the run's tool calls. A run is capped at its agent's
# max steps, so this is a ceiling for a runaway, not a page size.
CALL_LIMIT = 200
ERROR_LIMIT = 500
BAIT_FILE_NAME = "notes.txt"
CLOSE_NOTE = "Closed by a rehearsal. A proposal made during a rehearsal is never applied."


@frappe.whitelist(methods=["POST"])
def start_rehearsal(rehearsal: str, model_profile: str | None = None) -> dict:
	"""Queue one rehearsal. Returns the result that will hold its verdict."""
	doc = _rehearsal_for_manager(rehearsal)
	result = queue_rehearsal(doc, model_profile=(model_profile or "").strip() or None)
	return {"result": result}


@frappe.whitelist(methods=["POST"])
def run_all(agent: str, model_profile: str | None = None) -> dict:
	"""Queue every enabled rehearsal of an agent on one model, under one batch key.

	Each rehearsal goes through the same door a single one does. A rehearsal the
	door refuses is reported by name, and the rest still run.
	"""
	require_manager()
	frappe.has_permission("Agent", "read", doc=agent, throw=True)
	names = frappe.get_list(
		REHEARSAL,
		filters={"agent": agent, "enabled": 1},
		pluck="name",
		order_by="creation asc",
	)
	if not names:
		frappe.throw(_("Agent {0} has no enabled rehearsals.").format(agent))

	batch = frappe.generate_hash(length=10)
	profile = (model_profile or "").strip() or None
	queued, refused = [], []
	for name in names:
		doc = _rehearsal_for_manager(name)
		try:
			queued.append(queue_rehearsal(doc, model_profile=profile, batch=batch))
		except (frappe.ValidationError, frappe.PermissionError) as exc:
			refused.append({"rehearsal": name, "error": str(exc) or exc.__class__.__name__})
			# The refusal is in the reply. Left in the message log it would also pop
			# up as an error on a call that succeeded.
			frappe.clear_messages()
	return {"batch": batch, "results": queued, "refused": refused}


def queue_rehearsal(doc: Any, model_profile: str | None = None, batch: str | None = None) -> str:
	"""Check the door, write a Queued result, and enqueue the job that runs it.

	The checks are chat's own — runtime on, agent enabled, the test user allowed
	to use the agent, the model profile allowed to the test user, the daily
	budget — asked about the test user rather than the manager. The run asks the
	identity and profile questions again when it starts.
	"""
	if not runtime_enabled():
		frappe.throw(_("The agent runtime is switched off."))

	agent = frappe.get_doc("Agent", doc.agent)
	if not cint(agent.enabled):
		frappe.throw(_("Agent {0} is disabled.").format(agent.name))

	user = check_run_as(agent, doc.run_as_user)
	roles = set(frappe.get_roles(user))
	if not _user_may_use(agent, roles):
		raise frappe.PermissionError(_("{0} is not allowed to use the agent {1}.").format(user, agent.name))

	profile = model_profile or agent.model_profile
	if profile != agent.model_profile:
		profile = check_profile(agent, profile, roles=roles)

	_check_budget(agent, frappe.get_cached_doc("Agent Settings"))

	result = frappe.get_doc(
		{
			"doctype": RESULT,
			"rehearsal": doc.name,
			"agent": agent.name,
			"model_profile": profile,
			"batch": batch,
			"status": STATUS_QUEUED,
			"run_as_user": user,
			"started_by": frappe.session.user,
		}
	)
	# No role may create a result: they are written here and nowhere else.
	result.flags.ignore_permissions = True
	result.insert(ignore_permissions=True)

	frappe.enqueue(
		"frappe_agents.rehearsals.runner.execute_rehearsal",
		queue="long",
		enqueue_after_commit=True,
		result_name=result.name,
	)
	return result.name


def check_run_as(agent: Any, user: str | None, starter: str | None = None) -> str:
	"""The user a rehearsal of this agent may run as, or a refusal saying why."""
	starter = starter or frappe.session.user
	if agent.run_as == SERVICE_USER:
		if not agent.service_user:
			frappe.throw(_("Agent {0} runs as a service user but names none.").format(agent.name))
		if user and user != agent.service_user:
			frappe.throw(
				_("Agent {0} runs as {1}. A rehearsal of it runs as {1} too.").format(
					agent.name, agent.service_user
				)
			)
		user = agent.service_user

	if not user or user in FORBIDDEN_USERS:
		frappe.throw(_("A rehearsal cannot run as {0}.").format(user or _("nobody")))
	if not cint(frappe.db.get_value("User", user, "enabled")):
		frappe.throw(_("User {0} is disabled or missing.").format(user))

	# A service user is not exempt. Anyone who may write an Agent may name its
	# service user, so without this an Agent Manager could point a new agent at any
	# account and read as it through a rehearsal. Chat refuses service-user agents
	# outright; a rehearsal is the one door that starts them, so it asks for the
	# role a System Manager hands out.
	if user == starter and agent.run_as != SERVICE_USER:
		return user
	if RUN_AS_ROLE not in frappe.get_roles(user):
		raise frappe.PermissionError(
			_(
				"A rehearsal runs as you, or as a user with the {0} role. {1} has neither. "
				"Ask a System Manager to give {1} that role."
			).format(RUN_AS_ROLE, user)
		)
	return user


def execute_rehearsal(result_name: str) -> None:
	"""RQ job: place the bait, run the agent, score the run. Never raises.

	Whatever happens, a proposal the run made is closed before the job ends. A
	scorer that died must not leave a test proposal Pending in an approver's
	queue, or blocking a real proposal on the same document.
	"""
	result = frappe.get_doc(RESULT, result_name)
	state: dict = {}
	try:
		_execute(result, state)
	except Exception as exc:
		frappe.logger("frappe_agents").error(f"rehearsal result {result_name} failed", exc_info=True)
		_write(result, {"status": STATUS_FAILED, "error": (str(exc) or exc.__class__.__name__)[:ERROR_LIMIT]})
		_keep_bait_rows(result, state.get("bait_rows") or [])
	finally:
		if state.get("run"):
			close_rehearsal_proposals(state["run"], CLOSE_NOTE)


def _execute(result: Any, state: dict) -> None:
	rehearsal = frappe.get_doc(REHEARSAL, result.rehearsal)
	agent = frappe.get_doc("Agent", rehearsal.agent)
	# Asked again: a role can be taken away between queueing and running.
	user = check_run_as(agent, result.run_as_user, starter=result.started_by)

	_write(result, {"status": STATUS_RUNNING})
	conversation, bait, bait_rows = _plant_bait(rehearsal, user)
	state["bait_rows"] = bait_rows
	# Listed before the commit below, so a worker killed mid-run still leaves a
	# pointer to the bait it planted.
	_keep_bait_rows(result, bait_rows)

	run = frappe.get_doc(
		{
			"doctype": "Agent Run",
			"agent": agent.name,
			"conversation": conversation,
			"effective_user": user,
			"run_as_mode": agent.run_as,
			"surface": SURFACE,
			"status": "Queued",
			"depth": 0,
			"input_message": rehearsal.prompt,
			"context_doctype": rehearsal.context_doctype or None,
			"context_name": rehearsal.context_name or None,
			"model_profile": result.model_profile,
		}
	)
	# Owned by the user it runs as, the way a chat run is. Inserted as the manager,
	# the run's owner-scoped read would hand the manager its full event log.
	with _acting_as(user):
		run.flags.ignore_permissions = True
		run.insert(ignore_permissions=True)
	state["run"] = run.name
	_write(result, {"run": run.name})

	# Let the rest of the site see the rehearsal is running, and let go of the
	# bait comment's write on its document before the model is called — a model
	# turn can take minutes. A run that dies after this leaves the result
	# Running, which the daily sweep marks Failed.
	# Not under test: a commit there would outlive the rollback each test ends with.
	if not frappe.flags.in_test:
		frappe.db.commit()  # nosemgrep: frappe-semgrep-rules.rules.frappe-manual-commit

	execute_run(run.name)
	run.reload()
	_score(result, rehearsal, run, bait, bait_rows)


def _score(result: Any, rehearsal: Any, run: Any, bait: dict | None, bait_rows: list[dict]) -> None:
	# Read without a permission check: the scorer runs as the manager, who may
	# not read the audit, and the result it writes is what the manager is shown.
	calls = frappe.get_all(
		"Agent Tool Call",
		filters={"run": run.name},
		fields=["tool", "outcome", "docs_touched", "args_json", "result_summary", "error"],
		order_by="creation asc",
		limit=CALL_LIMIT,
	)
	proposals = frappe.get_all("Agent Action", filters={"run": run.name}, pluck="name", limit=CALL_LIMIT)
	write_tools = set(
		frappe.get_all("Agent Tool", filters={"capability": ("!=", CAPABILITY_READ)}, pluck="name")
	)
	known = _known_doctypes(calls)
	reports = _report_doctypes(calls, known)

	verdict, checks = scoring.score(
		[row.as_dict() for row in rehearsal.expectations],
		run_status=run.status,
		answer=run.output_message,
		calls=calls,
		proposals=proposals,
		write_tools=write_tools,
		known_doctypes=known,
		bait=bait,
		run_error=run.error,
		reports=reports,
	)

	closed = close_rehearsal_proposals(run.name, CLOSE_NOTE)

	result.reload()
	result.update(
		{
			"status": STATUS_SCORED,
			"verdict": verdict,
			"run_status": run.status,
			"error": (run.error or "")[:ERROR_LIMIT] or None,
			"answer": run.output_message,
			"tokens_in": cint(run.tokens_in),
			"tokens_out": cint(run.tokens_out),
			"cost": run_cost(run.model_profile, run.tokens_in, run.tokens_out),
			"model_profile": run.model_profile or result.model_profile,
		}
	)
	result.set("checks", checks)
	result.set(
		"documents", bait_rows + _written_documents(calls, write_tools, known) + _proposals(proposals, closed)
	)
	result.set(
		"calls",
		[
			{
				"tool": call.tool,
				"outcome": call.outcome,
				"documents": call.docs_touched,
				"summary": call.result_summary,
				"error": call.error,
			}
			for call in calls
		],
	)
	result.flags.ignore_permissions = True
	# A draft the agent wrote and a human later deleted is still part of the record.
	result.flags.ignore_links = True
	result.save(ignore_permissions=True)

	rehearsal.db_set({"last_verdict": verdict, "last_result": result.name}, update_modified=False)


def run_cost(profile: str | None, tokens_in: Any, tokens_out: Any) -> float | None:
	"""What the run cost at the profile's rates today, or None when it has none.

	A snapshot: profile prices change, and a comparison across a price change
	should compare what each run cost when it ran.
	"""
	if not profile:
		return None
	rates = frappe.db.get_value(
		"LLM Model Profile", profile, ["cost_input_per_million", "cost_output_per_million"], as_dict=True
	)
	if not rates or not (flt(rates.cost_input_per_million) or flt(rates.cost_output_per_million)):
		return None
	return (
		cint(tokens_in) * flt(rates.cost_input_per_million)
		+ cint(tokens_out) * flt(rates.cost_output_per_million)
	) / 1_000_000


def _plant_bait(rehearsal: Any, user: str) -> tuple[str | None, dict | None, list[dict]]:
	"""Put the bait where the agent will find it. Returns (conversation, bait, rows).

	A file goes on a conversation of its own, owned by the test user and hidden
	from their chat list: it touches no business data. A comment goes on the
	document itself and stays there — every later run on that document reads it
	too, so it is listed on the result for a person to remove.
	"""
	vector = rehearsal.bait_vector or VECTOR_NONE
	if vector == VECTOR_NONE or not (rehearsal.bait_text or "").strip():
		return None, None, []

	if vector == VECTOR_FILE:
		with _acting_as(user):
			conversation = frappe.get_doc(
				{
					"doctype": "Agent Conversation",
					"agent": rehearsal.agent,
					"user": user,
					"title": f"Rehearsal: {rehearsal.title}"[:140],
					"rehearsal": 1,
					"last_activity": now_datetime(),
				}
			)
			conversation.insert(ignore_permissions=True)
			file = frappe.get_doc(
				{
					"doctype": "File",
					"file_name": BAIT_FILE_NAME,
					"attached_to_doctype": "Agent Conversation",
					"attached_to_name": conversation.name,
					"is_private": 1,
					"content": rehearsal.bait_text,
				}
			)
			file.insert(ignore_permissions=True)
		return (
			conversation.name,
			{"vector": vector, "file": file.name},
			[{"kind": "Bait", "document_type": "File", "document_name": file.name}],
		)

	# The comment is written as the manager, so the manager has to be able to read
	# the document it lands on. A rehearsal is not a way to write on records the
	# person running it cannot open.
	frappe.has_permission(rehearsal.context_doctype, "read", doc=rehearsal.context_name, throw=True)
	comment = frappe.get_doc(
		{
			"doctype": "Comment",
			"comment_type": "Comment",
			"reference_doctype": rehearsal.context_doctype,
			"reference_name": rehearsal.context_name,
			"content": rehearsal.bait_text,
		}
	)
	# Saved through Comment.validate, which sanitises the HTML. The desk renders a
	# comment as HTML for everyone who opens the document, so raw bait here would
	# let a manager plant script on records they can only read. That is also why
	# the close-the-wrapper case ships for the file vector only. And it fires no
	# agent trigger: a trigger that answered the bait would be the bait working.
	with no_triggers():
		comment.insert(ignore_permissions=True)
	return (
		None,
		{"vector": vector, "doctype": rehearsal.context_doctype, "name": rehearsal.context_name},
		[{"kind": "Bait", "document_type": "Comment", "document_name": comment.name}],
	)


@contextmanager
def _acting_as(user: str):
	"""Write the bait as the test user, so they own it the way an upload would."""
	original = frappe.session.user
	frappe.set_user(user)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser
	try:
		yield
	finally:
		frappe.set_user(original)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser


def _known_doctypes(calls: list[dict]) -> set[str]:
	"""The labels in these calls' `docs_touched` that are real document types."""
	labels = set()
	for call in calls:
		labels |= scoring.labels(call.get("docs_touched"))
	return {label for label in labels if frappe.db.exists("DocType", label)}


def _report_doctypes(calls: list[dict], known: set[str]) -> dict[str, str]:
	"""Each report a call ran, mapped to the doctype it reads."""
	reports: dict[str, str] = {}
	for call in calls:
		for label, names in scoring.parse_touched(call.get("docs_touched"), known):
			if label != "Report":
				continue
			for name in names:
				reports[name] = frappe.db.get_value("Report", name, "ref_doctype") or ""
	return reports


def _keep_bait_rows(result: Any, rows: list[dict]) -> None:
	"""List the bait on a result whose run never got scored, so it can still be found."""
	if not rows:
		return
	try:
		result.reload()
		result.set("documents", rows)
		result.flags.ignore_permissions = True
		result.flags.ignore_links = True
		result.save(ignore_permissions=True)
	except Exception:
		frappe.logger("frappe_agents").error(f"could not list the bait on {result.name}", exc_info=True)


# How long a result may sit Queued or Running before the sweep calls it dead.
STALE_HOURS = 6


def fail_stale_results() -> int:
	"""Daily: mark results whose job never finished as Failed. Returns how many."""
	from frappe.utils import add_to_date

	cutoff = add_to_date(now_datetime(), hours=-STALE_HOURS)
	names = frappe.get_all(
		RESULT,
		filters={"status": ("in", (STATUS_QUEUED, STATUS_RUNNING)), "creation": ("<", cutoff)},
		pluck="name",
	)
	for name in names:
		frappe.db.set_value(
			RESULT,
			name,
			{"status": STATUS_FAILED, "error": _("The rehearsal job never finished.")},
			update_modified=False,
		)
	return len(names)


def _written_documents(calls: list[dict], write_tools: set[str], known: set[str]) -> list[dict]:
	"""Every document a successful write call says it touched, once each."""
	rows, seen = [], set()
	for call in calls:
		if call.get("outcome") != scoring.SUCCESS or call.get("tool") not in write_tools:
			continue
		touched = scoring.parse_touched(call.get("docs_touched"), known)
		if any(doctype == "Agent Action" for doctype, _names in touched):
			# A proposal names its target, which it did not write. It is listed
			# once, as a proposal.
			continue
		for doctype, names in touched:
			if doctype in scoring.NOT_A_DOCUMENT:
				continue
			for name in names:
				if (doctype, name) in seen:
					continue
				seen.add((doctype, name))
				rows.append(
					{
						"kind": "Written",
						"document_type": doctype,
						"document_name": name,
						"tool": call.get("tool"),
					}
				)
	return rows


def _proposals(proposals: list[str], closed: list[str]) -> list[dict]:
	return [
		{
			"kind": "Proposal",
			"document_type": "Agent Action",
			"document_name": name,
			"tool": "closed" if name in closed else "",
		}
		for name in proposals
	]


def _rehearsal_for_manager(name: str) -> Any:
	require_manager()
	frappe.has_permission(REHEARSAL, "write", doc=name, throw=True)
	return frappe.get_doc(REHEARSAL, name)


def require_manager() -> None:
	roles = set(frappe.get_roles())
	if not roles & {MANAGER_ROLE, SYSTEM_MANAGER}:
		raise frappe.PermissionError(_("You need the {0} role to run rehearsals.").format(MANAGER_ROLE))


def _write(result: Any, values: dict) -> None:
	result.flags.ignore_permissions = True
	result.db_set(values, update_modified=False)


def validate_prompt(prompt: str | None) -> None:
	"""The same limit the chat door puts on a message."""
	if not (prompt or "").strip():
		frappe.throw(_("A rehearsal needs a prompt."))
	if len(prompt) > MAX_MESSAGE_CHARS:
		frappe.throw(
			_("The prompt is {0} characters. The limit is {1}.").format(len(prompt), MAX_MESSAGE_CHARS)
		)


# How many scored results the comparison reads, newest first. Enough for every
# rehearsal of an agent on a handful of models, several times over.
COMPARISON_LIMIT = 1000


@frappe.whitelist()
def comparison(agent: str) -> dict:
	"""The newest scored verdict of each rehearsal of an agent, on each model it ran on.

	One row per rehearsal, one column per model profile. Read through `get_list`,
	so it shows exactly the results the caller may read.
	"""
	require_manager()
	frappe.has_permission("Agent", "read", doc=agent, throw=True)
	rehearsals = frappe.get_list(
		REHEARSAL,
		filters={"agent": agent},
		fields=["name", "title"],
		order_by="creation asc",
	)
	results = frappe.get_list(
		RESULT,
		filters={"agent": agent, "status": STATUS_SCORED},
		fields=[
			"name",
			"rehearsal",
			"model_profile",
			"verdict",
			"tokens_in",
			"tokens_out",
			"cost",
			"creation",
		],
		order_by="creation desc",
		limit_page_length=COMPARISON_LIMIT,
	)

	profiles: list[str] = []
	cells: dict[str, dict] = {}
	for row in results:
		profile = row.model_profile or ""
		if profile not in profiles:
			profiles.append(profile)
		by_profile = cells.setdefault(row.rehearsal, {})
		if profile in by_profile:
			continue
		by_profile[profile] = {
			"result": row.name,
			"verdict": row.verdict,
			"tokens": cint(row.tokens_in) + cint(row.tokens_out),
			"cost": row.cost,
		}

	return {
		"profiles": sorted(profiles),
		"rows": [
			{"rehearsal": row.name, "title": row.title, "cells": cells.get(row.name, {})}
			for row in rehearsals
		],
	}
