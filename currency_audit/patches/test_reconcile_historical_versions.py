"""Tests for currency_audit.patches.reconcile_historical_versions."""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from currency_audit.patches import reconcile_historical_versions


class TestReconcileHistoricalVersions(FrappeTestCase):
	def test_delegates_to_a_full_history_scan(self):
		with patch("currency_audit.patches.reconcile_historical_versions.reconcile_version_history") as mock_fn:
			reconcile_historical_versions.execute()

		mock_fn.assert_called_once_with(since=None)
