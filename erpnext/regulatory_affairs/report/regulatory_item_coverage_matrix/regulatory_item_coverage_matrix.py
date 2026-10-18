# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe import _
from frappe.utils import cint, getdate, today

from erpnext.regulatory_affairs import reporting
from erpnext.regulatory_affairs.constants import PRODUCT_REGISTRATION


def execute(filters=None):
	"""One row per product line with the Item's current stock and whether the line covers it today."""
	filters = reporting.clean_filters(filters)
	today_date = getdate(today())
	registrations = reporting.get_registrations(reporting.registration_query_filters(filters))
	registrations_by_name = {registration.name: registration for registration in registrations}
	lines = reporting.fetch_registered_lines(
		list(registrations_by_name),
		reporting.normalise_line_status(filters.get("line_status")),
		filters.get("item"),
	)
	item_codes = {line.item_code for line in lines if line.item_code}
	data = reporting.build_coverage_matrix_rows(
		lines,
		registrations_by_name,
		reporting.fetch_item_names(item_codes),
		reporting.fetch_stock_by_item(item_codes),
		today_date,
		bool(cint(filters.get("only_with_stock"))),
	)
	return get_columns(), data


def get_columns():
	column = reporting.column
	return [
		column("item_code", _("Item"), "Link", 140, "Item"),
		column("item_name", _("Item Name"), "Data", 180),
		column("newcode", _("NEWCODE"), "Data", 180),
		column("identifier", _("Identifier"), "Data", 150),
		column("registration", _("Registration"), "Link", 180, PRODUCT_REGISTRATION),
		column("official_status", _("Registration Status"), "Data", 120),
		column("line_status", _("Line Status"), "Data", 100),
		column("expiry_date", _("Registration Expiry"), "Date", 110),
		column("effective_from", _("Effective From"), "Date", 100),
		column("effective_to", _("Effective To"), "Date", 100),
		column("in_force", _("In Force Today"), "Check", 90),
		column("stock_qty", _("Current Stock Qty"), "Float", 120),
	]
