"""One-time rollout patch for match_confidence (Point 10/13's date-window scoring).

Existing Currency Exchange Log entries already marked "Completed" have a usage
table computed by the version of run_matching_job that predates match_confidence —
their rows won't have it until matching runs again. This forces that recompute
once, so confidence scoring isn't only available for activity from here on.
"""

import frappe

from currency_audit.matching import run_matching_job


def execute():
	"""Re-run matching for every already-Completed Currency Exchange Log entry."""
	completed = frappe.get_all(
		"Currency Exchange Log",
		filters={"matching_status": "Completed"},
		pluck="name",
	)
	for name in completed:
		run_matching_job(name)
