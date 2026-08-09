"""F4: background matching job.

Enqueued once per Currency Exchange Log entry (by currency_audit.utils.create_log_entry)
so the save that triggered logging isn't blocked. Matches the log's old and new rate
against every transaction doctype that snapshots a conversion rate:

- Sales Invoice, Purchase Invoice, POS Invoice, Purchase Receipt, Delivery Note share
  the exact same `currency` + `conversion_rate` shape, so one query pattern covers all five.
- Payment Entry stores the rate per side of the transaction (source/target).
- Journal Entry stores it per row of its `accounts` child table.
"""

import frappe
from frappe.utils import flt

STANDARD_DOCTYPES = (
	"Sales Invoice",
	"Purchase Invoice",
	"POS Invoice",
	"Purchase Receipt",
	"Delivery Note",
)


def run_matching_job(log_name):
	"""Match a Currency Exchange Log's old/new rate against transactions and save the results.

	Args:
		log_name (str): name of the Currency Exchange Log document to match against.
	"""
	log = frappe.get_doc("Currency Exchange Log", log_name)

	try:
		rows = []
		for rate_type, rate in (("Old Rate", log.old_rate), ("New Rate", log.new_rate)):
			if not rate:
				continue
			rate = flt(rate, 9)
			for doctype in STANDARD_DOCTYPES:
				rows += _match_standard_doctype(doctype, log.from_currency, rate, rate_type)
			rows += _match_payment_entry(log.from_currency, rate, rate_type)
			rows += _match_journal_entry(log.from_currency, rate, rate_type)

		log.set("usage", rows)
		log.matching_status = "Completed"
		log.save(ignore_permissions=True)
	except Exception:
		frappe.log_error(title=f"Currency Exchange Log matching failed: {log_name}")
		frappe.db.set_value("Currency Exchange Log", log_name, "matching_status", "Failed")


def _match_standard_doctype(doctype, currency, rate, rate_type):
	"""Match submitted documents of one of the five standard doctypes against a rate.

	Args:
		doctype (str): one of Sales Invoice / Purchase Invoice / POS Invoice / Purchase Receipt / Delivery Note.
		currency (str): transaction currency to filter on.
		rate (float): conversion_rate value to match exactly.
		rate_type (str): "Old Rate" or "New Rate", recorded on each usage row.

	Returns:
		list[dict]: usage child-table rows for matches found.
	"""
	matches = frappe.get_all(
		doctype,
		filters={"currency": currency, "conversion_rate": rate, "docstatus": 1},
		fields=["name", "posting_date", "grand_total", "currency"],
	)
	return [
		{
			"reference_doctype": doctype,
			"reference_name": m.name,
			"posting_date": m.posting_date,
			"currency": m.currency,
			"amount": m.grand_total,
			"rate_type": rate_type,
		}
		for m in matches
	]


def _match_payment_entry(currency, rate, rate_type):
	"""Match submitted Payment Entries against a rate on either side of the transaction.

	Args:
		currency (str): account currency to filter on, checked against both the source and target side.
		rate (float): source_exchange_rate / target_exchange_rate value to match exactly.
		rate_type (str): "Old Rate" or "New Rate", recorded on each usage row.

	Returns:
		list[dict]: usage child-table rows for matches found.
	"""
	rows = []

	source_matches = frappe.get_all(
		"Payment Entry",
		filters={
			"paid_from_account_currency": currency,
			"source_exchange_rate": rate,
			"docstatus": 1,
		},
		fields=["name", "posting_date", "paid_amount", "paid_from_account_currency"],
	)
	rows += [
		{
			"reference_doctype": "Payment Entry",
			"reference_name": m.name,
			"posting_date": m.posting_date,
			"currency": m.paid_from_account_currency,
			"amount": m.paid_amount,
			"rate_type": rate_type,
		}
		for m in source_matches
	]

	target_matches = frappe.get_all(
		"Payment Entry",
		filters={
			"paid_to_account_currency": currency,
			"target_exchange_rate": rate,
			"docstatus": 1,
		},
		fields=["name", "posting_date", "received_amount", "paid_to_account_currency"],
	)
	rows += [
		{
			"reference_doctype": "Payment Entry",
			"reference_name": m.name,
			"posting_date": m.posting_date,
			"currency": m.paid_to_account_currency,
			"amount": m.received_amount,
			"rate_type": rate_type,
		}
		for m in target_matches
	]

	return rows


def _match_journal_entry(currency, rate, rate_type):
	"""Match submitted Journal Entry rows (per-line rate/currency) against a rate.

	Args:
		currency (str): account currency to filter on within the `accounts` child table.
		rate (float): per-row exchange_rate value to match exactly.
		rate_type (str): "Old Rate" or "New Rate", recorded on each usage row.

	Returns:
		list[dict]: usage child-table rows for matches found.
	"""
	matches = frappe.get_all(
		"Journal Entry Account",
		filters={
			"account_currency": currency,
			"exchange_rate": rate,
			"docstatus": 1,
			"parenttype": "Journal Entry",
		},
		fields=["parent", "debit_in_account_currency", "credit_in_account_currency", "account_currency"],
	)
	rows = []
	if not matches:
		return rows

	posting_dates = dict(
		frappe.get_all(
			"Journal Entry",
			filters={"name": ["in", list({m.parent for m in matches})]},
			fields=["name", "posting_date"],
			as_list=True,
		)
	)

	for m in matches:
		rows.append(
			{
				"reference_doctype": "Journal Entry",
				"reference_name": m.parent,
				"posting_date": posting_dates.get(m.parent),
				"currency": m.account_currency,
				"amount": flt(m.debit_in_account_currency) or flt(m.credit_in_account_currency),
				"rate_type": rate_type,
			}
		)

	return rows
