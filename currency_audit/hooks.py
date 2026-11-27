app_name = "currency_audit"
app_title = "Currency Audit"
app_publisher = "Omar Sabry"
app_description = "Currency exchange rate change log and usage audit"
app_email = "omarelgewily2011@gmail.com"
app_license = "mit"

# Apps
# ------------------

# required_apps = []

# Each item in the list will be shown as an app in the apps page
# add_to_apps_screen = [
# 	{
# 		"name": "currency_audit",
# 		"logo": "/assets/currency_audit/logo.png",
# 		"title": "Currency Audit",
# 		"route": "/currency_audit",
# 		"has_permission": "currency_audit.api.permission.has_app_permission"
# 	}
# ]

# Includes in <head>
# ------------------

# include js, css files in header of desk.html
# app_include_css = "/assets/currency_audit/css/currency_audit.css"
# app_include_js = "/assets/currency_audit/js/currency_audit.js"

# include js, css files in header of web template
# web_include_css = "/assets/currency_audit/css/currency_audit.css"
# web_include_js = "/assets/currency_audit/js/currency_audit.js"

# include custom scss in every website theme (without file extension ".scss")
# website_theme_scss = "currency_audit/public/scss/website"

# include js, css files in header of web form
# webform_include_js = {"doctype": "public/js/doctype.js"}
# webform_include_css = {"doctype": "public/css/doctype.css"}

# include js in page
# page_js = {"page" : "public/js/file.js"}

# include js in doctype views
# doctype_js = {"doctype" : "public/js/doctype.js"}
# doctype_list_js = {"doctype" : "public/js/doctype_list.js"}
# doctype_tree_js = {"doctype" : "public/js/doctype_tree.js"}
# doctype_calendar_js = {"doctype" : "public/js/doctype_calendar.js"}

# Svg Icons
# ------------------
# include app icons in desk
# app_include_icons = "currency_audit/public/icons.svg"

# Home Pages
# ----------

# application home page (will override Website Settings)
# home_page = "login"

# website user home page (by Role)
# role_home_page = {
# 	"Role": "home_page"
# }

# Generators
# ----------

# automatically create page for each record of this doctype
# website_generators = ["Web Page"]

# Jinja
# ----------

# add methods and filters to jinja environment
# jinja = {
# 	"methods": "currency_audit.utils.jinja_methods",
# 	"filters": "currency_audit.utils.jinja_filters"
# }

# Installation
# ------------

after_install = "currency_audit.install.after_install"

# Uninstallation
# ------------

# before_uninstall = "currency_audit.uninstall.before_uninstall"
# after_uninstall = "currency_audit.uninstall.after_uninstall"

# Integration Setup
# ------------------
# To set up dependencies/integrations with other apps
# Name of the app being installed is passed as an argument

# before_app_install = "currency_audit.utils.before_app_install"
# after_app_install = "currency_audit.utils.after_app_install"

# Integration Cleanup
# -------------------
# To clean up dependencies/integrations with other apps
# Name of the app being uninstalled is passed as an argument

# before_app_uninstall = "currency_audit.utils.before_app_uninstall"
# after_app_uninstall = "currency_audit.utils.after_app_uninstall"

# Desk Notifications
# ------------------
# See frappe.core.notifications.get_notification_config

# notification_config = "currency_audit.notifications.get_notification_config"

# Permissions
# -----------
# Permissions evaluated in scripted ways

# permission_query_conditions = {
# 	"Event": "frappe.desk.doctype.event.event.get_permission_query_conditions",
# }
#
# has_permission = {
# 	"Event": "frappe.desk.doctype.event.event.has_permission",
# }

# DocType Class
# ---------------
# Override standard doctype classes

# override_doctype_class = {
# 	"ToDo": "custom_app.overrides.CustomToDo"
# }

# Document Events
# ---------------
# Hook on document methods and events

doc_events = {
	"Currency Exchange": {
		"on_update": "currency_audit.events.currency_exchange.on_update",
	},
	"Sales Invoice": {
		"on_submit": "currency_audit.external_rate_detection.check_standard_doctype",
	},
	"Purchase Invoice": {
		"on_submit": "currency_audit.external_rate_detection.check_standard_doctype",
	},
	"POS Invoice": {
		"on_submit": "currency_audit.external_rate_detection.check_standard_doctype",
	},
	"Purchase Receipt": {
		"on_submit": "currency_audit.external_rate_detection.check_standard_doctype",
	},
	"Delivery Note": {
		"on_submit": "currency_audit.external_rate_detection.check_standard_doctype",
	},
	"Payment Entry": {
		"on_submit": "currency_audit.external_rate_detection.check_payment_entry",
	},
	"Journal Entry": {
		"on_submit": "currency_audit.external_rate_detection.check_journal_entry",
	},
}

# Scheduled Tasks
# ---------------

scheduler_events = {
	"cron": {
		"*/15 * * * *": ["currency_audit.matching.retry_stuck_matches"],
		"0 3 * * *": ["currency_audit.reconciliation.reconcile_recent_version_history"],
		"0 9 * * *": ["currency_audit.market_rate_fetcher.fetch_today"],
		"15 9 * * *": ["currency_audit.market_rate_fetcher.retry_if_missing"],
	},
}

# scheduler_events = {
# 	"all": [
# 		"currency_audit.tasks.all"
# 	],
# 	"daily": [
# 		"currency_audit.tasks.daily"
# 	],
# 	"hourly": [
# 		"currency_audit.tasks.hourly"
# 	],
# 	"weekly": [
# 		"currency_audit.tasks.weekly"
# 	],
# 	"monthly": [
# 		"currency_audit.tasks.monthly"
# 	],
# }

# Testing
# -------

# before_tests = "currency_audit.install.before_tests"

# Overriding Methods
# ------------------------------
#
# override_whitelisted_methods = {
# 	"frappe.desk.doctype.event.event.get_events": "currency_audit.event.get_events"
# }
#
# each overriding function accepts a `data` argument;
# generated from the base implementation of the doctype dashboard,
# along with any modifications made in other Frappe apps
# override_doctype_dashboards = {
# 	"Task": "currency_audit.task.get_dashboard_data"
# }

# exempt linked doctypes from being automatically cancelled
#
# auto_cancel_exempted_doctypes = ["Auto Repeat"]

# Ignore links to specified DocTypes when deleting documents
# -----------------------------------------------------------

# PRD Principle 5 ("audit-only, not enforcement"): this app records what happened and
# must never stop anyone from cancelling or deleting a transaction. Without this hook
# it does exactly that — the `usage` child table points at every matched transaction
# with a Dynamic Link (reference_doctype + reference_name), which is enough for
# Frappe's link checker to treat a matched Journal Entry / Sales Invoice / Payment
# Entry / etc. as "still linked" and refuse the operation:
#
#     Cannot delete or cancel because Journal Entry ACC-JV-2026-00747 is linked
#     with Currency Exchange Log CEL-00032 at Row: 1
#
# Both names are listed, and between them they cover both operations
# (see frappe.model.delete_doc):
#
# - "Currency Exchange Log Usage" is the `df.parent` of the Dynamic Link field, so
#   get_dynamic_linked_docs skips it up front, before it even runs the lookup query.
# - "Currency Exchange Log" is the `reference_doctype` that same function reports for
#   a child row (the row's parenttype), checked again after the query — and it's also
#   what the *static* link path (get_linked_docs) matches on, which is what keeps the
#   log's own `currency_exchange` Link field from blocking deletion of a Currency
#   Exchange record.
#
# Cancel is covered as well as delete: for dynamic links this hook is deliberately not
# gated on the method, unlike the static path where it applies to Delete only. That
# asymmetry is fine here — the log has no static Link to any transaction doctype, only
# the dynamic one, so there is nothing left for Cancel to trip over.
#
# A usage row left pointing at a since-deleted document is intentionally not cleaned
# up: the log is an immutable record of what matched at the time (Principle 1), and
# the next matching run for that log rebuilds `usage` wholesale anyway.
ignore_links_on_delete = ["Currency Exchange Log", "Currency Exchange Log Usage"]

# Request Events
# ----------------
# before_request = ["currency_audit.utils.before_request"]
# after_request = ["currency_audit.utils.after_request"]

# Job Events
# ----------
# before_job = ["currency_audit.utils.before_job"]
# after_job = ["currency_audit.utils.after_job"]

# User Data Protection
# --------------------

# user_data_fields = [
# 	{
# 		"doctype": "{doctype_1}",
# 		"filter_by": "{filter_by}",
# 		"redact_fields": ["{field_1}", "{field_2}"],
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_2}",
# 		"filter_by": "{filter_by}",
# 		"partial": 1,
# 	},
# 	{
# 		"doctype": "{doctype_3}",
# 		"strict": False,
# 	},
# 	{
# 		"doctype": "{doctype_4}"
# 	}
# ]

# Authentication and authorization
# --------------------------------

# auth_hooks = [
# 	"currency_audit.auth.validate"
# ]

# Automatically update python controller files with type annotations for this app.
# export_python_type_annotations = True

# default_log_clearing_doctypes = {
# 	"Logging DocType Name": 30  # days to retain logs
# }

# Translation
# ------------
# List of apps whose translatable strings should be excluded from this app's translations.
# ignore_translatable_strings_from = []

