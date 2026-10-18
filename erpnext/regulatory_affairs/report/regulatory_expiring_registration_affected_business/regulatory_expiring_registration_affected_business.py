# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

from frappe import _
from frappe.utils import getdate, today

from erpnext.regulatory_affairs import reporting
from erpnext.regulatory_affairs.constants import ACTIVE, EXPIRED, PRODUCT_REGISTRATION

DEFAULT_STATUSES = (ACTIVE, EXPIRED)


def execute(filters=None):
	"""One row per (registration, Item) with that Item's own stock and open orders.

	Without filters the report lists Active and Expired registrations expiring within 365 days.
	"""
	filters = reporting.affected_business_filters(reporting.clean_filters(filters))
	today_date = getdate(today())
	registrations, items_by_registration, measures = reporting.load_registration_impact(
		reporting.registration_query_filters(filters, DEFAULT_STATUSES),
		reporting.int_filter(filters, "expiry_within_days"),
		today_date,
		filters.get("item"),
	)
	item_codes = {code for items in items_by_registration.values() for code in items}
	data = reporting.build_affected_business_rows(
		registrations, items_by_registration, reporting.fetch_item_names(item_codes), measures, today_date
	)
	return get_columns(), data


def get_columns():
	column = reporting.column
	return [
		column("registration", _("Registration"), "Link", 180, PRODUCT_REGISTRATION),
		column("official_status", _("Status"), "Data", 100),
		column("item_code", _("Item"), "Link", 140, "Item"),
		column("item_name", _("Item Name"), "Data", 180),
		column("newcode", _("NEWCODE"), "Data", 180),
		column("expiry_date", _("Expiry Date"), "Date", 100),
		column("days_remaining", _("Days Remaining"), "Int", 100),
		column("stock_qty", _("Current Stock Qty"), "Float", 120),
		column("stock_value", _("Stock Value"), "Currency", 120),
		column("open_po_qty", _("Open PO Qty"), "Float", 100),
		column("open_so_qty", _("Open SO Qty"), "Float", 100),
		column("responsible_user", _("Owner"), "Link", 150, "User"),
		column("renewal_status", _("Renewal Status"), "Data", 120),
	]
