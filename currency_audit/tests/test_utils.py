"""Tests for currency_audit.utils, next to the module per CLAUDE.md."""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit import utils
from currency_audit.test_helpers import get_or_create_currency_exchange


class TestCreateLogEntry(FrappeTestCase):
	@classmethod
	def setUpClass(cls):
		super().setUpClass()
		cls.currency_exchange = get_or_create_currency_exchange("USD", "JPY", 150)

	@patch("currency_audit.utils.frappe.enqueue")
	def test_creates_change_entry_and_enqueues_matching(self, mock_enqueue):
		name = utils.create_log_entry(
			self.currency_exchange, new_rate=151, old_rate=150, entry_type="Change"
		)
		log = frappe.get_doc("Currency Exchange Log", name)

		self.assertEqual(log.entry_type, "Change")
		self.assertEqual(log.from_currency, "USD")
		self.assertEqual(log.to_currency, "JPY")
		self.assertEqual(log.old_rate, 150)
		self.assertEqual(log.new_rate, 151)
		self.assertEqual(log.changed_by, frappe.session.user)

		mock_enqueue.assert_called_once()
		_, kwargs = mock_enqueue.call_args
		self.assertEqual(kwargs.get("log_name"), name)
		self.assertEqual(kwargs.get("queue"), "short")

	@patch("currency_audit.utils.frappe.enqueue")
	def test_accepts_a_dict_like_source_for_baseline_backfill(self, mock_enqueue):
		# The baseline patch (F6) passes a frappe.get_all() row (a frappe._dict), not a
		# full Document, since loading full Documents in a loop would violate the
		# "no get_doc() inside a loop" performance rule.
		row = frappe._dict(
			name=self.currency_exchange.name, from_currency="USD", to_currency="JPY"
		)
		name = utils.create_log_entry(row, new_rate=150, old_rate=None, entry_type="Baseline")
		log = frappe.get_doc("Currency Exchange Log", name)

		self.assertEqual(log.entry_type, "Baseline")
		self.assertEqual(log.currency_exchange, self.currency_exchange.name)
		mock_enqueue.assert_called_once()
