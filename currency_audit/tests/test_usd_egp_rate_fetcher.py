"""Tests for currency_audit.usd_egp_rate_fetcher.

Independent reference table only — nothing here touches the real ERPNext
Currency Exchange doctype. requests.get is always mocked; no test hits the real
API (and no key is configured in this environment to hit it with anyway).
"""

from unittest.mock import MagicMock, patch

import frappe
from frappe.tests.utils import FrappeTestCase

from currency_audit import usd_egp_rate_fetcher as fetcher


def _configure_settings(host="https://api.example.com", api_key="test-key", enabled=1):
	"""Set the USD EGP Exchange Settings singleton for a test.

	Args:
		host (str): API base URL to configure.
		api_key (str): API key to configure.
		enabled (int): 1 to enable the daily job, 0 to disable it.

	Returns:
		Document: the saved settings singleton.
	"""
	settings = frappe.get_single("USD EGP Exchange Settings")
	settings.host = host
	settings.api_key = api_key
	settings.enabled = enabled
	settings.flags.ignore_mandatory = True
	settings.save(ignore_permissions=True)
	frappe.clear_cache(doctype="USD EGP Exchange Settings")
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
	def test_creates_a_new_row(self):
		created = fetcher.store_rate("2024-06-01", 47.5, fetched_via="Backfill")
		self.assertTrue(created)

		row = frappe.get_all(
			"USD EGP Exchange Rate",
			filters={"date": "2024-06-01"},
			fields=["rate", "fetched_via"],
		)
		self.assertEqual(len(row), 1)
		self.assertEqual(row[0].rate, 47.5)
		self.assertEqual(row[0].fetched_via, "Backfill")

	def test_is_idempotent_per_date(self):
		fetcher.store_rate("2024-06-02", 47.5, fetched_via="Backfill")
		created_again = fetcher.store_rate("2024-06-02", 48.0, fetched_via="Daily Job")

		self.assertFalse(created_again)
		row = frappe.get_all("USD EGP Exchange Rate", filters={"date": "2024-06-02"}, fields=["rate"])
		self.assertEqual(len(row), 1)
		# The original value is untouched — store_rate never updates an existing row.
		self.assertEqual(row[0].rate, 47.5)

	def test_stores_the_raw_response(self):
		fetcher.store_rate("2024-06-03", 47.5, fetched_via="Backfill", raw_response={"quotes": {"USDEGP": 47.5}})
		raw = frappe.db.get_value("USD EGP Exchange Rate", {"date": "2024-06-03"}, "raw_response")
		self.assertIn("USDEGP", raw)


class TestParseTimeseriesResponse(FrappeTestCase):
	def test_parses_the_apilayer_quotes_shape(self):
		data = {"quotes": {"2024-01-01": {"USDEGP": 47.1}, "2024-01-02": {"USDEGP": 47.2}}}
		self.assertEqual(fetcher._parse_timeseries_response(data), {"2024-01-01": 47.1, "2024-01-02": 47.2})

	def test_parses_the_legacy_rates_shape(self):
		data = {"rates": {"2024-01-01": {"EGP": 47.1}, "2024-01-02": {"EGP": 47.2}}}
		self.assertEqual(fetcher._parse_timeseries_response(data), {"2024-01-01": 47.1, "2024-01-02": 47.2})

	def test_returns_empty_for_an_unrecognised_shape(self):
		self.assertEqual(fetcher._parse_timeseries_response({"unexpected": "shape"}), {})


class TestFetchLiveRate(FrappeTestCase):
	def test_parses_the_rate_and_calls_the_right_endpoint(self):
		with patch("currency_audit.usd_egp_rate_fetcher.requests.get") as mock_get:
			mock_get.return_value = _mock_response({"quotes": {"USDEGP": 47.9}})
			rate, raw = fetcher._fetch_live_rate("https://api.example.com", "key123")

		self.assertEqual(rate, 47.9)
		self.assertEqual(raw["quotes"]["USDEGP"], 47.9)

		args, kwargs = mock_get.call_args
		self.assertEqual(args[0], "https://api.example.com/live")
		self.assertEqual(kwargs["params"]["access_key"], "key123")
		self.assertEqual(kwargs["params"]["source"], "USD")
		self.assertEqual(kwargs["params"]["currencies"], "EGP")

	def test_throws_and_logs_on_an_unexpected_shape(self):
		with patch("currency_audit.usd_egp_rate_fetcher.requests.get") as mock_get:
			mock_get.return_value = _mock_response({"quotes": {}})
			with patch("currency_audit.usd_egp_rate_fetcher.frappe.log_error") as mock_log_error:
				self.assertRaises(
					frappe.ValidationError, fetcher._fetch_live_rate, "https://api.example.com", "key123"
				)
		mock_log_error.assert_called_once()


class TestFetchTimeseries(FrappeTestCase):
	def test_calls_the_timeframe_endpoint_not_timeseries(self):
		"""Regression test: this provider's date-range endpoint is named
		"timeframe" — "timeseries" is a plausible-looking but wrong guess that
		404s with invalid_api_function against the real API."""
		with patch("currency_audit.usd_egp_rate_fetcher.requests.get") as mock_get:
			mock_get.return_value = _mock_response({"quotes": {"2024-01-01": {"USDEGP": 47.1}}})
			rates, raw = fetcher._fetch_timeseries("https://api.example.com", "key123", "2024-01-01", "2024-01-01")

		self.assertEqual(rates, {"2024-01-01": 47.1})

		args, kwargs = mock_get.call_args
		self.assertEqual(args[0], "https://api.example.com/timeframe")
		self.assertEqual(kwargs["params"]["access_key"], "key123")
		self.assertEqual(kwargs["params"]["start_date"], "2024-01-01")
		self.assertEqual(kwargs["params"]["end_date"], "2024-01-01")


class TestFetchToday(FrappeTestCase):
	def test_stores_todays_rate_when_configured(self):
		_configure_settings()
		with patch("currency_audit.usd_egp_rate_fetcher._fetch_live_rate", return_value=(47.5, {})):
			fetcher.fetch_today()

		row = frappe.get_all(
			"USD EGP Exchange Rate",
			filters={"date": frappe.utils.nowdate()},
			fields=["rate", "fetched_via"],
		)
		self.assertEqual(len(row), 1)
		self.assertEqual(row[0].fetched_via, "Daily Job")

	def test_does_nothing_when_not_configured(self):
		_configure_settings(host="", api_key="")
		with patch("currency_audit.usd_egp_rate_fetcher._fetch_live_rate") as mock_fetch:
			fetcher.fetch_today()
		mock_fetch.assert_not_called()

	def test_logs_and_does_not_raise_when_the_api_call_fails(self):
		_configure_settings()
		with patch("currency_audit.usd_egp_rate_fetcher._fetch_live_rate", side_effect=Exception("boom")):
			with patch("currency_audit.usd_egp_rate_fetcher.frappe.log_error") as mock_log_error:
				fetcher.fetch_today()  # must not raise
		mock_log_error.assert_called_once()


class TestBackfillLast60Days(FrappeTestCase):
	def test_stores_every_returned_day_once(self):
		_configure_settings()
		rates = {"2024-07-01": 47.0, "2024-07-02": 47.1, "2024-07-03": 47.2}
		with patch("currency_audit.usd_egp_rate_fetcher._fetch_timeseries", return_value=(rates, {})):
			result = fetcher.backfill_last_60_days()

		self.assertEqual(result, {"fetched": 3, "stored": 3})
		for date in rates:
			self.assertTrue(frappe.db.exists("USD EGP Exchange Rate", {"date": date}))

	def test_skips_days_already_recorded(self):
		_configure_settings()
		fetcher.store_rate("2024-07-04", 47.0, fetched_via="Daily Job")

		rates = {"2024-07-04": 99.0, "2024-07-05": 47.1}
		with patch("currency_audit.usd_egp_rate_fetcher._fetch_timeseries", return_value=(rates, {})):
			result = fetcher.backfill_last_60_days()

		self.assertEqual(result, {"fetched": 2, "stored": 1})
		# The pre-existing day's value is untouched by the backfill.
		self.assertEqual(frappe.db.get_value("USD EGP Exchange Rate", {"date": "2024-07-04"}, "rate"), 47.0)

	def test_throws_when_not_configured(self):
		_configure_settings(host="", api_key="")
		self.assertRaises(frappe.ValidationError, fetcher.backfill_last_60_days)
