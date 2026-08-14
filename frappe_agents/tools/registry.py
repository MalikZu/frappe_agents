"""Tool registry: code declares tools, Agent Tool rows record them.

Apps declare tool modules in the `agent_tools` hook. Each module exposes a
`TOOLS` list. `sync_tools` runs after migrate and mirrors those declarations into
Agent Tool rows. Rows are never deleted — a tool that goes away is disabled, so
old Agent Tool Call rows keep pointing at something real.

This module is registry maintenance, not tool code: it writes with
`ignore_permissions` on purpose, because no human role may write Agent Tool.
"""

import json
from typing import Any

import frappe
from frappe.utils import cint

from frappe_agents.access.grants import exposed_tool_names

DEFAULT_ARGS_SCHEMA = {"type": "object", "properties": {}}


def sync_tools() -> None:
	"""Upsert Agent Tool rows from every `agent_tools` module. Runs after migrate."""
	declared: dict[str, dict] = {}
	import_failed = False

	for module_path in frappe.get_hooks("agent_tools") or []:
		try:
			module = frappe.get_module(module_path)
		except Exception:
			import_failed = True
			frappe.logger("frappe_agents").error(
				f"could not import agent tool module {module_path}", exc_info=True
			)
			continue

		app = module_path.split(".")[0]
		for spec in getattr(module, "TOOLS", None) or []:
			name = spec.get("tool_name")
			if not name or not spec.get("handler_path"):
				frappe.logger("frappe_agents").warning(
					f"skipping malformed tool declaration in {module_path}: {spec}"
				)
				continue
			declared[name] = dict(spec, provider_app=spec.get("provider_app") or app)

	for name, spec in declared.items():
		_upsert_tool(name, spec)

	if import_failed:
		# A module that would not import may still own live tools. Leave them alone.
		frappe.logger("frappe_agents").warning(
			"skipping stale-tool sweep: at least one agent_tools module failed to import"
		)
	else:
		_disable_undeclared(set(declared))


def agent_tool_order(agent: Any) -> list[str]:
	"""The tools this agent is offered, in the order they are shown and sent.

	The set comes from `exposed_tool_names`: the access matrix decides the generic
	family, the selection table still carries tools other apps registered. The
	order keeps the selection's own order first, so an agent that has not moved to
	the matrix yet is offered exactly what it was offered before.
	"""
	agent = _agent_doc(agent)
	names = exposed_tool_names(agent)

	ordered: list[str] = []
	for row in agent.get("tools") or []:
		if row.tool in names and row.tool not in ordered:
			ordered.append(row.tool)
	return ordered + sorted(names - set(ordered))


def get_tool_schemas(agent: Any) -> list[dict]:
	"""Schemas for the enabled tools this agent may use, for the model request."""
	agent = _agent_doc(agent)
	schemas = []
	for name in agent_tool_order(agent):
		tool = _get_enabled_tool(name)
		if not tool:
			continue
		schemas.append(
			{
				"name": tool.tool_name or tool.name,
				"description": tool.description or "",
				"args_schema": parse_args_schema(tool.args_schema),
			}
		)
	return schemas


def get_agent_tool_names(agent: Any) -> set[str]:
	"""Names of the enabled tools this agent may call."""
	agent = _agent_doc(agent)
	names = set()
	for name in agent_tool_order(agent):
		tool = _get_enabled_tool(name)
		if tool:
			names.add(tool.tool_name or tool.name)
	return names


def _agent_doc(agent: Any) -> Any:
	return frappe.get_cached_doc("Agent", agent) if isinstance(agent, str) else agent


def parse_args_schema(raw: str | dict | None) -> dict:
	if isinstance(raw, dict):
		return raw
	if not raw:
		return dict(DEFAULT_ARGS_SCHEMA)
	try:
		schema = json.loads(raw)
	except Exception:
		return dict(DEFAULT_ARGS_SCHEMA)
	return schema if isinstance(schema, dict) else dict(DEFAULT_ARGS_SCHEMA)


def _get_enabled_tool(tool_name: str | None):
	if not tool_name:
		return None
	try:
		tool = frappe.get_cached_doc("Agent Tool", tool_name)
	except frappe.DoesNotExistError:
		return None
	return tool if cint(tool.enabled) else None


def _upsert_tool(name: str, spec: dict) -> None:
	values = {
		"description": spec.get("description") or "",
		"provider_app": spec.get("provider_app") or "",
		"handler_path": spec["handler_path"],
		"capability": spec.get("capability") or "Read",
		"args_schema": frappe.as_json(spec.get("args_schema") or DEFAULT_ARGS_SCHEMA),
		"enabled": 1,
	}

	if frappe.db.exists("Agent Tool", name):
		tool = frappe.get_doc("Agent Tool", name)
		if all(_same(tool.get(field), value) for field, value in values.items()):
			return
		tool.update(values)
		tool.flags.ignore_permissions = True
		tool.save(ignore_permissions=True)
		return

	tool = frappe.get_doc(doctype="Agent Tool", tool_name=name, **values)
	tool.flags.ignore_permissions = True
	tool.insert(ignore_permissions=True)


def _disable_undeclared(declared: set[str]) -> None:
	for name in frappe.get_all("Agent Tool", filters={"enabled": 1}, pluck="name"):
		if name in declared:
			continue
		tool = frappe.get_doc("Agent Tool", name)
		tool.flags.ignore_permissions = True
		tool.db_set("enabled", 0)
		frappe.logger("frappe_agents").info(f"disabled tool {name}: no longer declared by any app")


def _same(current: Any, new: Any) -> bool:
	if isinstance(new, int):
		return cint(current) == new
	return (current or "") == (new or "")
