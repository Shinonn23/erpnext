# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder import Case
from frappe.query_builder.functions import Coalesce

from erpnext.stock.report.storage_location_utils import add_location_column


def execute(filters=None):
	columns = [
		{"label": _(label), "fieldname": field, "fieldtype": kind, "options": options, "width": 150}
		for label, field, kind, options in (
			("Material Request", "material_request", "Link", "Material Request"),
			("Date", "date", "Date", None),
			("Item Code", "item_code", "Link", "Item"),
			("Qty", "qty", "Float", None),
			("Transferred Qty", "transferred_qty", "Float", None),
			("Qty to Transfer", "qty_to_transfer", "Float", None),
			("Item Name", "item_name", "Data", None),
			("Description", "description", "Data", None),
			("Company", "company", "Link", "Company"),
			("Warehouse", "warehouse", "Link", "Warehouse"),
		)
	]
	request, item = frappe.qb.DocType("Material Request"), frappe.qb.DocType("Material Request Item")
	query = (
		frappe.qb.from_(request)
		.inner_join(item)
		.on(item.parent == request.name)
		.select(
			request.name.as_("material_request"),
			request.transaction_date.as_("date"),
			item.item_code,
			item.qty,
			item.ordered_qty.as_("transferred_qty"),
			(item.qty - Coalesce(item.ordered_qty, 0)).as_("qty_to_transfer"),
			item.item_name,
			item.description,
			request.company,
			Case()
			.when(item.from_warehouse.notnull() & (item.from_warehouse != ""), item.from_warehouse)
			.else_(item.warehouse)
			.as_("warehouse"),
			Case()
			.when(item.from_warehouse.notnull() & (item.from_warehouse != ""), item.from_storage_location)
			.else_(item.storage_location)
			.as_("storage_location"),
		)
		.where(
			(request.material_request_type.isin(["Material Transfer", "Material Issue"]))
			& (request.docstatus == 1)
			& (request.status != "Stopped")
			& (Coalesce(item.ordered_qty, 0) < Coalesce(item.qty, 0))
		)
		.orderby(request.transaction_date)
	)
	return add_location_column(columns, filters), query.run(as_dict=True)
