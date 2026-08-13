"""Shared low-level types for Tau's portable agent layer."""

# Vendored from huggingface/tau v0.3.9 (commit 420ae60089c1adb30d415e4dde99f7323d3f4afb).
# Copyright (c) 2026 Alejandro AO. MIT licensed — see LICENSE.upstream.
# Local edits are marked "frappe_agents patch:".

from __future__ import annotations

# Pydantic needs PEP 695 named recursive aliases for JSON-like values.
type JSONPrimitive = str | int | float | bool | None
type JSONValue = JSONPrimitive | list[JSONValue] | dict[str, JSONValue]
type JSONObject = dict[str, JSONValue]
