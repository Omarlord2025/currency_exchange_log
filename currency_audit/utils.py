"""Shared helpers for creating Currency Exchange Log entries.

Reused by both the change-detection hook (F1, currency_audit.events.currency_exchange)
and the baseline backfill patch (F6, currency_audit.patches.*), so the log-creation
and matching-job-enqueue logic lives here once instead of being duplicated.
"""

import frappe
from frappe.utils import now_datetime

from currency_audit.matching import matching_job_id


def create_log_entry(
	currency_exchange,
	new_rate,
	old_rate=None,
	entry_type="Change",
	changed_by=None,
	changed_on=None,
	source_version=None,
):
	"""Create an immutable Currency Exchange Log entry and enqueue the F4 matching job.

	Args:
		currency_exchange (Document): the source Currency Exchange document the log is about.
		new_rate (float): the rate value to record as the current/new value.
		old_rate (float | None): the prior rate value; None for baseline entries (F6).
		entry_type (str): "Change", "Baseline", or "Recovered" (found via Version-history
			reconciliation — see currency_audit.reconciliation).
		changed_by (str | None): user who made the change; defaults to the current session user.
		changed_on (datetime | None): when the change happened; defaults to now.
		source_version (str | None): name of the Frappe Version record this entry was
			reconstructed from, for Recovered entries only.

	Returns:
		str: name of the created Currency Exchange Log document.
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
			"changed_by": changed_by or frappe.session.user,
			"changed_on": changed_on or now_datetime(),
			"source_version": source_version,
		}
	)
	log.insert(ignore_permissions=True)
	if not frappe.flags.in_test:
		# Committed before enqueue so a worker that picks the job up immediately can
		# always find the row — otherwise a fast worker can race the insert's own
		# transaction and see the log as not-yet-existing. Skipped in tests: an
		# explicit commit here would defeat FrappeTestCase's rollback-based
		# isolation and leak fixtures into the real database permanently.
		frappe.db.commit()

	frappe.enqueue(
		"currency_audit.matching.run_matching_job",
		queue="short",
		log_name=log.name,
		job_id=matching_job_id(log.name),
		deduplicate=True,
	)

	return log.name
