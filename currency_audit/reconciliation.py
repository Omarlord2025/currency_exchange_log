"""Version-history reconciliation — a second, active safety net on top of F1.

Currency Exchange has track_changes on (turned on by F6), so Frappe captures a
native Version record for every edit, independent of whether currency_audit's own
on_update hook (F1) actually fired. Until now that Version data just sat there,
unused, in Frappe's generic Version list — this module is what makes it useful: it
cross-references the two, and any Version record with an exchange_rate change and no
corresponding Currency Exchange Log entry means the primary hook missed that edit (a
bug, a bypass, the app being temporarily uninstalled/disabled while the Property
Setter kept versioning active regardless). A "Recovered" Log entry is created for
each gap found, so it doesn't stay invisible.

Two entry points, covering historical, present, and future use of the same Version
data:
- reconcile_version_history(since=None): the actual logic. since=None scans all
  history — used once by patches.reconcile_historical_versions to catch up on
  anything already sitting in Version by the time this feature shipped.
- reconcile_recent_version_history(): thin wrapper over a rolling recent window,
  registered as a daily scheduled job — the ongoing safety net going forward.
"""

import frappe
from frappe.utils import add_to_date, flt, now_datetime

from currency_audit import utils

# Rolling window for the daily sweep. Wider than the 1-day schedule interval so a
# missed run (site down overnight, etc.) can't let a gap slip through uncaught.
RECENT_WINDOW_DAYS = 3

# How close a Version's creation must be to an existing Log entry's changed_on to
# count as "the primary hook already handled this". Both fire within the same
# save(), so in practice they're within milliseconds of each other — generous on
# purpose, since erring toward "already covered" only costs a missed recovery,
# while erring toward "not covered" costs a harmless duplicate-looking entry.
COVERAGE_TOLERANCE_MINUTES = 5


def reconcile_recent_version_history():
	"""Scheduled daily sweep: reconcile Version history from the last few days."""
	reconcile_version_history(since=add_to_date(now_datetime(), days=-RECENT_WINDOW_DAYS))


def reconcile_version_history(since=None):
	"""Find Currency Exchange rate changes Frappe's Version history caught but
	Currency Exchange Log missed, and create a Recovered entry for each.

	Args:
		since (datetime | None): only consider Version records created on/after this;
			None scans all history.
	"""
	filters = {"ref_doctype": "Currency Exchange", "data": ["like", "%exchange_rate%"]}
	if since:
		filters["creation"] = [">=", since]

	# A cheap pre-filter, not a precise one (e.g. also matches a Version whose only
	# real change was to an unrelated field mentioning the word) — _extract_rate_change
	# does the real check. Volume here is bounded by how many times a rate was ever
	# edited, not by transaction volume, so a small per-row lookup below is fine.
	versions = frappe.get_all(
		"Version",
		filters=filters,
		fields=["name", "docname", "owner", "creation", "data"],
	)
	if not versions:
		return

	currency_exchanges = {
		row.name: row
		for row in frappe.get_all(
			"Currency Exchange",
			filters={"name": ["in", list({v.docname for v in versions})]},
			fields=["name", "from_currency", "to_currency"],
		)
	}

	for version in versions:
		currency_exchange = currency_exchanges.get(version.docname)
		if not currency_exchange:
			continue

		change = _extract_rate_change(version.data)
		if not change:
			continue

		if _already_covered(version.docname, version.creation):
			continue

		old_rate, new_rate = change
		utils.create_log_entry(
			currency_exchange=currency_exchange,
			new_rate=new_rate,
			old_rate=old_rate,
			entry_type="Recovered",
			changed_by=version.owner,
			changed_on=version.creation,
			source_version=version.name,
		)


def _extract_rate_change(version_data):
	"""Parse a Version record's diff JSON for an exchange_rate change.

	Args:
		version_data (str): the Version document's `data` field (JSON string).

	Returns:
		tuple[float, float] | None: (old_rate, new_rate), or None if this Version
			didn't actually change exchange_rate, or the value couldn't be parsed.
	"""
	try:
		data = frappe.parse_json(version_data)
	except Exception:
		return None

	for item in data.get("changed", []):
		if len(item) >= 3 and item[0] == "exchange_rate":
			# flt() ignores thousands-separators, matching how these values were
			# formatted going in (Document.get_formatted -> fmt_money).
			old_rate, new_rate = flt(item[1]), flt(item[2])
			if old_rate and new_rate:
				return old_rate, new_rate

	return None


def _already_covered(currency_exchange_name, version_creation):
	"""Whether an existing Change/Recovered Log entry already accounts for this edit.

	Args:
		currency_exchange_name (str): the Currency Exchange record's name.
		version_creation (datetime): the Version record's creation timestamp.

	Returns:
		bool: True if a Log entry already exists close enough to this timestamp
			(also true for a Version already turned into a Recovered entry by an
			earlier run — same timestamp, trivially within tolerance — so this
			doubles as the idempotency check).
	"""
	window_start = add_to_date(version_creation, minutes=-COVERAGE_TOLERANCE_MINUTES)
	window_end = add_to_date(version_creation, minutes=COVERAGE_TOLERANCE_MINUTES)
	return bool(
		frappe.db.exists(
			"Currency Exchange Log",
			{
				"currency_exchange": currency_exchange_name,
				"entry_type": ["in", ("Change", "Recovered")],
				"changed_on": ["between", [window_start, window_end]],
			},
		)
	)
