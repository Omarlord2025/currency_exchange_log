"""USD -> CNY daily market rate fetcher.

Independent reference/history table only (confirmed with the user) — never
touches the real ERPNext Currency Exchange doctype and never changes what rate
any transaction actually uses. Two entry points:

- backfill_last_60_days(): one-time/on-demand, fetches the last 60 days in a
  single API call (the provider's timeframe endpoint supports up to 365 days
  per call, so this is one request, not sixty).
- fetch_today(): the daily 9am scheduled job, fetches just today's rate.

Both funnel through store_rate(), which is idempotent per calendar date — no
deletion policy for now, rows are kept indefinitely.

API shape (exchangerate.host, now part of apilayer, access_key query param):
- GET {host}/live?access_key=...&source=USD&currencies=CNY
    -> {"quotes": {"USDCNY": <rate>}, ...}
- GET {host}/timeframe?access_key=...&start_date=...&end_date=...&source=USD&currencies=CNY
    -> {"quotes": {"2024-01-01": {"USDCNY": <rate>}, ...}, ...}
    Confirmed live against this provider's actual key: the endpoint is named
    "timeframe", not "timeseries" — "timeseries" 404s with invalid_api_function.
    _parse_timeseries_response still tries a legacy shape too and logs the raw
    response if neither matches, so a future provider change gives a concrete
    diagnostic instead of failing silently.
"""

import frappe
import requests
from frappe import _
from frappe.utils import add_days, flt, getdate, now_datetime, nowdate

API_TIMEOUT_SECONDS = 15
BACKFILL_DAYS = 60


def fetch_today():
	"""Scheduled daily job (9am): fetch and store today's USD->CNY rate."""
	settings = _get_settings()
	if not settings:
		return

	try:
		rate, raw = _fetch_live_rate(settings.host, settings.get_password("api_key"))
	except Exception:
		frappe.log_error(title="USD/CNY daily rate fetch failed")
		return

	store_rate(nowdate(), rate, fetched_via="Daily Job", raw_response=raw)


@frappe.whitelist()
def backfill_last_60_days():
	"""One-time/on-demand: fetch and store the last 60 days of USD->CNY rates in
	a single API call.

	Returns:
		dict: {"fetched": int, "stored": int} — how many days the API returned vs
			how many were newly stored (already-recorded days are skipped).
	"""
	frappe.only_for("System Manager")

	settings = _get_settings()
	if not settings:
		frappe.throw(_("Configure USD CNY Exchange Settings (host + API key, enabled) first."))

	start_date = add_days(nowdate(), -BACKFILL_DAYS)
	end_date = nowdate()

	rates, raw = _fetch_timeseries(settings.host, settings.get_password("api_key"), start_date, end_date)

	stored = 0
	for date, rate in rates.items():
		if store_rate(date, rate, fetched_via="Backfill", raw_response=raw):
			stored += 1

	return {"fetched": len(rates), "stored": stored}


def store_rate(date, rate, fetched_via, raw_response=None):
	"""Insert a USD CNY Exchange Rate row for `date` if one doesn't already exist.

	Args:
		date (str | date): the rate's calendar date.
		rate (float): 1 USD = `rate` CNY.
		fetched_via (str): "Backfill" or "Daily Job".
		raw_response (dict | None): the raw API response, stored for debugging.

	Returns:
		bool: True if a new row was created, False if that date was already recorded.
	"""
	date = getdate(date)
	if frappe.db.exists("USD CNY Exchange Rate", {"date": date}):
		return False

	doc = frappe.get_doc(
		{
			"doctype": "USD CNY Exchange Rate",
			"date": date,
			"rate": flt(rate, 9),
			"fetched_at": now_datetime(),
			"fetched_via": fetched_via,
			"raw_response": frappe.as_json(raw_response) if raw_response else None,
		}
	)
	doc.insert(ignore_permissions=True)
	if not frappe.flags.in_test:
		# Skipped in tests: an explicit commit here would defeat FrappeTestCase's
		# rollback-based isolation and leak fixtures into the real database.
		frappe.db.commit()
	return True


def _get_settings():
	"""Load USD CNY Exchange Settings if configured and enabled.

	Returns:
		Document | None: the settings doc, or None if disabled or not configured.
	"""
	settings = frappe.get_cached_doc("USD CNY Exchange Settings")
	if not settings.enabled or not settings.host or not settings.api_key:
		return None
	return settings


def _fetch_live_rate(host, api_key):
	"""Call the /live endpoint for today's USD->CNY rate.

	Args:
		host (str): API base URL, e.g. https://api.exchangerate.host.
		api_key (str): the decrypted API key.

	Returns:
		tuple[float, dict]: (rate, raw_response_json).
	"""
	response = requests.get(
		f"{host.rstrip('/')}/live",
		params={"access_key": api_key, "source": "USD", "currencies": "CNY"},
		timeout=API_TIMEOUT_SECONDS,
	)
	response.raise_for_status()
	data = response.json()

	rate = (data.get("quotes") or {}).get("USDCNY")
	if rate is None:
		frappe.log_error(title="USD/CNY live rate: unexpected response shape", message=frappe.as_json(data))
		frappe.throw(_("Unexpected response from the exchange rate API — see Error Log."))

	return flt(rate, 9), data


def _fetch_timeseries(host, api_key, start_date, end_date):
	"""Call the /timeframe endpoint for a date range in a single request.

	Note: this provider's date-range endpoint is named "timeframe", not the more
	common "timeseries" — confirmed against a real key (the latter 404s).

	Args:
		host (str): API base URL.
		api_key (str): the decrypted API key.
		start_date (str): YYYY-MM-DD.
		end_date (str): YYYY-MM-DD.

	Returns:
		tuple[dict, dict]: ({date_str: rate}, raw_response_json).
	"""
	response = requests.get(
		f"{host.rstrip('/')}/timeframe",
		params={
			"access_key": api_key,
			"start_date": start_date,
			"end_date": end_date,
			"source": "USD",
			"currencies": "CNY",
		},
		timeout=API_TIMEOUT_SECONDS,
	)
	response.raise_for_status()
	data = response.json()

	rates = _parse_timeseries_response(data)
	if not rates:
		frappe.log_error(title="USD/CNY timeseries: unexpected response shape", message=frappe.as_json(data))
		frappe.throw(_("Unexpected response from the exchange rate API — see Error Log."))

	return rates, data


def _parse_timeseries_response(data):
	"""Extract {date: rate} from a /timeframe response, trying both documented
	response shapes (they differ between provider API versions):

	- apilayer/"live"-consistent: {"quotes": {"2024-01-01": {"USDCNY": 7.5}}}
	- legacy exchangerate.host:   {"rates":  {"2024-01-01": {"CNY": 7.5}}}

	Args:
		data (dict): the parsed JSON response.

	Returns:
		dict: {date_str: float}, empty if neither shape matched.
	"""
	quotes = data.get("quotes")
	if isinstance(quotes, dict):
		rates = {
			date_str: flt(day["USDCNY"], 9)
			for date_str, day in quotes.items()
			if isinstance(day, dict) and "USDCNY" in day
		}
		if rates:
			return rates

	rates_by_date = data.get("rates")
	if isinstance(rates_by_date, dict):
		rates = {
			date_str: flt(day["CNY"], 9)
			for date_str, day in rates_by_date.items()
			if isinstance(day, dict) and "CNY" in day
		}
		if rates:
			return rates

	return {}
