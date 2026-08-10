"""One-time historical catch-up: reconciles all Version history for Currency
Exchange that predates this feature (see currency_audit.reconciliation), so any
pre-existing gap between F1's hook and Frappe's native versioning gets recovered
once, on top of the ongoing daily sweep this same code now also runs going forward.
"""

from currency_audit.reconciliation import reconcile_version_history


def execute():
	"""Run a full-history Version reconciliation once."""
	reconcile_version_history(since=None)
