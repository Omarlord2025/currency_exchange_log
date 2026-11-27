"""Tests for currency_audit.market_rate_fetcher.

Independent reference tables only — nothing here touches the real ERPNext
Currency Exchange doctype. requests.get is always mocked; no test hits the real
API.
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import nowdate

from currency_audit import market_rate_fetcher as fetcher

EGP = "USD EGP Exchange Rate"
CNY = "USD CNY Exchange Rate"


def _configure_settings(host="https://api.example.com", api_key="test-key", enabled=1):
	"""Set the shared settings singleton for a test.

	Args:
		host (str): API base URL to configure.
		api_key (str): API key to configure.
		enabled (int): 1 to enable the daily job, 0 to disable it.

	Returns:
		Document: the saved settings singleton.
	"""
	settings = frappe.get_single(fetcher.SETTINGS_DOCTYPE)
	settings.host = host
	settings.api_key = api_key
	settings.enabled = enabled
	settings.flags.ignore_mandatory = True
	settings.save(ignore_permissions=True)
	frappe.clear_cache(doctype=fetcher.SETTINGS_DOCTYPE)
	return settings


def _mock_response(json_data, status_ok=True):
	"""Build a MagicMock standing in for a requests.Response.

	Args:
		json_data (dict): what .json() should return.
		status_ok (bool): whether raise_for_status() should be a no-op.

	Returns:
		MagicMock: the fake response object.
	"""
	response = MagicMock()
	response.json.return_value = json_data
	if not status_ok:
		response.raise_for_status.side_effect = Exception("HTTP error")
	return response


class TestGetSettings(FrappeTestCase):
	def test_returns_none_when_disabled(self):
		_configure_settings(enabled=0)
		self.assertIsNone(fetcher._get_settings())

	def test_returns_none_when_not_configured(self):
		_configure_settings(host="", api_key="", enabled=1)
		self.assertIsNone(fetcher._get_settings())

	def test_returns_settings_when_enabled_and_configured(self):
		_configure_settings()
		self.assertIsNotNone(fetcher._get_settings())


class TestStoreRate(FrappeTestCase):
	def test_creates_a_row_per_currency(self):
		self.assertTrue(fetcher.store_rate("EGP", "2024-06-01", 47.5, fetched_via="Backfill"))
		self.assertTrue(fetcher.store_rate("CNY", "2024-06-01", 7.2, fetched_via="Backfill"))

		egp = frappe.get_all(EGP, filters={"date": "2024-06-01"}, fields=["rate", "fetched_via"])
		cny = frappe.get_all(CNY, filters={"date": "2024-06-01"}, fields=["rate"])
		self.assertEqual((len(egp), egp[0].rate, egp[0].fetched_via), (1, 47.5, "Backfill"))
		self.assertEqual((len(cny), cny[0].rate), (1, 7.2))

	def test_is_idempotent_per_date(self):
		fetcher.store_rate("CNY", "2024-06-02", 7.2, fetched_via="Backfill")
		self.assertFalse(fetcher.store_rate("CNY", "2024-06-02", 7.3, fetched_via="Daily Job"))

		row = frappe.get_all(CNY, filters={"date": "2024-06-02"}, fields=["rate"])
		self.assertEqual((len(row), row[0].rate), (1, 7.2))

	def test_treats_a_primary_key_collision_as_already_stored(self):
		"""A concurrent insert that slips past the exists() check must not raise."""
		with patch("currency_audit.market_rate_fetcher._has_rate_for", return_value=False):
			fetcher.store_rate("CNY", "2024-06-05", 7.2, fetched_via="Backfill")
			self.assertFalse(fetcher.store_rate("CNY", "2024-06-05", 7.3, fetched_via="Daily Job"))

	def test_stores_the_raw_response(self):
		fetcher.store_rate("CNY", "2024-06-03", 7.2, fetched_via="Backfill", raw_response={"quotes": {"USDCNY": 7.2}})
		raw = frappe.db.get_value(CNY, {"date": "2024-06-03"}, "raw_response")
		self.assertIn("USDCNY", raw)


class TestParseTimeseriesResponse(FrappeTestCase):
	def test_parses_the_quotes_shape_for_every_currency(self):
		data = {
			"quotes": {
				"2024-01-01": {"USDEGP": 47.1, "USDCNY": 7.1},
				"2024-01-02": {"USDEGP": 47.2, "USDCNY": 7.2},
			}
		}
		self.assertEqual(
			fetcher._parse_timeseries_response(data),
			{"EGP": {"2024-01-01": 47.1, "2024-01-02": 47.2}, "CNY": {"2024-01-01": 7.1, "2024-01-02": 7.2}},
		)

	def test_parses_the_legacy_rates_shape(self):
		data = {"rates": {"2024-01-01": {"EGP": 47.1, "CNY": 7.1}}}
		self.assertEqual(
			fetcher._parse_timeseries_response(data), {"EGP": {"2024-01-01": 47.1}, "CNY": {"2024-01-01": 7.1}}
		)

	def test_returns_empty_inner_dicts_for_an_unrecognised_shape(self):
		self.assertEqual(fetcher._parse_timeseries_response({"unexpected": "shape"}), {"EGP": {}, "CNY": {}})


class TestFetchLiveRates(FrappeTestCase):
	def test_one_request_returns_every_currency(self):
		with patch("currency_audit.market_rate_fetcher.requests.get") as mock_get:
			mock_get.return_value = _mock_response({"quotes": {"USDEGP": 47.9, "USDCNY": 7.25}})
			rates, raw = fetcher._fetch_live_rates("https://api.example.com/", "key123")

		self.assertEqual(rates, {"EGP": 47.9, "CNY": 7.25})
		self.assertEqual(raw["quotes"]["USDCNY"], 7.25)

		self.assertEqual(mock_get.call_count, 1)
		args, kwargs = mock_get.call_args
		self.assertEqual(args[0], "https://api.example.com/live")
		self.assertEqual(kwargs["params"]["access_key"], "key123")
		self.assertEqual(kwargs["params"]["source"], "USD")
		self.assertEqual(kwargs["params"]["currencies"], "EGP,CNY")

	def test_throws_and_logs_on_an_unexpected_shape(self):
		with patch("currency_audit.market_rate_fetcher.requests.get") as mock_get:
			mock_get.return_value = _mock_response({"quotes": {}})
			with patch("currency_audit.market_rate_fetcher.frappe.log_error") as mock_log_error:
				self.assertRaises(frappe.ValidationError, fetcher._fetch_live_rates, "https://api.example.com", "k")
		mock_log_error.assert_called_once()

	def test_quota_exhausted_error_is_reported_distinctly(self):
		body = {"success": False, "error": {"code": 104, "info": "monthly usage limit reached"}}
		with patch("currency_audit.market_rate_fetcher.requests.get", return_value=_mock_response(body)):
			with patch("currency_audit.market_rate_fetcher.frappe.log_error"):
				with self.assertRaises(frappe.ValidationError) as ctx:
					fetcher._fetch_live_rates("https://api.example.com", "k")
		self.assertIn("monthly request limit", str(ctx.exception))

	def test_other_api_errors_are_not_mistaken_for_a_shape_problem(self):
		body = {"success": False, "error": {"code": 101, "info": "invalid access key"}}
		with patch("currency_audit.market_rate_fetcher.requests.get", return_value=_mock_response(body)):
			with patch("currency_audit.market_rate_fetcher.frappe.log_error"):
				with self.assertRaises(frappe.ValidationError) as ctx:
					fetcher._fetch_live_rates("https://api.example.com", "k")
		self.assertIn("returned an error", str(ctx.exception))


class TestFetchTimeseries(FrappeTestCase):
	def test_calls_the_timeframe_endpoint_not_timeseries(self):
		"""Regression test: this provider's date-range endpoint is named
		"timeframe" — "timeseries" is a plausible-looking but wrong guess that
		404s with invalid_api_function against the real API."""
		with patch("currency_audit.market_rate_fetcher.requests.get") as mock_get:
			mock_get.return_value = _mock_response({"quotes": {"2024-01-01": {"USDEGP": 47.1, "USDCNY": 7.1}}})
			rates, raw = fetcher._fetch_timeseries("https://api.example.com", "key123", "2024-01-01", "2024-01-01")

		self.assertEqual(rates, {"EGP": {"2024-01-01": 47.1}, "CNY": {"2024-01-01": 7.1}})

		self.assertEqual(mock_get.call_count, 1)
		args, kwargs = mock_get.call_args
		self.assertEqual(args[0], "https://api.example.com/timeframe")
		self.assertEqual(kwargs["params"]["access_key"], "key123")
		self.assertEqual(kwargs["params"]["start_date"], "2024-01-01")
		self.assertEqual(kwargs["params"]["end_date"], "2024-01-01")
		self.assertEqual(kwargs["params"]["currencies"], "EGP,CNY")


class TestFetchToday(FrappeTestCase):
	def test_stores_todays_rate_for_every_currency(self):
		_configure_settings()
		with patch(
			"currency_audit.market_rate_fetcher._fetch_live_rates",
			return_value=({"EGP": 47.5, "CNY": 7.2}, {}),
		):
			fetcher.fetch_today()

		for doctype in (EGP, CNY):
			row = frappe.get_all(doctype, filters={"date": nowdate()}, fields=["fetched_via"])
			self.assertEqual([r.fetched_via for r in row], ["Daily Job"])

	def test_does_nothing_when_not_configured(self):
		_configure_settings(host="", api_key="")
		with patch("currency_audit.market_rate_fetcher._fetch_live_rates") as mock_fetch:
			fetcher.fetch_today()
		mock_fetch.assert_not_called()

	def test_logs_and_does_not_raise_when_the_api_call_fails(self):
		_configure_settings()
		with patch("currency_audit.market_rate_fetcher._fetch_live_rates", side_effect=Exception("boom")):
			with patch("currency_audit.market_rate_fetcher.frappe.log_error") as mock_log_error:
				fetcher.fetch_today()  # must not raise
		mock_log_error.assert_called_once()


class TestRetryIfMissing(FrappeTestCase):
	def test_makes_no_api_call_when_every_currency_is_recorded(self):
		_configure_settings()
		for currency in fetcher.RATE_DOCTYPES:
			fetcher.store_rate(currency, nowdate(), 1.0, fetched_via="Daily Job")

		with patch("currency_audit.market_rate_fetcher._fetch_live_rates") as mock_fetch:
			fetcher.retry_if_missing()
		mock_fetch.assert_not_called()

	def test_fetches_and_stores_only_the_missing_currency(self):
		_configure_settings()
		fetcher.store_rate("EGP", nowdate(), 47.0, fetched_via="Daily Job")

		with patch(
			"currency_audit.market_rate_fetcher._fetch_live_rates",
			return_value=({"EGP": 99.0, "CNY": 7.2}, {}),
		) as mock_fetch:
			fetcher.retry_if_missing()

		self.assertEqual(mock_fetch.call_count, 1)
		self.assertEqual(frappe.db.get_value(EGP, {"date": nowdate()}, "rate"), 47.0)
		self.assertEqual(frappe.db.get_value(CNY, {"date": nowdate()}, "rate"), 7.2)


class TestBackfillLast60Days(FrappeTestCase):
	def _series(self):
		return (
			{
				"EGP": {"2024-07-01": 47.0, "2024-07-02": 47.1},
				"CNY": {"2024-07-01": 7.0, "2024-07-02": 7.1},
			},
			{},
		)

	def test_stores_every_returned_day_for_every_currency_once(self):
		_configure_settings()
		with patch("currency_audit.market_rate_fetcher._fetch_timeseries", return_value=self._series()):
			result = fetcher.backfill_last_60_days()

		self.assertEqual(result, {"fetched": 4, "stored": 4})
		for doctype in (EGP, CNY):
			for date in ("2024-07-01", "2024-07-02"):
				self.assertTrue(frappe.db.exists(doctype, {"date": date}))

	def test_skips_days_already_recorded(self):
		_configure_settings()
		fetcher.store_rate("CNY", "2024-07-01", 6.0, fetched_via="Daily Job")

		with patch("currency_audit.market_rate_fetcher._fetch_timeseries", return_value=self._series()):
			result = fetcher.backfill_last_60_days()

		self.assertEqual(result, {"fetched": 4, "stored": 3})
		# The pre-existing day's value is untouched by the backfill.
		self.assertEqual(frappe.db.get_value(CNY, {"date": "2024-07-01"}, "rate"), 6.0)

	def test_throws_when_not_configured(self):
		_configure_settings(host="", api_key="")
		self.assertRaises(frappe.ValidationError, fetcher.backfill_last_60_days)
