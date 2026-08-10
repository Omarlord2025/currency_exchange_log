"""One-time corrective patch for Baseline entries mis-attributed by a bug in the
original backfill logic.

The old version of _create_baseline_logs() didn't pass changed_by/changed_on to
create_log_entry(), so every Baseline entry defaulted to whoever ran the patch —
always Administrator/CLI, never the actual employee who last set that rate. Fixed
in the backfill itself (it now reads the source Currency Exchange record's own
modified_by/modified), but that fix only affects newly created entries: the backfill
is idempotent (skips currency exchanges that already have a Baseline entry) and
entries are immutable once created, so every Baseline entry created by the buggy
version is permanently stuck with the wrong attribution unless corrected here.

Change entries are never touched — F1 captures changed_by/changed_on synchronously
during the real edit's own request, so they were never at risk of this bug.
"""

import frappe


def execute():
	"""Correct changed_by/changed_on on Baseline entries that disagree with their source."""
	baselines = frappe.get_all(
		"Currency Exchange Log",
		filters={"entry_type": "Baseline"},
		fields=["name", "currency_exchange", "changed_by", "changed_on"],
	)
	if not baselines:
		return

	sources = {
		row.name: row
		for row in frappe.get_all(
			"Currency Exchange",
			filters={"name": ["in", list({b.currency_exchange for b in baselines})]},
			fields=["name", "modified_by", "modified"],
		)
	}

	for entry in baselines:
		source = sources.get(entry.currency_exchange)
		if not source:
			continue
		if entry.changed_by == source.modified_by and str(entry.changed_on) == str(source.modified):
			continue

		log = frappe.get_doc("Currency Exchange Log", entry.name)
		log.add_comment(
			"Info",
			(
				f"Corrected changed_by/changed_on: was {entry.changed_by} at {entry.changed_on} "
				f"(the backfill script's own session — a bug), now {source.modified_by} at "
				f"{source.modified} (the source Currency Exchange record's own last editor)."
			),
		)
		log.flags.allow_baseline_attribution_correction = True
		log.changed_by = source.modified_by
		log.changed_on = source.modified
		log.save(ignore_permissions=True)
