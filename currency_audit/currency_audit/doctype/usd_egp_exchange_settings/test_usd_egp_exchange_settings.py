# Copyright (c) 2026, Omar Sabry and Contributors
# See license.txt

import frappe
from frappe.tests.utils import FrappeTestCase


class TestUSDEGPExchangeSettings(FrappeTestCase):
	def _set_host(self, host):
		settings = frappe.get_single("USD EGP Exchange Settings")
		settings.host = host
		settings.api_key = "test-key"
		settings.flags.ignore_mandatory = True
		return settings

	def test_rejects_host_with_a_known_endpoint_segment(self):
		settings = self._set_host("https://api.exchangerate.host/convert")
		self.assertRaises(frappe.ValidationError, settings.save)

	def test_rejects_host_ending_in_timeframe(self):
		settings = self._set_host("https://api.exchangerate.host/timeframe")
		self.assertRaises(frappe.ValidationError, settings.save)

	def test_accepts_a_bare_host(self):
		settings = self._set_host("https://api.exchangerate.host")
		settings.save(ignore_permissions=True)
		self.assertEqual(frappe.get_single("USD EGP Exchange Settings").host, "https://api.exchangerate.host")
