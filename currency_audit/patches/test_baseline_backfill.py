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

	def test_baseline_changed_by_and_changed_on_come_from_the_source_record(self):
		# Backfill runs as a script (Administrator/CLI session), which is not who
		# actually set the rate — changed_by/changed_on must reflect the source
		# Currency Exchange record's own modified_by/modified, not the session
		# running the patch.
		currency_exchange = get_or_create_currency_exchange("USD", "SEK", 10.5)
		# update_modified=False: db.set_value auto-stamps modified/modified_by to the
		# current session by default, which would defeat the point of this test.
		# changed_by is a Link to User, so this has to be a real user — test@example.com
		# is a standard Frappe test-site fixture, distinct from the Administrator/CLI
		# session this patch actually runs under.
		frappe.db.set_value(
			"Currency Exchange",
			currency_exchange.name,
			{"modified_by": "test@example.com", "modified": "2020-01-01 00:00:00"},
			update_modified=False,
		)

		with patch("currency_audit.utils.frappe.enqueue"):
			baseline_backfill._create_baseline_logs()

		log_name = frappe.db.get_value(
			"Currency Exchange Log",
			{"currency_exchange": currency_exchange.name, "entry_type": "Baseline"},
		)
		log = frappe.get_doc("Currency Exchange Log", log_name)
		self.assertEqual(log.changed_by, "test@example.com")
		self.assertEqual(str(log.changed_on), "2020-01-01 00:00:00")

	def test_enables_track_changes_on_currency_exchange(self):
		baseline_backfill._enable_track_changes()
		# track_changes is applied via a Property Setter, which overrides the doctype's
		# effective meta rather than the stored DocType row itself.
		meta = frappe.get_meta("Currency Exchange", cached=False)
		self.assertEqual(meta.track_changes, 1)
