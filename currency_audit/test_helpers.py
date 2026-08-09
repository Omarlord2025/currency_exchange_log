"""Shared fixture builders for currency_audit test modules.

Not itself a test module (no `test_` prefix) — imported by the various test_*.py
files so fixture setup isn't duplicated across them (CLAUDE.md: never duplicate a
function across files).
"""

import frappe
from frappe.utils import now_datetime, nowdate


def get_or_create_currency_exchange(from_currency, to_currency, exchange_rate):
	"""Return an existing Currency Exchange for the pair, updated to `exchange_rate`, or create one.

	Args:
		from_currency (str): source currency code.
		to_currency (str): target currency code.
		exchange_rate (float): rate to set on the record.

	Returns:
		Document: the Currency Exchange document.
	"""
	name = frappe.db.exists("Currency Exchange", {"from_currency": from_currency, "to_currency": to_currency})
	if name:
		doc = frappe.get_doc("Currency Exchange", name)
		if doc.exchange_rate != exchange_rate:
			doc.exchange_rate = exchange_rate
			doc.save(ignore_permissions=True)
		return doc

	doc = frappe.get_doc(
		{
			"doctype": "Currency Exchange",
			"date": nowdate(),
			"from_currency": from_currency,
			"to_currency": to_currency,
			"exchange_rate": exchange_rate,
			"for_buying": 1,
			"for_selling": 1,
		}
	)
	doc.insert(ignore_permissions=True)
	return doc


def make_log(currency_exchange, new_rate, old_rate=None, entry_type="Change"):
	"""Insert a Currency Exchange Log directly, bypassing utils.create_log_entry's enqueue call.

	Args:
		currency_exchange (Document): source Currency Exchange the log is about.
		new_rate (float): new rate value to record.
		old_rate (float | None): prior rate value, if any.
		entry_type (str): "Change" or "Baseline".

	Returns:
		Document: the inserted Currency Exchange Log document.
	"""
	log = frappe.get_doc(
		{
			"doctype": "Currency Exchange Log",
			"entry_type": entry_type,
			"currency_exchange": currency_exchange.name,
			"from_currency": currency_exchange.from_currency,
			"to_currency": currency_exchange.to_currency,
			"old_rate": old_rate,
			"new_rate": new_rate,
			"changed_by": frappe.session.user,
			"changed_on": now_datetime(),
		}
	)
	log.insert(ignore_permissions=True)
	return log
