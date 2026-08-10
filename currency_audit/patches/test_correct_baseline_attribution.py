"""Tests for currency_audit.patches.correct_baseline_attribution."""

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit.patches import correct_baseline_attribution
from currency_audit.test_helpers import get_or_create_currency_exchange, make_log


class TestCorrectBaselineAttribution(FrappeTestCase):
	def test_corrects_a_mis_attributed_baseline_entry(self):
		currency_exchange = get_or_create_currency_exchange("USD", "PLN", 4.2)
		frappe.db.set_value(
			"Currency Exchange",
			currency_exchange.name,
			{"modified_by": "test@example.com", "modified": "2021-06-15 09:00:00"},
			update_modified=False,
		)
		# Simulates the old bug's output: attributed to Administrator/now instead of
		# the source record's real last editor.
		log = make_log(currency_exchange, new_rate=4.2, old_rate=None, entry_type="Baseline")
		self.assertEqual(log.changed_by, frappe.session.user)

		correct_baseline_attribution.execute()

		log.reload()
		self.assertEqual(log.changed_by, "test@example.com")
		self.assertEqual(str(log.changed_on), "2021-06-15 09:00:00")

	def test_leaves_an_already_correct_baseline_entry_untouched(self):
		currency_exchange = get_or_create_currency_exchange("USD", "CZK", 22.5)
		log = make_log(
			currency_exchange,
			new_rate=22.5,
			old_rate=None,
			entry_type="Baseline",
		)
		# Already matches the source's own modified_by/modified (the normal case for
		# any entry created by the fixed backfill logic).
		frappe.db.set_value(
			"Currency Exchange",
			currency_exchange.name,
			{"modified_by": log.changed_by, "modified": log.changed_on},
			update_modified=False,
		)
		before_modified = frappe.db.get_value("Currency Exchange Log", log.name, "modified")

		correct_baseline_attribution.execute()

		after_modified = frappe.db.get_value("Currency Exchange Log", log.name, "modified")
		self.assertEqual(before_modified, after_modified)

	def test_change_entries_are_never_touched(self):
		currency_exchange = get_or_create_currency_exchange("USD", "HUF", 350.0)
		log = make_log(currency_exchange, new_rate=350.0, old_rate=345.0, entry_type="Change")

		correct_baseline_attribution.execute()

		log.reload()
		self.assertEqual(log.changed_by, frappe.session.user)

	def test_the_correction_flag_does_not_open_up_other_fields(self):
		# Defense in depth: even with allow_baseline_attribution_correction set, only
		# changed_by/changed_on may move — everything else stays fully immutable.
		currency_exchange = get_or_create_currency_exchange("USD", "THB", 36.0)
		log = make_log(currency_exchange, new_rate=36.0, old_rate=None, entry_type="Baseline")

		log.flags.allow_baseline_attribution_correction = True
		log.new_rate = 999
		self.assertRaises(frappe.ValidationError, log.save, ignore_permissions=True)
