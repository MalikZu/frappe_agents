"""The doc_events hook that notices an event, and the job that turns it into a run.

`on_update` is registered for every doctype, so it is on the path of every save
on the site. It does as little as it can there:

* flags first, then one cached lookup of the triggers on this doctype — a site
  with no trigger on the doctype pays for a dict lookup and nothing else;
* no database writes inside the person's save. A row inserted there, or an error
  raised there, would be part of their transaction, and a trigger must never be
  the reason somebody's save failed;
* the rate caps are Redis counters, and the work is a job queued after commit.

`run_trigger` is that job. It runs as the person whose save fired it, decides
whether the run may happen at all, writes the Agent Run, and queues
`execute_run` exactly as chat does. One run per document per trigger is in
flight at a time; an event that arrives meanwhile marks the document, and the
run that is in flight queues one more when it ends. That last run reads the
newest state, so a burst of edits costs two runs, not one per edit and not a
summary of text that has since changed.

Everything that is skipped is counted per trigger per day, so a trigger that
stopped firing says why on its own form instead of in nobody's log.
"""

import json
import operator
from typing import Any

import frappe
import redis
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import cint, cstr, now_datetime, today
from frappe.utils.data import cast

from frappe_agents.tools.base import FORBIDDEN_USERS, RUN_FLAG, runtime_enabled
from frappe_agents.triggers import (
	EVENT_ATTACHMENT,
	EVENT_CHANGED,
	EVENT_INSERTED,
	SESSION_USER,
	SURFACE,
	TRIGGER,
)

INDEX_KEY = "frappe_agents:trigger_index"
INDEX_SECONDS = 10 * 60
# How many waiting uploads one insert may pick up. A form is not a bulk import.
MAX_RELINKED = 20
# How many files one document may have waiting behind a run in flight.
MAX_WAITING_FILES = 20
KEY_PREFIX = "frappe_agents:trigger"

DEFAULT_RUNS_PER_HOUR = 30
DEFAULT_RUNS_PER_USER_PER_HOUR = 10

# How long one run may hold its document before the lock lets go by itself. A run
# that dies without its finally block must not shut the trigger off for that
# document for good.
LOCK_SECONDS = 30 * 60
SKIP_SECONDS = 2 * 24 * 60 * 60
JOB_TIMEOUT = 300
QUEUE = "long"

# Why an event did not become a run. Each one is a counter on the trigger's form.
SKIP_NO_USER = "No eligible user"
SKIP_RATE = "Rate limit"
SKIP_SWITCH = "Runtime switched off"
SKIP_AGENT = "Agent disabled or not Session User"
SKIP_ROLE = "User not in the agent's allowed roles"
SKIP_BUDGET = "Token budget used"
SKIP_REASONS = (SKIP_NO_USER, SKIP_RATE, SKIP_SWITCH, SKIP_AGENT, SKIP_ROLE, SKIP_BUDGET)

# Saves that never fire a trigger whatever the settings say. Nobody's action is
# behind them, and the user they run as is Administrator.
SYSTEM_FLAGS = ("in_install", "in_install_db", "in_migrate", "in_patch", "in_uninstall")

COMPARISONS = {
	"=": operator.eq,
	"!=": operator.ne,
	">": operator.gt,
	"<": operator.lt,
	">=": operator.ge,
	"<=": operator.le,
}

FILE = "File"
LOCAL_FILE_PREFIXES = ("/files/", "/private/files/")


# --- the hook ---------------------------------------------------------------


def on_update(doc: Any, method: str | None = None) -> None:
	"""doc_events "*" on_update. Never raises into the save that called it.

	One hook, not two: `insert` runs `after_insert` and then `on_update` with
	`flags.in_insert` set, so this one hook sees both and tells them apart.
	"""
	said = len(frappe.local.message_log or [])
	try:
		_on_update(doc)
	except Exception:
		# A throw leaves its message queued for the person's screen even when it is
		# caught. Their save worked; the trigger's trouble is not theirs to read.
		del frappe.local.message_log[said:]
		frappe.logger("frappe_agents").error(
			f"agent trigger dispatch failed for {doc.doctype} {doc.name}", exc_info=True
		)


def _on_update(doc: Any) -> None:
	if _suppressed():
		return

	index = trigger_index()
	if not index:
		return

	if doc.doctype == FILE:
		rows = [row for row in index.get(doc.get("attached_to_doctype") or "", []) if _file_fires(doc, row)]
		# An upload made before its document was saved hangs off a temporary name.
		# It is picked up when the document is inserted, below.
		if rows and frappe.db.exists(doc.attached_to_doctype, doc.attached_to_name):
			_fire(rows, doc.attached_to_doctype, doc.attached_to_name, file=doc.name)
		return

	watched = index.get(doc.doctype, [])
	rows = [row for row in watched if _doc_fires(doc, row)]
	if rows:
		_fire(rows, doc.doctype, doc.name, doc=doc)

	if doc.flags.get("in_insert"):
		attachment_rows = [row for row in watched if row.get("event") == EVENT_ATTACHMENT]
		if attachment_rows:
			_fire_relinked(doc, attachment_rows)


def _fire_relinked(doc: Any, rows: list[dict]) -> None:
	"""Files uploaded while the document was still unsaved, now attached to it.

	frappe moves them onto the new name with a direct update, which no hook sees,
	so the document's own insert is where they are noticed.
	"""
	# Runtime code: which files hang off a document this person just created.
	files = frappe.get_all(
		FILE,
		filters={"attached_to_doctype": doc.doctype, "attached_to_name": doc.name, "is_folder": 0},
		fields=["name", "file_name", "file_url"],
		limit_page_length=MAX_RELINKED,
	)
	for file in files:
		if not cstr(file.file_url).startswith(LOCAL_FILE_PREFIXES):
			continue
		matching = [row for row in rows if _extension_allowed(file, row.get("file_extensions"))]
		if matching:
			_fire(matching, doc.doctype, doc.name, doc=doc, file=file.name)


def _suppressed() -> bool:
	"""Whether this save is one no trigger may answer.

	A save inside an agent tool call is the important one: an agent that drafted
	forty documents must not start forty more runs, and a trigger's own write is
	a tool call too, so it can never fire itself or anything else.
	"""
	flags = frappe.flags
	if any(flags.get(flag) for flag in SYSTEM_FLAGS):
		return True
	if flags.get(RUN_FLAG):
		return True
	if flags.get("in_import"):
		return bool(cint(frappe.get_cached_doc("Agent Settings").get("suppress_on_import")))
	return False


def _doc_fires(doc: Any, row: dict) -> bool:
	inserting = bool(doc.flags.get("in_insert"))
	event = row.get("event")
	if event == EVENT_INSERTED:
		return inserting
	if event == EVENT_CHANGED:
		field = row.get("source_field")
		if not field:
			return False
		if inserting:
			# A new document has no "before". Filling the field at creation is the
			# change; creating the document with it empty is not.
			return not _is_empty(doc.get(field))
		return bool(doc.has_value_changed(field))
	return False


def _file_fires(file: Any, row: dict) -> bool:
	"""A file that has just come to hang off a document of this doctype.

	A File inserted already attached, or one whose attachment was changed by a
	save. An upload made before its document existed is picked up by the
	document's insert instead.
	"""
	if row.get("event") != EVENT_ATTACHMENT:
		return False
	if cint(file.get("is_folder")) or not file.get("attached_to_name"):
		return False
	if not (file.flags.get("in_insert") or file.has_value_changed("attached_to_name")):
		return False
	if not cstr(file.get("file_url")).startswith(LOCAL_FILE_PREFIXES):
		# A link to somewhere else is not a file in this site.
		return False
	return _extension_allowed(file, row.get("file_extensions"))


def _extension_allowed(file: Any, allowed: str | None) -> bool:
	wanted = {part.strip().lower().lstrip(".") for part in cstr(allowed).split(",") if part.strip()}
	if not wanted:
		return True
	name = cstr(file.get("file_name") or file.get("file_url"))
	return "." in name and name.rsplit(".", 1)[-1].lower() in wanted


def _fire(rows: list[dict], doctype: str, docname: str, doc: Any = None, file: str | None = None) -> None:
	user = frappe.session.user
	for row in rows:
		# A trigger deleted since the index was built is simply not there.
		if not frappe.db.exists(TRIGGER, row["name"]):
			continue
		try:
			_fire_one(frappe.get_cached_doc(TRIGGER, row["name"]), user, doctype, docname, doc, file)
		except Exception:
			frappe.logger("frappe_agents").error(f"trigger {row['name']} could not fire", exc_info=True)


def _fire_one(trigger: Any, user: str, doctype: str, docname: str, doc: Any, file: str | None) -> None:
	if doc is not None and not conditions_hold(trigger, doc):
		return
	if not user or user in FORBIDDEN_USERS:
		count_skip(trigger.name, SKIP_NO_USER)
		return
	if not take_slot(trigger, user):
		count_skip(trigger.name, SKIP_RATE)
		return
	enqueue_run(trigger.name, doctype, docname, user, file)


def enqueue_run(trigger: str, doctype: str, docname: str, user: str, file: str | None = None) -> None:
	frappe.enqueue(
		"frappe_agents.triggers.dispatch.run_trigger",
		queue=QUEUE,
		timeout=JOB_TIMEOUT,
		enqueue_after_commit=True,
		trigger=trigger,
		doctype=doctype,
		docname=docname,
		fired_by=user,
		file=file,
	)


def conditions_hold(trigger: Any, doc: Any) -> bool:
	"""Every condition row on the trigger holds on this document.

	Compared after casting both sides to the field's own type, so `amount > 50`
	compares numbers and `status = Open` compares text, whatever the row stored.
	"""
	meta = frappe.get_meta(doc.doctype)
	for row in trigger.get("conditions") or []:
		df = meta.get_field(row.fieldname)
		if not _holds(doc.get(row.fieldname), row.condition, row.value, df.fieldtype if df else None):
			return False
	return True


def _holds(actual: Any, condition: str, expected: Any, fieldtype: str | None) -> bool:
	if condition == "is":
		wanted_set = cstr(expected).strip().lower() == "set"
		return _is_empty(actual) != wanted_set
	if condition in ("in", "not in"):
		options = [part.strip() for part in cstr(expected).split(",") if part.strip()]
		found = cstr(actual) in options
		return found if condition == "in" else not found
	if condition in ("like", "not like"):
		needle = cstr(expected).replace("%", "").lower()
		found = needle in cstr(actual).lower()
		return found if condition == "like" else not found

	if fieldtype:
		actual, expected = cast(fieldtype, actual), cast(fieldtype, expected)
	else:
		actual, expected = cstr(actual), cstr(expected)
	compare = COMPARISONS.get(condition)
	try:
		return bool(compare and compare(actual, expected))
	except TypeError:
		# A value of the wrong shape cannot meet a condition. Never fire on doubt.
		return False


# --- the cached index -------------------------------------------------------


def trigger_index() -> dict:
	"""Enabled triggers, by the doctype they watch.

	Cleared when a trigger is saved, and again once that save commits: a save on
	another worker in between would otherwise rebuild it from the rows as they were
	before the commit. It expires on its own as well, as the floor under both.
	"""
	index = frappe.cache.get_value(INDEX_KEY)
	if index is None:
		index = _build_index()
		frappe.cache.set_value(INDEX_KEY, index, expires_in_sec=INDEX_SECONDS)
	return index or {}


def clear_index() -> None:
	frappe.cache.delete_value(INDEX_KEY)


def clear_index_after_commit() -> None:
	clear_index()
	frappe.db.after_commit.add(clear_index)


def _build_index() -> dict:
	if not frappe.db.table_exists(TRIGGER):
		return {}
	# Runtime code, not tool code: the index is the same for every user and holds
	# no document content, only which doctypes have a trigger at all.
	rows = frappe.get_all(
		TRIGGER,
		filters={"enabled": 1},
		fields=["name", "document_type", "event", "source_field", "file_extensions"],
	)
	index: dict[str, list[dict]] = {}
	for row in rows:
		index.setdefault(row.document_type, []).append(dict(row))
	return index


# --- the job ----------------------------------------------------------------


def run_trigger(trigger: str, doctype: str, docname: str, fired_by: str, file: str | None = None) -> None:
	"""RQ job: decide whether one event becomes a run, and queue it. Never raises."""
	original = frappe.session.user
	try:
		_run_trigger(trigger, doctype, docname, fired_by, file)
	except Exception:
		frappe.logger("frappe_agents").error(
			f"trigger {trigger} could not start on {doctype} {docname}", exc_info=True
		)
	finally:
		frappe.set_user(original)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser


def _run_trigger(trigger: str, doctype: str, docname: str, fired_by: str, file: str | None) -> None:
	if not frappe.db.exists(TRIGGER, trigger):
		return
	doc = frappe.get_doc(TRIGGER, trigger)
	if not cint(doc.enabled):
		return
	if not fired_by or fired_by in FORBIDDEN_USERS:
		count_skip(doc.name, SKIP_NO_USER)
		return
	if not runtime_enabled():
		count_skip(doc.name, SKIP_SWITCH)
		return

	agent = frappe.get_cached_doc("Agent", doc.agent)
	if not cint(agent.enabled) or agent.run_as != SESSION_USER:
		count_skip(doc.name, SKIP_AGENT)
		return
	if not user_may_use(agent, fired_by):
		count_skip(doc.name, SKIP_ROLE)
		return
	if over_budget(doc, agent):
		count_skip(doc.name, SKIP_BUDGET)
		return

	# Everything after this reads the document as the person who fired the event.
	frappe.set_user(fired_by)  # nosemgrep: frappe-semgrep-rules.rules.security.frappe-setuser

	if not _conditions_hold(doc, doctype, docname):
		return

	if not take_lock(doc.name, doctype, docname):
		# A run is already working on this document. Leave this event for it; the
		# run in flight queues one more when it ends.
		mark_dirty(doc.name, doctype, docname, fired_by, file)
		if not take_lock(doc.name, doctype, docname):
			return
		# That run ended between the two asks and found nothing waiting. This
		# event is the one to run, so take it back, and leave the rest waiting.
		pending = take_dirty(doc.name, doctype, docname) or {}
		_wait_again(doc.name, doctype, docname, pending, running=file)

	try:
		run = _insert_run(doc, agent, doctype, docname, fired_by, file)
	except Exception:
		release_lock(doc.name, doctype, docname)
		raise

	frappe.enqueue(
		"frappe_agents.runner.run.execute_run",
		queue=QUEUE,
		enqueue_after_commit=True,
		run_name=run.name,
	)


def _conditions_hold(trigger: Any, doctype: str, docname: str) -> bool:
	"""The trigger's conditions, read off the document as the person it runs for.

	The hook checked them already for a save, where the document was in hand. They
	are asked again here because an attachment arrives on the File, a manual run
	arrives with no save at all, and a document can change while the job waits. A
	document the person cannot read fires nothing.
	"""
	if not trigger.get("conditions"):
		return True
	if not frappe.db.exists(doctype, docname):
		return False
	if not frappe.has_permission(doctype, "read", doc=docname):
		return False
	return conditions_hold(trigger, frappe.get_doc(doctype, docname))


def _insert_run(trigger: Any, agent: Any, doctype: str, docname: str, fired_by: str, file: str | None) -> Any:
	"""The Agent Run row, owned by the person who fired the event.

	The input message is a placeholder until the run starts: what the model is
	given is read then, as that person, and written over this.
	"""
	run = frappe.get_doc(
		{
			"doctype": "Agent Run",
			"agent": agent.name,
			"effective_user": fired_by,
			"run_as_mode": SESSION_USER,
			"surface": SURFACE,
			"trigger": trigger.name,
			"trigger_file": file,
			"status": "Queued",
			"depth": 0,
			"input_message": _("{0} on {1} {2}").format(trigger.event, doctype, docname),
			"context_doctype": doctype,
			"context_name": docname,
		}
	)
	run.flags.ignore_permissions = True
	run.insert(ignore_permissions=True)
	return run


def release(run: Any) -> None:
	"""Let go of the document when a triggered run ends, and queue the event it missed.

	Called from `execute_run`'s finally block, on every way out. Never raises.
	"""
	try:
		trigger = run.get("trigger")
		doctype, docname = run.get("context_doctype"), run.get("context_name")
		if not trigger or not doctype or not docname:
			return
		release_lock(trigger, doctype, docname)
		missed = take_dirty(trigger, doctype, docname)
		if not missed:
			return
		# One more run: the oldest waiting file, as the person who attached it, or
		# else the newest field event. Everything else waits for the run after that.
		files = waiting_files(missed)
		if files:
			first = files[0]
			enqueue_run(trigger, doctype, docname, first["user"], first["file"])
			_wait_again(trigger, doctype, docname, missed, running=first["file"])
		elif missed.get("user"):
			enqueue_run(trigger, doctype, docname, missed["user"], None)
	except Exception:
		frappe.logger("frappe_agents").error(f"could not release trigger run {run.name}", exc_info=True)


# --- who, how often, how much -----------------------------------------------


def user_may_use(agent: Any, user: str) -> bool:
	"""The agent's Allowed Roles, against the person who fired the event.

	Agent User is not asked for. It gates the chat surface; requiring it here
	would make the same document fill in or not depending on who saved it, and
	push every admin into handing the role to everyone.
	"""
	allowed = {row.role for row in (agent.get("allowed_roles") or []) if row.role}
	return not allowed or bool(allowed & set(frappe.get_roles(user)))


def over_budget(trigger: Any, agent: Any) -> bool:
	"""The agent's daily budget, and the trigger's own, if it sets one.

	The trigger's own cap is what keeps a storm of events from spending the
	agent's whole day and leaving chat with nothing.
	"""
	settings = frappe.get_cached_doc("Agent Settings")
	budget = cint(agent.daily_token_budget) or cint(settings.default_daily_token_budget)
	if budget and tokens_today("agent", agent.name) >= budget:
		return True
	own = cint(trigger.get("daily_token_budget"))
	return bool(own and tokens_today("trigger", trigger.name) >= own)


def tokens_today(field: str, value: str) -> int:
	run = frappe.qb.DocType("Agent Run")
	rows = (
		frappe.qb.from_(run)
		.select(Sum(run.tokens_in), Sum(run.tokens_out))
		.where((run[field] == value) & (run.creation >= today()))
	).run()
	tokens_in, tokens_out = rows[0] if rows else (0, 0)
	return cint(tokens_in) + cint(tokens_out)


def take_slot(trigger: Any, user: str) -> bool:
	"""Count this event against the trigger's hourly caps. False when either is full."""
	hour = now_datetime().strftime("%Y%m%d%H")
	per_trigger = cint(trigger.get("max_runs_per_hour")) or DEFAULT_RUNS_PER_HOUR
	per_user = cint(trigger.get("max_runs_per_user_per_hour")) or DEFAULT_RUNS_PER_USER_PER_HOUR
	# The person's own cap first. Only an event they are still allowed spends the
	# trigger's shared one, so one busy person cannot use up everyone's hour.
	mine = _key(f"rate:{trigger.name}:{user}:{hour}")
	if _incr_key(mine, 3600) > per_user:
		return False
	if _incr(f"rate:{trigger.name}:{hour}", 3600) > per_trigger:
		_raw().decr(mine)
		return False
	return True


def count_skip(trigger: str, reason: str) -> None:
	try:
		_incr(f"skip:{trigger}:{today()}:{reason}", SKIP_SECONDS)
	except Exception:
		frappe.logger("frappe_agents").warning("could not count a skipped trigger", exc_info=True)


def skips_today(trigger: str) -> dict[str, int]:
	"""What this trigger skipped today, by reason. Only the reasons that happened."""
	counts = {}
	for reason in SKIP_REASONS:
		value = cint(_raw().get(_key(f"skip:{trigger}:{today()}:{reason}")))
		if value:
			counts[reason] = value
	return counts


# --- one run per document ---------------------------------------------------


def take_lock(trigger: str, doctype: str, docname: str) -> bool:
	return bool(_raw().set(_key(_doc_key("lock", trigger, doctype, docname)), 1, nx=True, ex=LOCK_SECONDS))


def release_lock(trigger: str, doctype: str, docname: str) -> None:
	_raw().delete(_key(_doc_key("lock", trigger, doctype, docname)))


def mark_dirty(trigger: str, doctype: str, docname: str, user: str, file: str | None) -> None:
	"""Leave an event for the run in flight.

	A field event needs remembering once: the run it queues reads the newest state,
	as the newest person. Files are different. Three uploaded together are three
	files to read, and each is read as the person who attached it — never as
	whoever happened to attach the last one.
	"""
	key = _key(_doc_key("dirty", trigger, doctype, docname))
	raw = _raw().get(key)
	pending = json.loads(raw) if raw else {}
	files = waiting_files(pending)
	if file:
		if file not in {waiting["file"] for waiting in files} and len(files) < MAX_WAITING_FILES:
			files.append({"file": file, "user": user})
	else:
		pending["user"] = user
	_raw().set(key, json.dumps({"user": pending.get("user"), "files": files}), ex=LOCK_SECONDS)


def waiting_files(pending: dict) -> list[dict]:
	"""The waiting files, each with the person who attached it."""
	files = []
	for waiting in pending.get("files") or []:
		if isinstance(waiting, dict) and waiting.get("file") and waiting.get("user"):
			files.append({"file": waiting["file"], "user": waiting["user"]})
	return files


def _wait_again(trigger: str, doctype: str, docname: str, pending: dict, running: str | None) -> None:
	"""Put back what was waiting, except the event about to run, each with its own person."""
	for waiting in waiting_files(pending):
		if waiting["file"] != running:
			mark_dirty(trigger, doctype, docname, waiting["user"], waiting["file"])
	if running and pending.get("user"):
		# A file runs now; a field event that was waiting still needs its own run.
		mark_dirty(trigger, doctype, docname, pending["user"], None)


def take_dirty(trigger: str, doctype: str, docname: str) -> dict | None:
	"""Read and clear what was left waiting, in one step."""
	value = _raw().pop(_key(_doc_key("dirty", trigger, doctype, docname)))
	return json.loads(value) if value else None


def _doc_key(kind: str, trigger: str, doctype: str, docname: str) -> str:
	return f"{kind}:{trigger}:{doctype}:{docname}"


# --- redis ------------------------------------------------------------------
#
# Counters and locks are plain redis values, so they go through the plain redis
# methods. frappe's own get_value and set_value pickle what they store, which an
# INCR cannot add to.


def _raw() -> Any:
	return _RawRedis(frappe.cache)


class _RawRedis:
	"""The unpickled redis commands, on frappe's own connection."""

	def __init__(self, cache: Any) -> None:
		self.cache = cache

	def get(self, key: str) -> Any:
		return redis.Redis.get(self.cache, key)

	def set(self, key: str, value: Any, **kwargs: Any) -> Any:
		return redis.Redis.set(self.cache, key, value, **kwargs)

	def delete(self, key: str) -> Any:
		return redis.Redis.delete(self.cache, key)

	def pop(self, key: str) -> Any:
		"""GET and DELETE as one transaction, for servers older than GETDEL."""
		pipe = redis.Redis.pipeline(self.cache, transaction=True)
		pipe.get(key)
		pipe.delete(key)
		value, _ = pipe.execute()
		return value

	def incr(self, key: str) -> int:
		return redis.Redis.incr(self.cache, key)

	def decr(self, key: str) -> int:
		return redis.Redis.decr(self.cache, key)

	def expire(self, key: str, seconds: int) -> Any:
		return redis.Redis.expire(self.cache, key, seconds)


def _key(name: str) -> str:
	return frappe.cache.make_key(f"{KEY_PREFIX}:{name}")


def _incr(name: str, seconds: int) -> int:
	return _incr_key(_key(name), seconds)


def _incr_key(key: str, seconds: int) -> int:
	value = _raw().incr(key)
	if value == 1:
		_raw().expire(key, seconds)
	return value


def _is_empty(value: Any) -> bool:
	"""Nothing worth reacting to. A 0 in a number or check field is its default."""
	if value is None or value == 0:
		return True
	if isinstance(value, str):
		return not value.strip()
	if isinstance(value, list | tuple):
		return not value
	return False


# --- a run asked for by hand ------------------------------------------------


@frappe.whitelist(methods=["POST"])
def run_now(trigger: str, docname: str) -> dict:
	"""Fire a trigger on one document now, as the person asking.

	For the events the caps dropped, and for the documents that existed before the
	trigger did. It is the same job a real event queues, with the same checks and
	the same caps, run as the session user — never as anyone else.
	"""
	frappe.has_permission(TRIGGER, "write", doc=trigger, throw=True)
	doc = frappe.get_doc(TRIGGER, trigger)
	if not cint(doc.enabled):
		frappe.throw(_("Trigger {0} is disabled.").format(doc.name))

	user = frappe.session.user
	if user in FORBIDDEN_USERS:
		frappe.throw(_("A trigger never runs as {0}. Sign in as the person it should run for.").format(user))

	frappe.has_permission(doc.document_type, "write", doc=docname, throw=True)
	if not conditions_hold(doc, frappe.get_doc(doc.document_type, docname)):
		frappe.throw(_("{0} {1} does not meet the trigger's conditions.").format(doc.document_type, docname))

	file = None
	if doc.event == EVENT_ATTACHMENT:
		file = _latest_attachment(doc, docname)
		if not file:
			frappe.throw(
				_("{0} {1} has no attachment this trigger reads.").format(doc.document_type, docname)
			)

	if not take_slot(doc, user):
		frappe.throw(_("Trigger {0} has reached its hourly limit. Try again later.").format(doc.name))

	enqueue_run(doc.name, doc.document_type, docname, user, file)
	return {"queued": True}


def _latest_attachment(trigger: Any, docname: str) -> str | None:
	files = frappe.get_list(
		FILE,
		filters={
			"attached_to_doctype": trigger.document_type,
			"attached_to_name": docname,
			"is_folder": 0,
		},
		fields=["name", "file_name", "file_url"],
		order_by="creation desc",
		limit_page_length=20,
	)
	for row in files:
		if cstr(row.file_url).startswith(LOCAL_FILE_PREFIXES) and _extension_allowed(
			row, trigger.file_extensions
		):
			return row.name
	return None
