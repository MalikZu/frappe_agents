"""The run loop.

`execute_run` is the RQ job. It binds the effective user, refuses to run as
Administrator or Guest, calls the model, executes whatever tools the model asks
for, and writes the outcome back onto the Agent Run.

One attempt per run: failures are recorded on the run, never re-raised into a
retry, because a retry would repeat the side effects of every tool already called.
"""

from typing import Any

import frappe
from frappe.utils import cint, now_datetime

from frappe_agents.runner.providers import call_model
from frappe_agents.tools.base import KillSwitchActive, execute_tool
from frappe_agents.tools.registry import get_tool_schemas

EVENT = "frappe_agents:run_update"

FORBIDDEN_USERS = ("Administrator", "Guest")
DEFAULT_MAX_STEPS = 10
DEFAULT_MAX_DEPTH = 3
HISTORY_LIMIT = 20
ERROR_LIMIT = 500
TOOL_RESULT_LIMIT = 20_000

TOOL_DISCIPLINE = (
	"You are an assistant working inside a Frappe site.\n"
	"You can only see data through the tools you were given, and every tool call runs "
	"with the permissions of the user you are acting for.\n"
	"If a tool denies you, say so plainly and stop — never guess the data, and never "
	"try another route to the same record.\n"
	"A field returned as '<restricted>' means the user may not see it. Say that; do not "
	"treat it as empty.\n"
	"Use the fewest tool calls that answer the question, then answer in plain words. "
	"Base your answer only on what the tools returned."
)


def execute_run(run_name: str) -> None:
	"""RQ job: execute one Agent Run. Never raises."""
	original_user = frappe.session.user
	run = None
	try:
		run = frappe.get_doc("Agent Run", run_name)
		run.flags.ignore_permissions = True
		_execute(run)
	except Exception as exc:
		frappe.logger("frappe_agents").error(f"run {run_name} failed", exc_info=True)
		if run is not None:
			_fail(run, str(exc) or exc.__class__.__name__)
	finally:
		frappe.set_user(original_user)


def _execute(run: Any) -> None:
	agent = frappe.get_doc("Agent", run.agent)

	# The identity assertion. An agent never runs as Administrator or Guest, and
	# never as a disabled user — no matter what wrote the run row.
	effective_user = run.effective_user
	if not effective_user or effective_user in FORBIDDEN_USERS:
		return _fail(run, f"Refusing to run as {effective_user or 'no user'}.")
	if not cint(frappe.get_cached_value("User", effective_user, "enabled") or 0):
		return _fail(run, f"Effective user {effective_user} is disabled or missing.")

	settings = frappe.get_cached_doc("Agent Settings")
	if not cint(settings.global_enabled):
		return _fail(run, "The agent runtime is switched off.", status="Cancelled")
	if not cint(agent.enabled):
		return _fail(run, f"Agent {agent.name} is disabled.")

	max_depth = cint(settings.max_depth) or DEFAULT_MAX_DEPTH
	if cint(run.depth) > max_depth:
		return _fail(run, f"Run depth {cint(run.depth)} is over the limit of {max_depth}.")

	frappe.set_user(effective_user)

	_update(run, {"status": "Running", "started_at": now_datetime()})
	_publish(run, "status", status="Running")

	messages = _build_messages(agent, run)
	tool_schemas = get_tool_schemas(agent)
	max_steps = cint(agent.max_steps) or DEFAULT_MAX_STEPS

	tokens_in = 0
	tokens_out = 0
	steps = 0
	final_text = None

	try:
		while steps < max_steps:
			steps += 1
			reply = call_model(agent.model_profile, messages, tool_schemas)
			tokens_in += cint(reply.get("tokens_in"))
			tokens_out += cint(reply.get("tokens_out"))

			calls = reply.get("tool_calls") or []
			if not calls:
				final_text = reply.get("text") or ""
				break

			messages.append(
				{
					"role": "assistant",
					"content": reply.get("text") or "",
					"tool_calls": calls,
				}
			)
			for call in calls:
				result = execute_tool(run, call.get("name"), call.get("args"))
				_publish(
					run,
					"tool_call",
					tool=call.get("name"),
					args=call.get("args") or {},
					ok=result.get("ok"),
					error=result.get("error"),
				)
				messages.append(
					{
						"role": "tool",
						"tool_call_id": call.get("id"),
						"name": call.get("name"),
						"content": _tool_content(result),
					}
				)
	except KillSwitchActive as exc:
		_update(run, {"tokens_in": tokens_in, "tokens_out": tokens_out, "steps_taken": steps})
		return _fail(run, str(exc), status="Cancelled")

	if final_text is None:
		_update(run, {"tokens_in": tokens_in, "tokens_out": tokens_out, "steps_taken": steps})
		return _fail(run, f"Stopped after {steps} steps without a final answer.")

	_update(
		run,
		{
			"status": "Completed",
			"output_message": final_text,
			"ended_at": now_datetime(),
			"tokens_in": tokens_in,
			"tokens_out": tokens_out,
			"steps_taken": steps,
		},
	)
	_publish(run, "message", status="Completed", message=final_text)
	_touch_conversation(run)


def _build_messages(agent: Any, run: Any) -> list[dict]:
	system = TOOL_DISCIPLINE
	if agent.instructions:
		system = f"{agent.instructions.strip()}\n\n{TOOL_DISCIPLINE}"

	messages: list[dict] = [{"role": "system", "content": system}]
	for prior in _history(run):
		if prior.get("input_message"):
			messages.append({"role": "user", "content": prior["input_message"]})
		if prior.get("output_message"):
			messages.append({"role": "assistant", "content": prior["output_message"]})
	messages.append({"role": "user", "content": run.input_message or ""})
	return messages


def _history(run: Any) -> list[dict]:
	if not run.conversation:
		return []
	try:
		rows = frappe.get_list(
			"Agent Run",
			filters={
				"conversation": run.conversation,
				"name": ("!=", run.name),
				"status": "Completed",
			},
			fields=["input_message", "output_message", "creation"],
			order_by="creation desc",
			limit_page_length=HISTORY_LIMIT,
		)
	except frappe.PermissionError:
		return []
	return list(reversed(rows))


def _tool_content(result: dict) -> str:
	content = frappe.as_json(result)
	if len(content) > TOOL_RESULT_LIMIT:
		content = content[:TOOL_RESULT_LIMIT] + "\n... (result truncated)"
	return content


def _touch_conversation(run: Any) -> None:
	if not run.conversation:
		return
	conversation = frappe.get_doc("Agent Conversation", run.conversation)
	conversation.flags.ignore_permissions = True
	conversation.db_set("last_activity", now_datetime(), update_modified=False)


def _update(run: Any, values: dict) -> None:
	# Agent Run has no write permission for any role by design, so the framework
	# writes to it with the ignore_permissions flag set on the document.
	run.flags.ignore_permissions = True
	run.db_set(values, update_modified=False)


def _fail(run: Any, message: str, status: str = "Failed") -> None:
	error = (message or "Unknown error")[:ERROR_LIMIT]
	try:
		_update(run, {"status": status, "error": error, "ended_at": now_datetime()})
	except Exception:
		frappe.logger("frappe_agents").error(f"could not record failure on run {run.name}", exc_info=True)
	_publish(run, "error", status=status, error=error)


def _publish(run: Any, event_type: str, **payload: Any) -> None:
	data = {
		"run": run.name,
		"conversation": run.conversation,
		"agent": run.agent,
		"type": event_type,
	}
	data.update(payload)
	frappe.publish_realtime(EVENT, data, user=run.effective_user)
