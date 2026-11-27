"""Daily USD -> {EGP, CNY} market rate fetcher (one shared API key, one request).

Independent reference/history tables only — never touches the real ERPNext
Currency Exchange doctype and never changes what rate any transaction uses.

All target currencies are fetched together in a single request (the provider
accepts `currencies=EGP,CNY`), so adding a currency costs no extra API calls —
this matters because the provider's free plan is only ~100 requests/month. The
host and key come from the one shared "USD EGP Exchange Settings" single (its
name predates the CNY addition; renaming a single doctype isn't worth the
migration risk).

Entry points:
- fetch_today(): the 9am scheduled job.
- retry_if_missing(): the 9:15 scheduled job. A DB-only check that re-calls the
  API only if a currency has no row for today, so a transient failure at 9am
  doesn't lose that day's rate and normal days cost no extra request.
- backfill_last_60_days(): on-demand, one /timeframe call for every currency.

API shape (exchangerate.host, now part of apilayer, access_key query param):
- GET {host}/live?access_key=...&source=USD&currencies=EGP,CNY
    -> {"quotes": {"USDEGP": <rate>, "USDCNY": <rate>}, ...}
- GET {host}/timeframe?access_key=...&start_date=...&end_date=...&source=USD&currencies=EGP,CNY
    -> {"quotes": {"2024-01-01": {"USDEGP": <rate>, "USDCNY": <rate>}, ...}, ...}
    The endpoint is "timeframe", not "timeseries" — "timeseries" 404s with
    invalid_api_function. _parse_timeseries_response also tries a legacy shape
    and logs the raw response if neither matches.
- Errors come back as {"success": false, "error": {"code": ..., "info": ...}},
  often with HTTP 200; code 104 means the monthly request quota is used up.
"""

import frappe
import requests
from frappe import _
from frappe.utils import add_days, flt, getdate, now_datetime, nowdate

API_TIMEOUT_SECONDS = 15
BACKFILL_DAYS = 60
SOURCE_CURRENCY = "USD"
QUOTA_EXCEEDED_CODE = 104

# target currency -> the doctype that stores its daily rates. Adding a currency
# is a new doctype plus one entry here.
RATE_DOCTYPES = {
	"EGP": "USD EGP Exchange Rate",
	"CNY": "USD CNY Exchange Rate",
}
SETTINGS_DOCTYPE = "USD EGP Exchange Settings"


def fetch_today():
	"""Scheduled daily job (9am): fetch and store today's rate for every currency.

	Never raises — a failed API call is logged and picked up by retry_if_missing.
	"""
	_fetch_and_store_today(fetched_via="Daily Job")


def retry_if_missing():
	"""Scheduled job (9:15am): re-fetch only if some currency has no row for today.

	Why a separate job rather than an in-process retry: the daily job runs in a
	short-lived worker, and a DB-only check keeps the common (already fetched)
	case free of any API call.
	"""
	missing = [c for c in RATE_DOCTYPES if not _has_rate_for(c, nowdate())]
	if missing:
		_fetch_and_store_today(fetched_via="Daily Job", only=missing)


def _fetch_and_store_today(fetched_via, only=None):
	"""Fetch today's live rates in one request and store them.

	Args:
		fetched_via (str): "Daily Job" or "Backfill", stored on each row.
		only (list[str] | None): currencies to store; None means all of them.
	"""
	settings = _get_settings()
	if not settings:
		return

	try:
		rates, raw = _fetch_live_rates(settings.host, settings.get_password("api_key"))
	except Exception:
		frappe.log_error(title="USD market rate daily fetch failed")
		return

	for currency in only or RATE_DOCTYPES:
		if currency in rates:
			store_rate(currency, nowdate(), rates[currency], fetched_via=fetched_via, raw_response=raw)


@frappe.whitelist()
def backfill_last_60_days():
	"""On-demand: fetch and store the last 60 days of rates for every currency in
	a single API call.

	Returns:
		dict: {"fetched": int, "stored": int} — day-currency rates the API returned
			vs how many were newly stored (already-recorded days are skipped).
	"""
	frappe.only_for("System Manager")

	settings = _get_settings()
	if not settings:
		frappe.throw(_("Configure USD EGP Exchange Settings (host + API key, enabled) first."))

	rates_by_currency, raw = _fetch_timeseries(
		settings.host,
		settings.get_password("api_key"),
		add_days(nowdate(), -BACKFILL_DAYS),
		nowdate(),
	)

	fetched = stored = 0
	for currency, by_date in rates_by_currency.items():
		for date, rate in by_date.items():
			fetched += 1
			if store_rate(currency, date, rate, fetched_via="Backfill", raw_response=raw):
				stored += 1

	return {"fetched": fetched, "stored": stored}


def store_rate(currency, date, rate, fetched_via, raw_response=None):
	"""Insert a rate row for `currency` on `date` if one doesn't already exist.

	One row per date is enforced by the rate doctypes' deterministic autoname
	(USD-<CUR>-<date>), so two concurrent inserts collide on the primary key
	(DuplicateEntryError) instead of both succeeding. The exists() check still
	covers older rows that were named with the previous counter scheme.

	Args:
		currency (str): target currency, a key of RATE_DOCTYPES.
		date (str | date): the rate's calendar date.
		rate (float): 1 USD = `rate` units of `currency`.
		fetched_via (str): "Backfill" or "Daily Job".
		raw_response (dict | None): the raw API response, stored for debugging.

	Returns:
		bool: True if a new row was created, False if that date was already recorded.
	"""
	doctype = RATE_DOCTYPES[currency]
	date = getdate(date)
	if _has_rate_for(currency, date):
		return False

	doc = frappe.get_doc(
		{
			"doctype": doctype,
			"date": date,
			"rate": flt(rate, 9),
			"fetched_at": now_datetime(),
			"fetched_via": fetched_via,
			"raw_response": frappe.as_json(raw_response) if raw_response else None,
		}
	)
	try:
		doc.insert(ignore_permissions=True)
	except frappe.DuplicateEntryError:
		return False

	if not frappe.flags.in_test:
		# Skipped in tests: an explicit commit here would defeat FrappeTestCase's
		# rollback-based isolation and leak fixtures into the real database.
		frappe.db.commit()
	return True


def _has_rate_for(currency, date):
	"""Whether a rate row already exists for `currency` on `date`.

	Args:
		currency (str): a key of RATE_DOCTYPES.
		date (str | date): the calendar date.

	Returns:
		bool: True if recorded.
	"""
	return bool(frappe.db.exists(RATE_DOCTYPES[currency], {"date": getdate(date)}))


def _get_settings():
	"""Load the shared settings if configured and enabled.

	Returns:
		Document | None: the settings doc, or None if disabled or not configured.
	"""
	settings = frappe.get_cached_doc(SETTINGS_DOCTYPE)
	if not settings.enabled or not settings.host or not settings.api_key:
		return None
	return settings


def _request(host, path, params):
	"""GET a provider endpoint and return its parsed JSON, raising on API errors.

	The provider reports errors (including the exhausted monthly quota) as a
	`{"success": false, "error": {...}}` body that can arrive with HTTP 200, so
	raise_for_status alone would let them through as an "unexpected shape".

	Args:
		host (str): API base URL, e.g. https://api.exchangerate.host.
		path (str): endpoint name, "live" or "timeframe".
		params (dict): query parameters (access_key is added by the caller).

	Returns:
		dict: the parsed response.
	"""
	response = requests.get(f"{host.rstrip('/')}/{path}", params=params, timeout=API_TIMEOUT_SECONDS)
	response.raise_for_status()
	data = response.json()

	if isinstance(data, dict) and data.get("success") is False:
		error = data.get("error") or {}
		code = error.get("code")
		frappe.log_error(title=f"Exchange rate API error ({code})", message=frappe.as_json(data))
		if code == QUOTA_EXCEEDED_CODE:
			frappe.throw(_("The exchange rate API's monthly request limit has been reached."))
		frappe.throw(_("The exchange rate API returned an error — see Error Log."))

	return data


def _fetch_live_rates(host, api_key):
	"""Call /live once for every currency's today rate.

	Args:
		host (str): API base URL.
		api_key (str): the decrypted API key.

	Returns:
		tuple[dict, dict]: ({currency: rate}, raw_response_json) — only currencies
			present in the response are included.
	"""
	data = _request(
		host,
		"live",
		{"access_key": api_key, "source": SOURCE_CURRENCY, "currencies": ",".join(RATE_DOCTYPES)},
	)

	quotes = data.get("quotes") or {}
	rates = {c: flt(quotes[SOURCE_CURRENCY + c], 9) for c in RATE_DOCTYPES if SOURCE_CURRENCY + c in quotes}
	if not rates:
		frappe.log_error(title="USD live rates: unexpected response shape", message=frappe.as_json(data))
		frappe.throw(_("Unexpected response from the exchange rate API — see Error Log."))

	return rates, data


def _fetch_timeseries(host, api_key, start_date, end_date):
	"""Call /timeframe for a date range, all currencies in a single request.

	Args:
		host (str): API base URL.
		api_key (str): the decrypted API key.
		start_date (str): YYYY-MM-DD.
		end_date (str): YYYY-MM-DD.

	Returns:
		tuple[dict, dict]: ({currency: {date_str: rate}}, raw_response_json).
	"""
	data = _request(
		host,
		"timeframe",
		{
			"access_key": api_key,
			"start_date": start_date,
			"end_date": end_date,
			"source": SOURCE_CURRENCY,
			"currencies": ",".join(RATE_DOCTYPES),
		},
	)

	rates = _parse_timeseries_response(data)
	if not any(rates.values()):
		frappe.log_error(title="USD timeseries: unexpected response shape", message=frappe.as_json(data))
		frappe.throw(_("Unexpected response from the exchange rate API — see Error Log."))

	return rates, data


def _parse_timeseries_response(data):
	"""Extract {currency: {date: rate}} from a /timeframe response, trying both
	documented shapes (they differ between provider API versions):

	- apilayer/"live"-consistent: {"quotes": {"2024-01-01": {"USDEGP": 47.5}}}
	- legacy exchangerate.host:   {"rates":  {"2024-01-01": {"EGP": 47.5}}}

	Args:
		data (dict): the parsed JSON response.

	Returns:
		dict: {currency: {date_str: float}} for every currency in RATE_DOCTYPES
			(empty inner dicts when neither shape matched).
	"""
	result = {}
	for currency in RATE_DOCTYPES:
		pair = SOURCE_CURRENCY + currency
		rates = {}
		for key, field in (("quotes", pair), ("rates", currency)):
			by_date = data.get(key)
			if isinstance(by_date, dict):
				rates = {
					date_str: flt(day[field], 9)
					for date_str, day in by_date.items()
					if isinstance(day, dict) and field in day
				}
			if rates:
				break
		result[currency] = rates
	return result
