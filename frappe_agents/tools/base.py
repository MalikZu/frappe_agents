"""Tool execution: capability gate, kill switch, and the audit row.

Every tool call an agent makes goes through `execute_tool`. It always writes one
Agent Tool Call row — success, denial or error — before it returns.

The row is durable and Agent Auditor reads it, so it is written with sensitive
values held back. What a tool *did* stays fully legible — the tool, the doctype,
the field names, the outcome — and only the values that must not become audit
content are replaced with `[redacted]`. See `_sensitive_keys` for what counts.
Redaction happens here, at the write, and nowhere else: the tool's own return
value is handed to the model untouched.
"""

import inspect
import time
from typing import Any

import frappe
from frappe.utils import cint

CAPABILITY_READ = "Read"
CAPABILITY_DRAFT = "Draft"
CAPABILITY_WRITE = "Write"
CAPABILITY_SUBMIT = "Submit"

OUTCOME_SUCCESS = "Success"
OUTCOME_DENIED = "Denied"
OUTCOME_ERROR = "Error"

RUN_FLAG = "agent_current_run"

INTERRUPTED = "The tool call was interrupted before it finished."

SETTINGS = "Agent Settings"
KILL_SWITCH_FIELD = "global_enabled"
# Where the switch is published so that a job already running can see it move.
KILL_SWITCH_KEY = "agent_runtime_enabled"

ARGS_JSON_LIMIT = 10_000
RESULT_SUMMARY_LIMIT = 500
DOCS_TOUCHED_LIMIT = 500
ERROR_LIMIT = 500

REDACTED = "[redacted]"

# Key names redacted whatever doctype is in play — the answer for arguments that
# name no doctype at all. Deliberately tiny and exact: a rule wide enough to
# catch every secret by its name would redact half the audit trail, and a row an
# auditor cannot read is its own kind of failure.
FALLBACK_SENSITIVE_KEYS = frozenset({"password", "api_key", "token", "secret"})

# Below this length a redacted value is not worth removing from prose. "12" or
# "abc" appears inside ordinary words, and replacing every occurrence would
# corrupt the sentence that tells an auditor what happened.
SCRUB_MIN_LENGTH = 4

# What each autonomy level may do. Submit is never granted to an agent:
# agents draft, humans submit.
AUTONOMY_CAPABILITIES = {
	"Suggest": {CAPABILITY_READ},
	"Draft": {CAPABILITY_READ, CAPABILITY_DRAFT},
	"Write": {CAPABILITY_READ, CAPABILITY_DRAFT, CAPABILITY_WRITE},
}


class ToolDenied(Exception):
	"""The effective user, the agent, or the runtime is not allowed to do this."""


class KillSwitchActive(ToolDenied):
	"""Global kill switch is off. Aborts the run — never handed back to the model."""


def require_grant(target: str, verb: str, target_type: str = "DocType") -> None:
	"""Refuse unless the running agent's access rules allow this verb on this target.

	The chokepoint. Every generic tool asks here and nowhere else, beside the
	frappe permission check it already makes — user permissions ∩ access rules,
	never one instead of the other. The rules live in `frappe_agents.access.grants`.
	"""
	_grants().require_grant(target, verb, target_type)


def has_grant(target: str, verb: str, target_type: str = "DocType") -> bool:
	"""Whether `require_grant` would pass, for a caller that omits rather than refuses."""
	return _grants().has_grant(target, verb, target_type)


def require_draft_ownership(doctype: str, doc: Any) -> None:
	"""Refuse a draft somebody else created unless the rule allows any draft."""
	_grants().require_draft_ownership(doctype, doc)


def require_file_access() -> None:
	"""Refuse reading attached files unless the agent carries `may_read_files`."""
	_grants().require_file_access()


def require_blueprint_drafting() -> None:
	"""Refuse the builder's meta tools unless the agent may draft Agent Blueprints."""
	_grants().require_blueprint_drafting()


def row_cap(target: str, tool_max: int, target_type: str = "DocType") -> int:
	"""The tool's own row cap, narrowed by the rule's cap when it sets one."""
	return _grants().row_cap(target, tool_max, target_type)


def _grants() -> Any:
	# Imported on use: `access.grants` needs ToolDenied from this module, and a
	# module-level import here would close the circle.
	from frappe_agents.access import grants

	return grants


def execute_tool(run: Any, tool_name: str, args: dict | None = None) -> dict:
	"""Run one tool for one Agent Run and log it.

	`run` is an Agent Run document or its name. Returns
	`{"ok": bool, "result": any, "error": str | None}`. Handler failures come back
	as data so the model can react; only the kill switch is raised.
	"""
	args = args if isinstance(args, dict) else {}
	started = time.monotonic()

	run_doc = frappe.get_doc("Agent Run", run) if isinstance(run, str) else run
	run_name = run_doc.name

	try:
		_check_kill_switch()
	except KillSwitchActive as exc:
		_log_call(
			run_name,
			tool_name,
			args,
			OUTCOME_DENIED,
			_elapsed_ms(started),
			error=str(exc),
		)
		raise

	docs_touched = None
	summary = None
	result: Any = None
	# A handler that records something — a proposal, an audit row — has to say which
	# run asked for it. The run is the caller's, not the handler's argument, so it
	# travels beside the call rather than inside the model's payload.
	previous_run = frappe.flags.get(RUN_FLAG)
	frappe.flags[RUN_FLAG] = run_doc
	try:
		handler = _resolve_handler(run_doc, tool_name)
		result = handler(args)
		if isinstance(result, dict):
			docs_touched = result.pop("_docs_touched", None)
			# A handler that knows what its own row should say says so. Everything
			# else is summarised from the shape of the result.
			summary = result.pop("_result_summary", None)
		outcome, error = OUTCOME_SUCCESS, None
	except (ToolDenied, frappe.PermissionError) as exc:
		outcome, result, error = OUTCOME_DENIED, None, _message(exc)
	except Exception as exc:
		outcome, result, error = OUTCOME_ERROR, None, _message(exc)
		frappe.logger("frappe_agents").warning(f"tool {tool_name} failed on run {run_name}: {error}")
	finally:
		frappe.flags[RUN_FLAG] = previous_run

	_log_call(
		run_name,
		tool_name,
		args,
		outcome,
		_elapsed_ms(started),
		result=result,
		result_summary=summary,
		docs_touched=docs_touched,
		error=error,
	)

	return {"ok": outcome == OUTCOME_SUCCESS, "result": result, "error": error}


def log_interrupted_call(run: Any, tool_name: str, args: dict | None = None) -> None:
	"""Audit a tool call that was killed before `execute_tool` could log it.

	`execute_tool` always writes its own row, so this is only reached when the
	call itself was torn down — the run was cancelled while the tool was in
	flight. The row exists so that an auditor sees the attempt rather than a gap.
	"""
	run_name = run if isinstance(run, str) else run.name
	_log_call(run_name, tool_name, args or {}, OUTCOME_ERROR, 0, error=INTERRUPTED)


def current_run() -> Any:
	"""The Agent Run this tool call belongs to, or None outside a run."""
	return frappe.flags.get(RUN_FLAG)


def run_model_profile() -> str | None:
	"""The model profile this run is on, or None outside a run.

	A model call a tool makes on the user's behalf — reading a scan, extracting a
	document — is the run's call, so it uses the run's model: the override if the
	run carries one, otherwise the agent's own. None means nobody chose, and the
	caller decides what to do about that.
	"""
	run = current_run()
	if run is None or not run.get("agent"):
		return None
	if run.get("model_profile"):
		return run.model_profile
	agent = frappe.get_cached_doc("Agent", run.agent)
	return agent.model_profile or None


def runtime_enabled() -> bool:
	"""Whether the agent runtime is switched on, read the way a live run needs it.

	The switch is only worth having if a run already in flight sees it move, and
	neither obvious read does:

	* the cached Single does not. `frappe.get_cached_doc` keeps the document in
	  this process, and the save that cleared the cache only cleared the process
	  that saved.
	* an uncached query on this job's own connection does not either. The job
	  holds one transaction from start to end, and the database pins what that
	  transaction sees at its first read — a value another connection committed
	  afterwards is invisible to it however often it asks.

	So the switch is published to the shared cache every time Agent Settings is
	saved, and the runtime is on only when nothing says it is off. Either read
	alone can be behind; neither can be behind in the direction that keeps a
	stopped runtime running.
	"""
	if not _stored_switch():
		return False
	published = _published_switch()
	return True if published is None else published


def publish_kill_switch(enabled: Any) -> None:
	"""Publish the switch to every process. Agent Settings calls this when saved."""
	try:
		frappe.cache.set_value(KILL_SWITCH_KEY, cint(enabled))
	except Exception:
		frappe.logger("frappe_agents").warning("could not publish the kill switch", exc_info=True)


def _stored_switch() -> bool:
	"""The switch as the database holds it, past the document cache and its memo.

	get_single_value is not usable here: it memoizes into db.value_cache, which
	only clears on commit or rollback — and a background job holds one transaction
	for its whole life, so the memo would pin the switch for the entire run.
	"""
	value = frappe.db.get_value(  # nosemgrep: frappe-semgrep-rules.rules.frappe-single-value-type-safety
		SETTINGS, None, KILL_SWITCH_FIELD
	)
	if value is None:
		# Never saved on this site, so the field's own default stands.
		field = frappe.get_meta(SETTINGS).get_field(KILL_SWITCH_FIELD)
		value = field.default if field else 0
	return bool(cint(value))


def _published_switch() -> bool | None:
	"""The switch as the last save left it, or None when nothing has published it."""
	try:
		value = _read_published()
	except Exception:
		# Redis being unreachable leaves the stored switch in charge, which is the
		# safe direction. It is logged rather than swallowed: a published half that
		# silently never answers is a kill switch that silently never stops a job.
		frappe.logger("frappe_agents").warning("could not read the published kill switch", exc_info=True)
		return None
	return None if value is None else bool(cint(value))


def _read_published() -> Any:
	"""Read the published switch past every process-local memo.

	The whole point of publishing is that a job already running sees the switch
	move, so a cached answer is the one thing this read must not give.

	v16 says that with `use_local_cache=False`. v15 has no such argument: its
	`get_value` consults `frappe.local.cache` first and memoizes what it read, so
	a worker that read the switch once would keep answering with that first value
	for the rest of the run. There, the memo is dropped before the read and
	`expires=True` stops the read putting it back.
	"""
	getter = frappe.cache.get_value
	if _accepts(getter, "use_local_cache"):
		return getter(KILL_SWITCH_KEY, use_local_cache=False)
	frappe.local.cache.pop(frappe.cache.make_key(KILL_SWITCH_KEY), None)
	return getter(KILL_SWITCH_KEY, expires=True)


# Named rather than written inline: `ruff format` on a py314 target rewrites an
# inline `except (A, B):` into PEP 758 `except A, B:`, which the version-15
# branch cannot parse. A name is not a tuple literal, so both formatters leave
# it alone and the same source works on either Python.
SIGNATURE_ERRORS = (TypeError, ValueError)


def _accepts(method: Any, name: str) -> bool:
	try:
		return name in inspect.signature(method).parameters
	except SIGNATURE_ERRORS:
		return False


def _check_kill_switch() -> None:
	if not runtime_enabled():
		raise KillSwitchActive("The agent runtime is switched off.")


def _resolve_handler(run_doc: Any, tool_name: str):
	if not tool_name:
		raise ValueError("No tool name given.")

	agent = frappe.get_cached_doc("Agent", run_doc.agent) if run_doc.get("agent") else None
	if agent is not None:
		# The model may only call what the agent record grants it.
		from frappe_agents.tools.registry import get_agent_tool_names

		if tool_name not in get_agent_tool_names(agent):
			raise ToolDenied(f"Tool {tool_name} is not enabled for agent {agent.name}.")

	try:
		tool = frappe.get_cached_doc("Agent Tool", tool_name)
	except frappe.DoesNotExistError:
		raise ValueError(f"Unknown tool: {tool_name}")

	if not cint(tool.enabled):
		raise ToolDenied(f"Tool {tool_name} is disabled.")

	if agent is not None:
		allowed = AUTONOMY_CAPABILITIES.get(agent.autonomy, {CAPABILITY_READ})
		if tool.capability not in allowed:
			raise ToolDenied(
				f"Tool {tool_name} needs {tool.capability} capability, agent autonomy is {agent.autonomy}."
			)

	return frappe.get_attr(tool.handler_path)


def _log_call(
	run_name: str,
	tool_name: str,
	args: dict,
	outcome: str,
	duration_ms: int,
	result: Any = None,
	result_summary: str | None = None,
	docs_touched: str | None = None,
	error: str | None = None,
) -> None:
	"""Write the one row for this call, with sensitive values held back.

	The arguments are redacted by key. The summary and the error are prose, so
	they are scrubbed by value instead: whatever was redacted out of the
	arguments or the result is removed from the text as well, because a handler
	that quotes a bad value back at the model would otherwise put it in the row
	the arguments no longer carry.
	"""
	try:
		keys = _sensitive_keys(args)
		safe_args, secrets = _redact(args, keys)

		summary = None
		if outcome == OUTCOME_SUCCESS:
			if result_summary:
				summary = result_summary
			else:
				safe_result, result_secrets = _redact(result, keys)
				secrets |= result_secrets
				summary = _summarise(safe_result)

		call = frappe.get_doc(
			{
				"doctype": "Agent Tool Call",
				"run": run_name,
				"tool": tool_name or "unknown",
				"args_json": _truncate(_as_json(safe_args), ARGS_JSON_LIMIT),
				"outcome": outcome,
				"result_summary": _truncate(_scrub(summary, secrets), RESULT_SUMMARY_LIMIT),
				"docs_touched": _truncate(docs_touched, DOCS_TOUCHED_LIMIT),
				"duration_ms": duration_ms,
				"error": _truncate(_scrub(error, secrets), ERROR_LIMIT),
			}
		)
		call.flags.ignore_permissions = True
		call.insert(ignore_permissions=True)
	except Exception:
		frappe.logger("frappe_agents").error(
			f"could not log tool call {tool_name} for run {run_name}", exc_info=True
		)


def _sensitive_keys(payload: Any) -> set[str]:
	"""Argument keys whose values must not become audit content, lowercased.

	Three sources, in the order they are trusted:

	1. **Password fields** on the doctype the call names. A fieldtype the
	   framework itself refuses to hand back in a query is not something to
	   copy into an audit row.
	2. **Masked and administrator-marked fields** on that same doctype — the
	   extraction gate's own configuration, read through `sensitive_fieldnames`
	   so there is one registry of what an administrator called sensitive and
	   not two that can disagree.
	3. **A fallback by name** for arguments tied to no doctype at all.

	Child tables count: a payment detail hidden in a line item is the same
	value with more steps, and the keys are matched at any depth.
	"""
	keys = set(FALLBACK_SENSITIVE_KEYS)
	doctype = payload.get("doctype") if isinstance(payload, dict) else None
	if not isinstance(doctype, str) or not doctype.strip():
		return keys

	try:
		keys |= _doctype_sensitive_keys(doctype.strip())
	except frappe.DoesNotExistError:
		# The call named a doctype that does not exist — the tool is about to say
		# so. The fallback keys still apply.
		pass
	except Exception:
		frappe.logger("frappe_agents").warning(
			f"could not resolve sensitive fields on {doctype}", exc_info=True
		)
	return keys


def _doctype_sensitive_keys(doctype: str) -> set[str]:
	from frappe.model import table_fields

	from frappe_agents.extraction.gate import sensitive_fieldnames

	meta = frappe.get_meta(doctype)
	metas = [meta]
	metas += [
		frappe.get_meta(df.options) for df in meta.fields if df.fieldtype in table_fields and df.options
	]

	keys: set[str] = set()
	for one in metas:
		keys |= {df.fieldname for df in one.fields if df.fieldtype == "Password"}
		keys |= _masked_fieldnames(one)
		keys |= sensitive_fieldnames(one.name)
	return {key.lower() for key in keys if key}


def masking_supported() -> bool:
	"""Whether this framework masks fields per user.

	frappe_agents patch (version-15): `Meta.get_masked_fields` arrived in v16.
	Where it is missing, a field an administrator masked is not automatically
	held out of an audit row, and the extraction gate has one fewer source for
	what to withhold. Nothing crashes — the protection is simply not there, and
	`docs/admin.md` says so. Agent Settings' own sensitive-field list works on
	both and is what to lean on.
	"""
	from frappe.model.meta import Meta

	return hasattr(Meta, "get_masked_fields")


def _masked_fieldnames(meta: Any) -> set[str]:
	try:
		return {df.fieldname for df in meta.get_masked_fields()}
	except AttributeError:
		return set()


def _redact(value: Any, keys: set[str]) -> tuple[Any, set[str]]:
	"""A copy with sensitive values replaced, and the raw values that were removed.

	Recursive over dicts and lists, so a draft's child rows and a filter object
	are covered by the same pass. Key names survive: what the call touched is the
	part an auditor needs.
	"""
	secrets: set[str] = set()
	return _walk(value, keys, secrets), secrets


def _walk(value: Any, keys: set[str], secrets: set[str]) -> Any:
	if isinstance(value, dict):
		clean = {}
		for key, item in value.items():
			if isinstance(key, str) and key.lower() in keys:
				_collect(item, secrets)
				clean[key] = REDACTED
				continue
			clean[key] = _walk(item, keys, secrets)
		return clean
	if isinstance(value, (list, tuple)):
		return [_walk(item, keys, secrets) for item in value]
	return value


def _collect(value: Any, secrets: set[str]) -> None:
	"""Every string under a redacted key, so prose can be scrubbed of it later."""
	if isinstance(value, str):
		secrets.add(value)
	elif isinstance(value, dict):
		for item in value.values():
			_collect(item, secrets)
	elif isinstance(value, (list, tuple)):
		for item in value:
			_collect(item, secrets)


def _scrub(text: str | None, secrets: set[str]) -> str | None:
	if not text or not secrets:
		return text
	for secret in secrets:
		if len(secret) >= SCRUB_MIN_LENGTH and secret in text:
			text = text.replace(secret, REDACTED)
	return text


def _summarise(result: Any) -> str:
	if isinstance(result, dict):
		rows = result.get("rows")
		if isinstance(rows, list):
			summary = f"{len(rows)} row(s)"
			if result.get("truncated"):
				summary += f" of {result.get('total_rows')}"
			return summary
		fields = result.get("fields")
		if isinstance(fields, list):
			return f"{len(fields)} field(s)"
	return _truncate(_as_json(result), RESULT_SUMMARY_LIMIT) or ""


def _as_json(value: Any) -> str:
	try:
		return frappe.as_json(value)
	except Exception:
		return str(value)


def _truncate(text: str | None, limit: int) -> str | None:
	if text is None:
		return None
	text = str(text)
	if len(text) <= limit:
		return text
	return text[: limit - 3] + "..."


def _message(exc: Exception) -> str:
	return str(exc) or exc.__class__.__name__


def _elapsed_ms(started: float) -> int:
	return int((time.monotonic() - started) * 1000)
