"""Tests for currency_audit.patches.recompute_matching_with_confidence."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit.patches import recompute_matching_with_confidence
from currency_audit.test_helpers import get_or_create_currency_exchange, make_log


class TestRecomputeMatchingWithConfidence(FrappeTestCase):
	def test_recomputes_only_completed_entries(self):
		currency_exchange = get_or_create_currency_exchange("USD", "VND", 24000.0)
		with patch("currency_audit.utils.frappe.enqueue"):
			completed_log = make_log(currency_exchange, new_rate=24000.0, old_rate=23800.0)
			pending_log = make_log(currency_exchange, new_rate=24100.0, old_rate=24000.0)

		frappe.db.set_value("Currency Exchange Log", completed_log.name, "matching_status", "Completed")
		frappe.db.set_value("Currency Exchange Log", pending_log.name, "matching_status", "Pending")

		with patch("currency_audit.patches.recompute_matching_with_confidence.run_matching_job") as mock_run:
			recompute_matching_with_confidence.execute()

		ran_for = [call.args[0] for call in mock_run.call_args_list]
		self.assertIn(completed_log.name, ran_for)
		self.assertNotIn(pending_log.name, ran_for)
