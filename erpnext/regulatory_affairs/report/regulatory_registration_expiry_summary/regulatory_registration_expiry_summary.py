# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe import _
from frappe.utils import getdate, today

from erpnext.regulatory_affairs import reporting
from erpnext.regulatory_affairs.constants import PRODUCT_REGISTRATION


def execute(filters=None):
	"""One row per registration, earliest expiry first, with the stock and open orders it puts at risk."""
	filters = reporting.clean_filters(filters)
	today_date = getdate(today())
	registrations, items_by_registration, measures = reporting.load_registration_impact(
		reporting.registration_query_filters(filters),
		reporting.int_filter(filters, "expiry_within_days"),
		today_date,
	)
	data = reporting.build_expiry_summary_rows(registrations, items_by_registration, measures, today_date)
	return get_columns(), data


def get_columns():
	column = reporting.column
	return [
		column("registration", _("Registration"), "Link", 180, PRODUCT_REGISTRATION),
		column("certificate_type", _("Certificate Type"), "Data", 130),
		column("product_category", _("Product Category"), "Data", 130),
		column("device_category", _("Device Category"), "Data", 100),
		column("product_family_name", _("Product Family"), "Data", 180),
		column("official_status", _("Status"), "Data", 100),
		column("expiry_basis", _("Expiry Basis"), "Data", 150),
		column("expiry_date", _("Expiry Date"), "Date", 100),
		column("days_remaining", _("Days Remaining"), "Int", 100),
		column("renewal_status", _("Renewal Status"), "Data", 120),
		column("responsible_user", _("Responsible User"), "Link", 150, "User"),
		column("affected_items", _("Affected Items"), "Int", 100),
		column("stock_qty", _("Current Stock Qty"), "Float", 120),
		column("stock_value", _("Stock Value"), "Currency", 120),
		column("open_po_qty", _("Open PO Qty"), "Float", 100),
		column("open_so_qty", _("Open SO Qty"), "Float", 100),
	]
