# Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt


import frappe
from frappe import _
from frappe.query_builder.functions import IfNull
from frappe.utils import flt

from erpnext.stock.report.storage_location_utils import (
	add_location_column,
	location_key,
	show_location_wise_stock,
)


def execute(filters=None):
	columns = get_columns(filters)
	consumed_details = get_consumed_details(filters)
	supplier_details = get_suppliers_details(filters)
	material_transfer_vouchers = get_material_transfer_vouchers()
	data = []

	for item_code, suppliers in supplier_details.items():
		groups = {}
		for entry in consumed_details.get(item_code, []):
			if entry.voucher_no in material_transfer_vouchers:
				continue
			key = (entry.warehouse, location_key(entry, filters))
			row = groups.setdefault(
				key,
				[
					entry.item_code,
					entry.item_name,
					entry.description,
					entry.stock_uom,
					0,
					0,
					0,
					0,
					0,
					0,
					",".join(sorted(set(suppliers))),
					entry.warehouse,
				],
			)
			index = 6 if entry.voucher_type in ["Delivery Note", "Sales Invoice"] else 4
			row[index] += abs(flt(entry.actual_qty))
			row[index + 1] += abs(flt(entry.stock_value_difference))
		for (_warehouse, location), row in groups.items():
			row[8], row[9] = row[4] + row[6], row[5] + row[7]
			if show_location_wise_stock(filters):
				row.append(location or None)
			data.append(row)

	return columns, data


def get_columns(filters):
	"""return columns based on filters"""

	columns = [
		_("Item") + ":Link/Item:100",
		_("Item Name") + "::100",
		_("Description") + "::150",
		_("UOM") + ":Link/UOM:90",
		_("Consumed Qty") + ":Float:110",
		_("Consumed Amount") + ":Currency:130",
		_("Delivered Qty") + ":Float:110",
		_("Delivered Amount") + ":Currency:130",
		_("Total Qty") + ":Float:110",
		_("Total Amount") + ":Currency:130",
		_("Supplier(s)") + "::250",
	]

	columns.append(_("Warehouse") + ":Link/Warehouse:150")
	return add_location_column(columns, filters)


def get_consumed_details(filters):
	item = frappe.qb.DocType("Item")
	sle = frappe.qb.DocType("Stock Ledger Entry")

	query = (
		frappe.qb.from_(sle)
		.from_(item)
		.select(
			sle.item_code,
			sle.warehouse,
			sle.storage_location,
			item.item_name,
			item.description,
			item.stock_uom,
			sle.actual_qty,
			sle.stock_value_difference,
			sle.voucher_no,
			sle.voucher_type,
		)
		.where((sle.is_cancelled == 0) & (sle.item_code == item.name) & (sle.actual_qty < 0))
	)

	if filters.get("from_date") and filters.get("to_date"):
		query = query.where(
			(sle.posting_date >= filters.get("from_date")) & (sle.posting_date <= filters.get("to_date"))
		)

	consumed_details = {}
	for d in query.run(as_dict=True):
		consumed_details.setdefault(d.item_code, []).append(d)

	return consumed_details


def get_suppliers_details(filters):
	item_supplier_map = {}
	supplier = filters.get("supplier")

	item = frappe.qb.DocType("Item")
	pr = frappe.qb.DocType("Purchase Receipt")
	pr_item = frappe.qb.DocType("Purchase Receipt Item")

	query = (
		frappe.qb.from_(pr)
		.from_(pr_item)
		.select(pr.supplier, pr_item.item_code)
		.where(
			(pr.name == pr_item.parent)
			& (pr.docstatus == 1)
			& (
				pr_item.item_code
				== (
					frappe.qb.from_(item)
					.select(item.name)
					.where((item.is_stock_item == 1) & (item.name == pr_item.item_code))
				)
			)
		)
	)

	for d in query.run(as_dict=True):
		item_supplier_map.setdefault(d.item_code, []).append(d.supplier)

	pi = frappe.qb.DocType("Purchase Invoice")
	pi_item = frappe.qb.DocType("Purchase Invoice Item")

	query = (
		frappe.qb.from_(pi)
		.from_(pi_item)
		.select(pi.supplier, pi_item.item_code)
		.where(
			(pi.name == pi_item.parent)
			& (pi.docstatus == 1)
			& (IfNull(pi.update_stock, 0) == 1)
			& (
				pi_item.item_code
				== (
					frappe.qb.from_(item)
					.select(item.name)
					.where((item.is_stock_item == 1) & (item.name == pi_item.item_code))
				)
			)
		)
	)

	for d in query.run(as_dict=True):
		if d.item_code not in item_supplier_map:
			item_supplier_map.setdefault(d.item_code, []).append(d.supplier)

	if supplier:
		invalid_items = []
		for item_code, suppliers in item_supplier_map.items():
			if supplier not in suppliers:
				invalid_items.append(item_code)

		for item_code in invalid_items:
			del item_supplier_map[item_code]

	return item_supplier_map


def get_material_transfer_vouchers():
	se = frappe.qb.DocType("Stock Entry")
	query = (
		frappe.qb.from_(se).select(se.name).where((se.purpose == "Material Transfer") & (se.docstatus == 1))
	)

	return [r[0] for r in query.run()]
