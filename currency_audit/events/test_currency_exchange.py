"""Tests for currency_audit.events.currency_exchange (F1 change detection)."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from currency_audit.test_helpers import get_or_create_currency_exchange


class TestOnUpdate(FrappeTestCase):
	@patch("currency_audit.events.currency_exchange.utils.create_log_entry")
	def test_new_record_does_not_log(self, mock_create_log_entry):
		# on_update also fires after insert; a brand-new record has no "before" state
		# and must not be treated as a change (F1: only edits to existing rates log).
		doc = frappe.get_doc(
			{
				"doctype": "Currency Exchange",
				"date": nowdate(),
				"from_currency": "USD",
				"to_currency": "AUD",
				"exchange_rate": 1.5,
				"for_buying": 1,
				"for_selling": 1,
			}
		)
		doc.insert(ignore_permissions=True)
		mock_create_log_entry.assert_not_called()

	@patch("currency_audit.events.currency_exchange.utils.create_log_entry")
	def test_resave_with_same_rate_is_a_no_op(self, mock_create_log_entry):
		doc = get_or_create_currency_exchange("USD", "CAD", 1.3)
		doc.save(ignore_permissions=True)
		mock_create_log_entry.assert_not_called()

	@patch("currency_audit.events.currency_exchange.utils.create_log_entry")
	def test_rate_change_creates_exactly_one_log(self, mock_create_log_entry):
		doc = get_or_create_currency_exchange("USD", "CHF", 0.85)
		doc.exchange_rate = 0.9
		doc.save(ignore_permissions=True)

		mock_create_log_entry.assert_called_once()
		_, kwargs = mock_create_log_entry.call_args
		self.assertEqual(kwargs.get("old_rate"), 0.85)
		self.assertEqual(kwargs.get("new_rate"), 0.9)
		self.assertEqual(kwargs.get("entry_type"), "Change")
