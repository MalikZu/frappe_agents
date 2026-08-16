"""Shared low-level types for Tau's portable agent layer."""

# Vendored from huggingface/tau v0.3.9 (commit 420ae60089c1adb30d415e4dde99f7323d3f4afb).
# Copyright (c) 2026 Alejandro AO. MIT licensed — see LICENSE.upstream.
# Local edits are marked "frappe_agents patch:".

from __future__ import annotations

from typing import Union

from typing_extensions import TypeAliasType

# frappe_agents patch: upstream writes these as PEP 695 `type` aliases, which are
# Python 3.12+. This branch targets 3.11, and a plain `TypeAlias` is NOT a
# substitute here — pydantic expands it eagerly and recurses until it dies.
# TypeAliasType is the backport of the same lazy, *named* alias pydantic needs to
# tie the recursive knot. typing_extensions is a direct frappe dependency, so
# this adds nothing to the bench.
JSONPrimitive = TypeAliasType("JSONPrimitive", Union[str, int, float, bool, None])
JSONValue = TypeAliasType(
    "JSONValue", Union[JSONPrimitive, list["JSONValue"], dict[str, "JSONValue"]]
)
JSONObject = TypeAliasType("JSONObject", dict[str, JSONValue])
