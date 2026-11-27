# Copyright (c) 2026, Omar Sabry and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document

# Path segments the fetcher appends itself (see usd_cny_rate_fetcher.py). If a
# user pastes a full endpoint URL into `host` instead of the bare host — an easy
# mistake, since ERPNext's own built-in "Currency Exchange Settings" doctype has
# a similarly-named field that *does* want a full endpoint URL — the fetcher
# would silently double up the path (e.g. ".../convert/timeframe") and 404.
_KNOWN_ENDPOINT_SEGMENTS = ("live", "timeframe", "timeseries", "convert", "historical")


class USDCNYExchangeSettings(Document):
	"""Single doctype holding the external rate API's host and key
	(currency_audit.usd_cny_rate_fetcher reads this). api_key is a Password field,
	encrypted at rest by Frappe."""

	def validate(self):
		"""Reject a `host` that already includes one of the fetcher's own endpoint
		segments (e.g. pasting ERPNext's built-in Currency Exchange Settings'
		"https://api.exchangerate.host/convert" here) — that would silently
		double up the path and 404 on every fetch instead of failing clearly here.
		"""
		if not self.host:
			return

		last_segment = self.host.rstrip("/").rsplit("/", 1)[-1].lower()
		if last_segment in _KNOWN_ENDPOINT_SEGMENTS:
			frappe.throw(
				_(
					"API Host should be the bare base URL (e.g. https://api.exchangerate.host), "
					"not a full endpoint. It looks like it currently ends in \"/{0}\" — remove that "
					"part. This app appends /live, /convert, and /timeframe itself as needed."
				).format(last_segment)
			)
