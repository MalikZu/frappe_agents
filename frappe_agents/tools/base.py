"""Tool execution: capability gate, kill switch, and the audit row.

Every tool call an agent makes goes through `execute_tool`. It always writes one
Agent Tool Call row — success, denial or error — before it returns.
"""

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

ARGS_JSON_LIMIT = 10_000
RESULT_SUMMARY_LIMIT = 500
DOCS_TOUCHED_LIMIT = 500
ERROR_LIMIT = 500

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
		result_summary=_summarise(result) if outcome == OUTCOME_SUCCESS else None,
		docs_touched=docs_touched,
		error=error,
	)

	return {"ok": outcome == OUTCOME_SUCCESS, "result": result, "error": error}


def current_run() -> Any:
	"""The Agent Run this tool call belongs to, or None outside a run."""
	return frappe.flags.get(RUN_FLAG)


def _check_kill_switch() -> None:
	settings = frappe.get_cached_doc("Agent Settings")
	if not cint(settings.global_enabled):
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
	result_summary: str | None = None,
	docs_touched: str | None = None,
	error: str | None = None,
) -> None:
	try:
		call = frappe.get_doc(
			{
				"doctype": "Agent Tool Call",
				"run": run_name,
				"tool": tool_name or "unknown",
				"args_json": _truncate(_as_json(args), ARGS_JSON_LIMIT),
				"outcome": outcome,
				"result_summary": _truncate(result_summary, RESULT_SUMMARY_LIMIT),
				"docs_touched": _truncate(docs_touched, DOCS_TOUCHED_LIMIT),
				"duration_ms": duration_ms,
				"error": _truncate(error, ERROR_LIMIT),
			}
		)
		call.flags.ignore_permissions = True
		call.insert(ignore_permissions=True)
	except Exception:
		frappe.logger("frappe_agents").error(
			f"could not log tool call {tool_name} for run {run_name}", exc_info=True
		)


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
