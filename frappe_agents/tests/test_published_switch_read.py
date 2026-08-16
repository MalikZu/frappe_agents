# Copyright (c) 2026, Malik AlZubaidi and contributors
# For license information, please see LICENSE

"""Reading the published kill switch past a process-local memo.

Publishing exists so a job already running sees the switch move. A cached answer
is the one thing that read must never give — a worker holding a memo would keep
saying "still on" for the rest of the run, and the kill switch would stop
nothing.

The two frameworks say "do not use the local cache" differently. v16 takes
`use_local_cache=False`. v15 has no such argument and memoizes what it reads, so
the memo is dropped first and `expires=True` stops it being written back. These
pin both shapes, because getting it wrong is silent: the old code caught the
resulting TypeError and reported "nothing published", which reads exactly like a
healthy site that has never saved its settings.
"""

from typing import Any
from unittest.mock import patch

from frappe_agents.tests.compat import IntegrationTestCase
from frappe_agents.tools.base import KILL_SWITCH_KEY, _read_published


class NewCache:
	"""v16: takes use_local_cache."""

	def __init__(self, value: Any) -> None:
		self.value = value
		self.asked_with: dict = {}

	def get_value(self, key, generator=None, user=None, expires=False, shared=False, *, use_local_cache=True):
		self.asked_with = {"key": key, "use_local_cache": use_local_cache}
		return self.value

	def make_key(self, key, user=None, shared=False):
		return f"key::{key}"


class OldCache:
	"""v15: no such argument, and it memoizes what it reads."""

	def __init__(self, value: Any) -> None:
		self.value = value
		self.asked_with: dict = {}

	def get_value(self, key, generator=None, user=None, expires=False, shared=False):
		self.asked_with = {"key": key, "expires": expires}
		return self.value

	def make_key(self, key, user=None, shared=False):
		return f"key::{key}"


class TestReadingThePublishedSwitch(IntegrationTestCase):
	def test_the_new_cache_is_told_not_to_use_the_local_copy(self):
		cache = NewCache(0)
		with patch("frappe.cache", cache):
			self.assertEqual(_read_published(), 0)
		self.assertIs(cache.asked_with["use_local_cache"], False)

	def test_the_old_cache_is_asked_in_the_only_way_it_will_not_memoize(self):
		cache = OldCache(0)
		with patch("frappe.cache", cache):
			self.assertEqual(_read_published(), 0)
		self.assertIs(cache.asked_with["expires"], True, "expires=True is what stops v15 caching the read")

	def test_a_stale_memo_is_dropped_before_the_old_cache_is_read(self):
		"""The memo is the failure. A worker holding one never sees the switch move."""
		cache = OldCache(0)
		local: dict = {cache.make_key(KILL_SWITCH_KEY): 1}
		with patch("frappe.cache", cache), patch("frappe.local.cache", local):
			self.assertEqual(_read_published(), 0, "the read answered from the stale memo")
		self.assertNotIn(cache.make_key(KILL_SWITCH_KEY), local, "the memo was left in place")

	def test_both_shapes_are_asked_for_the_switch_key(self):
		for cache in (NewCache(1), OldCache(1)):
			with patch("frappe.cache", cache):
				_read_published()
			self.assertEqual(cache.asked_with["key"], KILL_SWITCH_KEY)
