"""Tests for currency_audit.reconciliation (Version-history safety net).

track_changes is on for Currency Exchange (enabled by F6's baseline patch), so a
real .save() through Frappe here also produces a genuine native Version record —
these tests rely on that rather than fabricating Version JSON by hand, since the
diff format (Document.get_formatted-based) isn't something worth re-implementing
just to test against. Frappe skips Version creation during tests by default
(Document._save: ignore_version defaults to frappe.flags.in_test), so every save()
below passes ignore_version=False to force a real one.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit import reconciliation
from currency_audit.test_helpers import get_or_create_currency_exchange


class TestReconcileVersionHistory(FrappeTestCase):
	def test_recovers_an_edit_the_primary_hook_missed(self):
		currency_exchange = get_or_create_currency_exchange("USD", "MXN", 17.5)

		# Simulates the primary hook (F1) failing to log this edit — e.g. a bug, or
		# the app being disabled while the Property Setter kept versioning active
		# regardless — while Frappe's own Version tracking still captures it normally,
		# since that's independent of currency_audit's own hook.
		with patch("currency_audit.events.currency_exchange.utils.create_log_entry"):
			currency_exchange.reload()
			currency_exchange.exchange_rate = 18.0
			currency_exchange.save(ignore_permissions=True, ignore_version=False)

		self.assertFalse(
			frappe.db.exists(
				"Currency Exchange Log",
				{"currency_exchange": currency_exchange.name, "new_rate": 18.0},
			)
		)

		with patch("currency_audit.utils.frappe.enqueue"):
			reconciliation.reconcile_version_history(since=None)

		recovered = frappe.get_all(
			"Currency Exchange Log",
			filters={"currency_exchange": currency_exchange.name, "entry_type": "Recovered"},
			fields=["name", "old_rate", "new_rate", "changed_by", "source_version"],
		)
		self.assertEqual(len(recovered), 1)
		self.assertEqual(recovered[0].old_rate, 17.5)
		self.assertEqual(recovered[0].new_rate, 18.0)
		self.assertTrue(recovered[0].source_version)

	def test_is_idempotent_across_repeated_runs(self):
		currency_exchange = get_or_create_currency_exchange("USD", "PEN", 3.7)
		with patch("currency_audit.events.currency_exchange.utils.create_log_entry"):
			currency_exchange.reload()
			currency_exchange.exchange_rate = 3.8
			currency_exchange.save(ignore_permissions=True, ignore_version=False)

		with patch("currency_audit.utils.frappe.enqueue"):
			reconciliation.reconcile_version_history(since=None)
			reconciliation.reconcile_version_history(since=None)

		recovered = frappe.get_all(
			"Currency Exchange Log",
			filters={"currency_exchange": currency_exchange.name, "entry_type": "Recovered"},
		)
		self.assertEqual(len(recovered), 1)

	def test_does_not_recover_an_edit_the_hook_already_logged(self):
		currency_exchange = get_or_create_currency_exchange("USD", "COP", 4000.0)
		with patch("currency_audit.utils.frappe.enqueue"):
			# Hook runs for real here (not mocked out) — this is the normal path.
			currency_exchange.reload()
			currency_exchange.exchange_rate = 4100.0
			currency_exchange.save(ignore_permissions=True, ignore_version=False)

		self.assertTrue(
			frappe.db.exists(
				"Currency Exchange Log",
				{
					"currency_exchange": currency_exchange.name,
					"new_rate": 4100.0,
					"entry_type": "Change",
				},
			)
		)

		with patch("currency_audit.utils.frappe.enqueue"):
			reconciliation.reconcile_version_history(since=None)

		recovered = frappe.get_all(
			"Currency Exchange Log",
			filters={"currency_exchange": currency_exchange.name, "entry_type": "Recovered"},
		)
		self.assertEqual(len(recovered), 0)


class TestExtractRateChange(FrappeTestCase):
	def test_parses_a_well_formed_diff(self):
		data = '{"changed": [["exchange_rate", "50.0", "52.5"], ["modified", "a", "b"]]}'
		self.assertEqual(reconciliation._extract_rate_change(data), (50.0, 52.5))

	def test_strips_thousands_separators(self):
		data = '{"changed": [["exchange_rate", "1,234.5", "1,300.0"]]}'
		self.assertEqual(reconciliation._extract_rate_change(data), (1234.5, 1300.0))

	def test_returns_none_when_exchange_rate_did_not_change(self):
		data = '{"changed": [["for_selling", "0", "1"]]}'
		self.assertIsNone(reconciliation._extract_rate_change(data))

	def test_returns_none_for_unparseable_json(self):
		self.assertIsNone(reconciliation._extract_rate_change("not json"))
