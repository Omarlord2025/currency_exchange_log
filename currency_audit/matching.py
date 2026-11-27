"""F4: background matching job.

Enqueued once per Currency Exchange Log entry (by currency_audit.utils.create_log_entry)
so the save that triggered logging isn't blocked. Matches the log's old and new rate
against every transaction doctype that snapshots a conversion rate:

- Sales Invoice, Purchase Invoice, POS Invoice, Purchase Receipt, Delivery Note share
  the exact same `currency` + `conversion_rate` shape, so one query pattern covers all five.
- Payment Entry stores the rate per side of the transaction (source/target).
- Journal Entry stores it per row of its `accounts` child table.
"""

import time
from datetime import datetime, time as time_of_day

import frappe
from frappe.utils import add_to_date, flt, get_datetime, get_time, getdate, now_datetime

STANDARD_DOCTYPES = (
	"Sales Invoice",
	"Purchase Invoice",
	"POS Invoice",
	"Purchase Receipt",
	"Delivery Note",
)

# How long a log can sit on "Pending" before the scheduled sweep re-queues it.
# Keeps a fast worker's in-flight job from being raced by the sweep itself.
STUCK_THRESHOLD_MINUTES = 10

# A log entry's matching job can legitimately take a while against a large real
# transaction table — long enough to outlast STUCK_THRESHOLD_MINUTES. This retry
# count covers the resulting Document.check_if_latest() conflict if the sweep
# re-queues the same log while the original job is still mid-save.
MAX_SAVE_ATTEMPTS = 3


def matching_job_id(log_name):
	"""Deterministic RQ job id for a log's matching job, used for deduplication.

	Args:
		log_name (str): name of the Currency Exchange Log document.

	Returns:
		str: job id to pass to frappe.enqueue(job_id=..., deduplicate=True).
	"""
	return f"currency-audit-match-{log_name}"


def run_matching_job(log_name):
	"""Match a Currency Exchange Log's old/new rate against transactions and save the results.

	Retries on a save conflict (another worker saving the same log concurrently — see
	matching_job_id's dedup, which prevents this at the source but isn't airtight, e.g.
	if the sweep's dedup check itself races an in-flight enqueue) before giving up.

	Args:
		log_name (str): name of the Currency Exchange Log document to match against.
	"""
	for attempt in range(1, MAX_SAVE_ATTEMPTS + 1):
		try:
			log = frappe.get_doc("Currency Exchange Log", log_name)
			companies = _companies_with_currency(log.to_currency)
			windows = _rate_windows(log)
			rows = []
			for rate_type, rate in (("Old Rate", log.old_rate), ("New Rate", log.new_rate)):
				if not rate:
					continue
				rate = flt(rate, 9)
				window = windows[rate_type]
				for doctype in STANDARD_DOCTYPES:
					rows += _match_standard_doctype(doctype, log.from_currency, rate, rate_type, companies, window)
				rows += _match_payment_entry(log.from_currency, rate, rate_type, companies, window)
				rows += _match_journal_entry(log.from_currency, rate, rate_type, companies, window)

			log.set("usage", rows)
			log.matching_status = "Completed"
			# This job is the only writer the log's immutability check will accept for
			# `usage` and `matching_status` (see CurrencyExchangeLog._reject_matching_result_edits).
			log.flags.allow_matching_update = True
			log.save(ignore_permissions=True)
			return
		except frappe.TimestampMismatchError:
			if attempt == MAX_SAVE_ATTEMPTS:
				frappe.log_error(title=f"Currency Exchange Log matching failed: {log_name}")
				frappe.db.set_value("Currency Exchange Log", log_name, "matching_status", "Failed")
			else:
				# Brief backoff so the concurrent save that caused the conflict has a
				# chance to finish before this job re-reads and retries.
				time.sleep(2 * attempt)
		except Exception:
			frappe.log_error(title=f"Currency Exchange Log matching failed: {log_name}")
			frappe.db.set_value("Currency Exchange Log", log_name, "matching_status", "Failed")
			return


def retry_stuck_matches():
	"""Scheduled safety net: re-queue any log still "Pending" after STUCK_THRESHOLD_MINUTES.

	The normal path is frappe.enqueue() right after the log is created (F1/F6). If no
	worker was consuming the queue at that moment, or a worker restarted mid-job, the
	job is lost with no trace and the entry would otherwise sit on "Pending" forever.
	This sweep catches that and tries again — deduplicated by job id so it never queues
	a second attempt on top of one that's still genuinely running.
	"""
	cutoff = add_to_date(now_datetime(), minutes=-STUCK_THRESHOLD_MINUTES)
	stuck = frappe.get_all(
		"Currency Exchange Log",
		filters={"matching_status": "Pending", "creation": ["<", cutoff]},
		pluck="name",
	)
	for log_name in stuck:
		frappe.enqueue(
			"currency_audit.matching.run_matching_job",
			queue="short",
			log_name=log_name,
			job_id=matching_job_id(log_name),
			deduplicate=True,
		)


def _companies_with_currency(currency):
	"""Companies whose base currency is `currency` — narrows matches to the specific
	pair a log entry is about, not just its from_currency (see Point C: without this,
	a USD→EGP and a USD→INR log with the same numeric rate both falsely claim any USD
	document, since a document's own conversion_rate never states its to_currency —
	only which company, and therefore which base currency, it converts into).

	Cached (companies rarely change) and empty on a cache miss means "couldn't
	determine any" — callers must skip the company filter in that case rather than
	excluding every document, since default_currency could plausibly not match any
	company for less-common currencies without that being this job's problem to solve.

	Args:
		currency (str): the currency code to look up companies for.

	Returns:
		list[str]: company names whose default_currency equals `currency`.
	"""
	cache_key = f"currency_audit:companies_with_currency:{currency}"
	companies = frappe.cache().get_value(cache_key)
	if companies is None:
		companies = frappe.get_all("Company", filters={"default_currency": currency}, pluck="name")
		frappe.cache().set_value(cache_key, companies, expires_in_sec=3600)
	return companies


def _rate_windows(log):
	"""The exact period each of this log's old_rate/new_rate was actually active
	for, based on this log's own Currency Exchange record's chronological history
	of entries — the mechanism behind match_confidence (Confirmed/Possible).

	Point 10/13: a rate value can recur (50 -> 52 -> 50 -> 52) without a matching
	transaction's currency+rate alone being enough to say which occurrence it
	belongs to. This never excludes a match on that basis (Principle 2: value match,
	not date match) — it only labels confidence, so an ambiguous case is still shown,
	just flagged for a second look instead of presented with false certainty.

	Uses full changed_on timestamps, not dates — changed_on always has real
	second-level precision (it's stamped by the hook/backfill at the moment of the
	edit), and comparing at that precision instead of truncating to a day is what
	makes _confidence actually decisive for same-day edits, rather than treating
	the whole day as shared/ambiguous territory.

	Args:
		log (Document): the Currency Exchange Log entry being matched.

	Returns:
		dict: {"Old Rate": (start, end), "New Rate": (start, end)}, each a
			(datetime | None, datetime | None) tuple — a window bound is None when
			open-ended (no entry before/after this one for the same record).
	"""
	if not log.currency_exchange:
		# External entries (F1.6) have no source Currency Exchange document to
		# chain against — grouping by a blank currency_exchange would otherwise
		# lump together unrelated External entries for entirely different currency
		# pairs. No ordering basis exists for these, so leave both windows open.
		return {"Old Rate": (None, None), "New Rate": (None, None)}

	siblings = frappe.get_all(
		"Currency Exchange Log",
		filters={"currency_exchange": log.currency_exchange},
		fields=["name", "changed_on"],
		order_by="changed_on asc",
	)
	names = [s.name for s in siblings]
	timestamps = [get_datetime(s.changed_on) for s in siblings]

	if log.name not in names:
		# Not yet visible in this ordering (e.g. mid-insert in a test) — no basis to
		# score confidence, so leave both windows fully open rather than guessing.
		return {"Old Rate": (None, None), "New Rate": (None, None)}

	idx = names.index(log.name)
	return {
		"Old Rate": (timestamps[idx - 1] if idx > 0 else None, timestamps[idx]),
		"New Rate": (timestamps[idx], timestamps[idx + 1] if idx + 1 < len(timestamps) else None),
	}


def _posting_datetime(posting_date, posting_time=None):
	"""Combine a matched document's posting_date (and posting_time, for the doctypes
	that have one) into a full timestamp for accurate window comparison.

	Sales Invoice, Purchase Invoice, POS Invoice, Purchase Receipt, and Delivery
	Note all carry a posting_time alongside posting_date — real precision, used
	here. Payment Entry and Journal Entry have no such field in ERPNext at all, so
	for those this falls back to midnight: the best available precision for those
	two doctypes is, honestly, day-level — there's no time-of-day data to combine.

	Args:
		posting_date (date | None): the document's posting date.
		posting_time (time | timedelta | str | None): the document's posting time,
			where the doctype has one.

	Returns:
		datetime | None: the combined timestamp, or None if posting_date is missing.
	"""
	if posting_date is None:
		return None
	return datetime.combine(getdate(posting_date), get_time(posting_time) if posting_time else time_of_day.min)


def _confidence(posting_datetime, window):
	"""Confirmed if posting_datetime falls within [start, end] (inclusive both
	ends, as a safety margin for the rare case of an exact-instant match), else
	Possible.

	Args:
		posting_datetime (datetime | None): the matched document's full timestamp —
			see _posting_datetime.
		window (tuple[datetime | None, datetime | None]): this rate's active period.

	Returns:
		str: "Confirmed" or "Possible".
	"""
	start, end = window
	if posting_datetime is None:
		return "Possible"
	if start and posting_datetime < start:
		return "Possible"
	if end and posting_datetime > end:
		return "Possible"
	return "Confirmed"


def _match_standard_doctype(doctype, currency, rate, rate_type, companies, window):
	"""Match submitted documents of one of the five standard doctypes against a rate.

	Args:
		doctype (str): one of Sales Invoice / Purchase Invoice / POS Invoice / Purchase Receipt / Delivery Note.
		currency (str): transaction currency to filter on.
		rate (float): conversion_rate value to match exactly.
		rate_type (str): "Old Rate" or "New Rate", recorded on each usage row.
		companies (list[str]): companies whose base currency is the log's to_currency — a
			conversion_rate only means what the log claims for a document belonging to one
			of these. Empty means "couldn't determine any", so the filter is skipped rather
			than excluding everything (see _companies_with_currency).
		window (tuple[datetime | None, datetime | None]): this rate's active period,
			for match_confidence — see _rate_windows.

	Returns:
		list[dict]: usage child-table rows for matches found.
	"""
	filters = {"currency": currency, "conversion_rate": rate, "docstatus": 1}
	if companies:
		filters["company"] = ["in", companies]

	# All five standard doctypes carry posting_time alongside posting_date, so
	# match_confidence gets real timestamp precision here, not just a day.
	matches = frappe.get_all(
		doctype,
		filters=filters,
		fields=["name", "posting_date", "posting_time", "grand_total", "currency"],
	)
	return [
		{
			"reference_doctype": doctype,
			"reference_name": m.name,
			"posting_date": m.posting_date,
			"currency": m.currency,
			"amount": m.grand_total,
			"rate_type": rate_type,
			"match_confidence": _confidence(_posting_datetime(m.posting_date, m.posting_time), window),
		}
		for m in matches
	]


def _match_payment_entry(currency, rate, rate_type, companies, window):
	"""Match submitted Payment Entries against a rate on either side of the transaction.

	Args:
		currency (str): account currency to filter on, checked against both the source and target side.
		rate (float): source_exchange_rate / target_exchange_rate value to match exactly.
		rate_type (str): "Old Rate" or "New Rate", recorded on each usage row.
		companies (list[str]): see _match_standard_doctype; skipped if empty.
		window (tuple[datetime | None, datetime | None]): see _match_standard_doctype.

	Returns:
		list[dict]: usage child-table rows for matches found.
	"""
	rows = []

	# Payment Entry has no posting_time field in ERPNext — day-level is the best
	# precision actually available here (see _posting_datetime).
	source_filters = {
		"paid_from_account_currency": currency,
		"source_exchange_rate": rate,
		"docstatus": 1,
	}
	if companies:
		source_filters["company"] = ["in", companies]

	source_matches = frappe.get_all(
		"Payment Entry",
		filters=source_filters,
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
			"match_confidence": _confidence(_posting_datetime(m.posting_date), window),
		}
		for m in source_matches
	]

	target_filters = {
		"paid_to_account_currency": currency,
		"target_exchange_rate": rate,
		"docstatus": 1,
	}
	if companies:
		target_filters["company"] = ["in", companies]

	target_matches = frappe.get_all(
		"Payment Entry",
		filters=target_filters,
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
			"match_confidence": _confidence(_posting_datetime(m.posting_date), window),
		}
		for m in target_matches
	]

	return rows


def _match_journal_entry(currency, rate, rate_type, companies, window):
	"""Match submitted Journal Entry rows (per-line rate/currency) against a rate.

	Args:
		currency (str): account currency to filter on within the `accounts` child table.
		rate (float): per-row exchange_rate value to match exactly.
		rate_type (str): "Old Rate" or "New Rate", recorded on each usage row.
		companies (list[str]): see _match_standard_doctype; skipped if empty. Applied on
			the parent Journal Entry, since company isn't a field on the child row itself.
		window (tuple[datetime | None, datetime | None]): see _match_standard_doctype.

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

	parent_filters = {"name": ["in", list({m.parent for m in matches})]}
	if companies:
		parent_filters["company"] = ["in", companies]

	# Journal Entry has no posting_time field either — same day-level limitation as
	# Payment Entry (see _posting_datetime).
	posting_dates = {
		p.name: p.posting_date
		for p in frappe.get_all("Journal Entry", filters=parent_filters, fields=["name", "posting_date"])
	}

	for m in matches:
		if m.parent not in posting_dates:
			# Parent's company doesn't match the log's to_currency — not this log's match.
			continue
		posting_date = posting_dates.get(m.parent)
		rows.append(
			{
				"reference_doctype": "Journal Entry",
				"reference_name": m.parent,
				"posting_date": posting_date,
				"currency": m.account_currency,
				"amount": flt(m.debit_in_account_currency) or flt(m.credit_in_account_currency),
				"rate_type": rate_type,
				"match_confidence": _confidence(_posting_datetime(posting_date), window),
			}
		)

	return rows
