"""Model providers.

One function, `call_model`, in one shape, over two wire formats: OpenAI-compatible
chat completions and Anthropic messages. No streaming in P0.

The API key is read here and nowhere else. It is never logged, never returned, and
never written to a document field.
"""

import json
from typing import Any

import frappe
import requests
from frappe.utils import cint

PROVIDER_OPENAI = "OpenAI Compatible"
PROVIDER_ANTHROPIC = "Anthropic"

REQUEST_TIMEOUT = 120
DEFAULT_MAX_TOKENS = 4096
ANTHROPIC_VERSION = "2023-06-01"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
OPENAI_BASE_URL = "https://api.openai.com/v1"
ERROR_BODY_LIMIT = 800


class ProviderError(Exception):
	"""The model call could not be made or came back unusable."""


def call_model(profile: Any, messages: list[dict], tool_schemas: list[dict] | None = None) -> dict:
	"""Call the model behind an LLM Model Profile.

	`messages` use the internal shape: `{"role": "system|user|assistant|tool",
	"content": str, "tool_calls": [{"id", "name", "args"}], "tool_call_id": str}`.

	Returns `{"text", "tool_calls", "tokens_in", "tokens_out"}`.
	"""
	if isinstance(profile, str):
		profile = frappe.get_cached_doc("LLM Model Profile", profile)
	if not cint(profile.enabled):
		raise ProviderError(f"Model profile {profile.name} is disabled.")

	provider = frappe.get_doc("LLM Provider", profile.provider)
	if not cint(provider.enabled):
		raise ProviderError(f"Provider {provider.name} is disabled.")

	api_key = provider.get_password("api_key", raise_exception=False)
	if not api_key:
		raise ProviderError(f"Provider {provider.name} has no API key set.")

	tool_schemas = tool_schemas or []
	if provider.provider_type == PROVIDER_ANTHROPIC:
		return _call_anthropic(provider, profile, messages, tool_schemas, api_key)
	return _call_openai(provider, profile, messages, tool_schemas, api_key)


def _call_openai(
	provider: Any, profile: Any, messages: list[dict], tool_schemas: list[dict], api_key: str
) -> dict:
	url = f"{(provider.base_url or OPENAI_BASE_URL).rstrip('/')}/chat/completions"
	payload: dict[str, Any] = {
		"model": profile.model_id,
		"messages": _openai_messages(messages),
	}
	if tool_schemas:
		payload["tools"] = [
			{
				"type": "function",
				"function": {
					"name": schema["name"],
					"description": schema.get("description") or "",
					"parameters": schema.get("args_schema") or {"type": "object", "properties": {}},
				},
			}
			for schema in tool_schemas
		]

	headers = {
		"Authorization": f"Bearer {api_key}",
		"Content-Type": "application/json",
	}
	data = _post(url, headers, payload, api_key)

	choice = (data.get("choices") or [{}])[0]
	message = choice.get("message") or {}

	tool_calls = []
	for index, call in enumerate(message.get("tool_calls") or []):
		function = call.get("function") or {}
		tool_calls.append(
			{
				"id": call.get("id") or f"call_{index}",
				"name": function.get("name") or "",
				"args": _load_args(function.get("arguments")),
			}
		)

	usage = data.get("usage") or {}
	return {
		"text": message.get("content"),
		"tool_calls": tool_calls,
		"tokens_in": cint(usage.get("prompt_tokens")),
		"tokens_out": cint(usage.get("completion_tokens")),
	}


def _call_anthropic(
	provider: Any, profile: Any, messages: list[dict], tool_schemas: list[dict], api_key: str
) -> dict:
	url = f"{(provider.base_url or ANTHROPIC_BASE_URL).rstrip('/')}/v1/messages"
	system, converted = _anthropic_messages(messages)
	payload: dict[str, Any] = {
		"model": profile.model_id,
		"max_tokens": DEFAULT_MAX_TOKENS,
		"messages": converted,
	}
	if system:
		payload["system"] = system
	if tool_schemas:
		payload["tools"] = [
			{
				"name": schema["name"],
				"description": schema.get("description") or "",
				"input_schema": schema.get("args_schema") or {"type": "object", "properties": {}},
			}
			for schema in tool_schemas
		]

	headers = {
		"x-api-key": api_key,
		"anthropic-version": ANTHROPIC_VERSION,
		"Content-Type": "application/json",
	}
	data = _post(url, headers, payload, api_key)

	texts = []
	tool_calls = []
	for index, block in enumerate(data.get("content") or []):
		if block.get("type") == "text":
			texts.append(block.get("text") or "")
		elif block.get("type") == "tool_use":
			tool_calls.append(
				{
					"id": block.get("id") or f"call_{index}",
					"name": block.get("name") or "",
					"args": block.get("input") if isinstance(block.get("input"), dict) else {},
				}
			)

	usage = data.get("usage") or {}
	return {
		"text": "\n".join(texts) if texts else None,
		"tool_calls": tool_calls,
		"tokens_in": cint(usage.get("input_tokens")),
		"tokens_out": cint(usage.get("output_tokens")),
	}


def _openai_messages(messages: list[dict]) -> list[dict]:
	converted = []
	for message in messages:
		role = message.get("role")
		if role == "tool":
			converted.append(
				{
					"role": "tool",
					"tool_call_id": message.get("tool_call_id") or "",
					"content": message.get("content") or "",
				}
			)
			continue

		if role == "assistant" and message.get("tool_calls"):
			converted.append(
				{
					"role": "assistant",
					"content": message.get("content") or "",
					"tool_calls": [
						{
							"id": call.get("id"),
							"type": "function",
							"function": {
								"name": call.get("name"),
								"arguments": json.dumps(call.get("args") or {}),
							},
						}
						for call in message["tool_calls"]
					],
				}
			)
			continue

		converted.append({"role": role or "user", "content": message.get("content") or ""})
	return converted


def _anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
	system_parts = []
	converted: list[dict] = []

	for message in messages:
		role = message.get("role")

		if role == "system":
			if message.get("content"):
				system_parts.append(message["content"])
			continue

		if role == "tool":
			block = {
				"type": "tool_result",
				"tool_use_id": message.get("tool_call_id") or "",
				"content": message.get("content") or "",
			}
			if converted and converted[-1]["role"] == "user" and _is_tool_result(converted[-1]):
				converted[-1]["content"].append(block)
			else:
				converted.append({"role": "user", "content": [block]})
			continue

		if role == "assistant":
			blocks: list[dict] = []
			if message.get("content"):
				blocks.append({"type": "text", "text": message["content"]})
			for call in message.get("tool_calls") or []:
				blocks.append(
					{
						"type": "tool_use",
						"id": call.get("id"),
						"name": call.get("name"),
						"input": call.get("args") or {},
					}
				)
			if blocks:
				converted.append({"role": "assistant", "content": blocks})
			continue

		converted.append(
			{"role": "user", "content": [{"type": "text", "text": message.get("content") or ""}]}
		)

	return "\n\n".join(system_parts), converted


def _is_tool_result(message: dict) -> bool:
	content = message.get("content")
	return bool(content) and isinstance(content, list) and content[0].get("type") == "tool_result"


def _post(url: str, headers: dict, payload: dict, api_key: str) -> dict:
	try:
		response = requests.post(url, headers=headers, json=payload, timeout=REQUEST_TIMEOUT)
	except requests.RequestException as exc:
		raise ProviderError(f"Model request to {url} failed: {_redact(str(exc), api_key)}")

	if response.status_code != 200:
		body = _redact(response.text or "", api_key)[:ERROR_BODY_LIMIT]
		raise ProviderError(f"Model request to {url} returned HTTP {response.status_code}: {body}")

	try:
		return response.json()
	except ValueError:
		raise ProviderError(f"Model request to {url} returned a non-JSON body.")


def _load_args(raw: Any) -> dict:
	if isinstance(raw, dict):
		return raw
	if not raw:
		return {}
	try:
		args = json.loads(raw)
	except Exception:
		return {}
	return args if isinstance(args, dict) else {}


def _redact(text: str, api_key: str) -> str:
	if api_key and api_key in text:
		text = text.replace(api_key, "***")
	return text
