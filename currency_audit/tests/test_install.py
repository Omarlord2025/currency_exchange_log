"""Tests for currency_audit.install (composite DB indexes the F4 matching job relies on,
and the F6 baseline backfill that `bench install-app` would otherwise silently skip)."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit.install import INDEXED_FIELDS, add_matching_indexes, after_install


class TestAddMatchingIndexes(FrappeTestCase):
	def test_creates_expected_indexes_idempotently(self):
		# Runs twice to prove it's safe to call repeatedly (e.g. re-running after_install).
		add_matching_indexes()
		add_matching_indexes()

		for doctype, fields in INDEXED_FIELDS:
			table = f"tab{doctype}"
			index_name = frappe.db.get_index_name(fields)
			self.assertTrue(
				frappe.db.has_index(table, index_name),
				msg=f"expected index {index_name} on {table}",
			)


class TestAfterInstall(FrappeTestCase):
	def test_runs_baseline_backfill_directly_not_just_via_patches_txt(self):
		# `bench install-app` marks patches.txt entries as already-applied without
		# running them (frappe.installer.set_all_patches_as_completed), so a fresh
		# install would silently skip F6 unless after_install calls it directly.
		with (
			patch("currency_audit.install.add_matching_indexes") as mock_add_indexes,
			patch("currency_audit.patches.baseline_backfill.execute") as mock_baseline,
		):
			after_install()

		mock_add_indexes.assert_called_once()
		mock_baseline.assert_called_once()
