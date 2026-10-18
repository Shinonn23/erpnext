# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import getdate, today

from erpnext.regulatory_affairs import reporting
from erpnext.regulatory_affairs.constants import PRODUCT_REGISTRATION, REGISTRATION_ACTION

ACTION_FIELDS = [
	"name",
	"registration",
	"action_type",
	"current_stage",
	"assigned_to",
	"fda_application_number",
	"submitted_date",
	"fda_query_date",
	"response_due_date",
	"new_expiry_date",
	"modified",
]


def execute(filters=None):
	"""Registration Actions (Renewals by default) ordered by the registration expiry they protect."""
	filters = reporting.clean_filters(filters)
	today_date = getdate(today())
	actions = frappe.get_list(
		REGISTRATION_ACTION,
		filters=reporting.workbench_action_filters(filters),
		fields=ACTION_FIELDS,
		limit_page_length=0,
	)
	registrations = reporting.load_workbench_registrations(actions)
	rows = reporting.build_workbench_rows(actions, registrations, today_date, filters.get("product_category"))
	return get_columns(), rows


def get_columns():
	column = reporting.column
	return [
		column("action", _("Action"), "Link", 130, REGISTRATION_ACTION),
		column("registration", _("Registration"), "Link", 180, PRODUCT_REGISTRATION),
		column("product_category", _("Product Category"), "Data", 130),
		column("action_type", _("Action Type"), "Data", 110),
		column("expiry_date", _("Registration Expiry"), "Date", 110),
		column("days_to_expiry", _("Days to Expiry"), "Int", 100),
		column("current_stage", _("Stage"), "Data", 110),
		column("assigned_to", _("Assigned To"), "Link", 140, "User"),
		column("fda_application_number", _("FDA Application No."), "Data", 150),
		column("submitted_date", _("Submitted Date"), "Date", 100),
		column("fda_query_date", _("FDA Query Date"), "Date", 110),
		column("response_due_date", _("Query Due Date"), "Date", 110),
		column("days_to_query_due", _("Days to Query Due"), "Int", 120),
		column("new_expiry_date", _("New Expiry Date"), "Date", 110),
	]
