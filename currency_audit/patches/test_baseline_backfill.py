"""Tests for currency_audit.patches.baseline_backfill (F6)."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit.patches import baseline_backfill
from currency_audit.test_helpers import get_or_create_currency_exchange


class TestBaselineBackfill(FrappeTestCase):
	def test_creates_a_baseline_log_for_each_currency_exchange_once(self):
		currency_exchange = get_or_create_currency_exchange("USD", "NZD", 1.6)

		with patch("currency_audit.utils.frappe.enqueue"):
			baseline_backfill._create_baseline_logs()
			first_count = frappe.db.count(
				"Currency Exchange Log",
				{"currency_exchange": currency_exchange.name, "entry_type": "Baseline"},
			)
			self.assertEqual(first_count, 1)

			# Running again must not create a duplicate baseline for the same record.
			baseline_backfill._create_baseline_logs()
			second_count = frappe.db.count(
				"Currency Exchange Log",
				{"currency_exchange": currency_exchange.name, "entry_type": "Baseline"},
			)
			self.assertEqual(second_count, 1)

	def test_enables_track_changes_on_currency_exchange(self):
		baseline_backfill._enable_track_changes()
		# track_changes is applied via a Property Setter, which overrides the doctype's
		# effective meta rather than the stored DocType row itself.
		meta = frappe.get_meta("Currency Exchange", cached=False)
		self.assertEqual(meta.track_changes, 1)
