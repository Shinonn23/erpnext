# Copyright (c) 2025, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _

import erpnext
from erpnext.stock.report.storage_location_utils import add_location_column, location_key


def execute(filters: dict | None = None):
	columns = get_columns()
	data = get_data(filters)
	columns.append({"label": _("Warehouse"), "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 150})
	add_location_column(columns, filters)
	columns.append({"label": _("Allocated Landed Cost"), "fieldname": "allocated_cost", "fieldtype": "Currency", "width": 150})
	data = add_location_allocations(data, filters)

	return columns, data


def get_columns() -> list[dict]:
	return [
		{
			"label": _("Landed Cost Id"),
			"fieldname": "name",
			"fieldtype": "Link",
			"options": "Landed Cost Voucher",
		},
		{
			"label": _("Total Landed Cost"),
			"fieldname": "landed_cost",
			"fieldtype": "Currency",
			"options": "currency",
		},
		{
			"label": _("Currency"),
			"fieldname": "currency",
			"fieldtype": "Link",
			"options": "Currency",
			"hidden": 1,
		},
		{
			"label": _("Purchase Voucher Type"),
			"fieldname": "voucher_type",
			"fieldtype": "Link",
			"options": "DocType",
			"width": 200,
		},
		{
			"label": _("Purchase Voucher No"),
			"fieldname": "voucher_no",
			"fieldtype": "Dynamic Link",
			"options": "voucher_type",
			"width": 220,
		},
		{
			"label": _("Vendor Invoice"),
			"fieldname": "vendor_invoice",
			"fieldtype": "Link",
			"options": "Purchase Invoice",
			"width": 200,
		},
	]


def get_data(filters) -> list[list]:
	company_currency = erpnext.get_company_currency(filters.get("company"))
	landed_cost_vouchers = get_landed_cost_vouchers(filters) or {}
	landed_vouchers = list(landed_cost_vouchers.keys())
	vendor_invoices = {}
	if landed_vouchers:
		vendor_invoices = get_vendor_invoices(landed_vouchers)

	data = []

	for name, vouchers in landed_cost_vouchers.items():
		res = {
			"name": name,
		}

		last_index = 0
		vendor_invoice_list = vendor_invoices.get(name, [])
		for i, d in enumerate(vouchers):
			if i == 0:
				res.update(
					{
						"landed_cost": d.landed_cost,
						"voucher_type": d.voucher_type,
						"voucher_no": d.voucher_no,
						"currency": company_currency,
					}
				)
			else:
				res = {
					"voucher_type": d.voucher_type,
					"voucher_no": d.voucher_no,
				}

			if len(vendor_invoice_list) > i:
				res["vendor_invoice"] = vendor_invoice_list[i]

			data.append(res)
			last_index = i

		if vendor_invoice_list and len(vendor_invoice_list) > len(vouchers):
			for row in vendor_invoice_list[last_index + 1 :]:
				data.append({"vendor_invoice": row})

	return data


def get_landed_cost_vouchers(filters):
	lcv = frappe.qb.DocType("Landed Cost Voucher")
	lcv_voucher = frappe.qb.DocType("Landed Cost Purchase Receipt")

	query = (
		frappe.qb.from_(lcv)
		.inner_join(lcv_voucher)
		.on(lcv.name == lcv_voucher.parent)
		.select(
			lcv.name,
			lcv.total_taxes_and_charges.as_("landed_cost"),
			lcv_voucher.receipt_document_type.as_("voucher_type"),
			lcv_voucher.receipt_document.as_("voucher_no"),
		)
		.where((lcv.docstatus == 1) & (lcv.company == filters.company))
	)

	if filters.from_date and filters.to_date:
		query = query.where(lcv.posting_date.between(filters.from_date, filters.to_date))

	if filters.raw_material_voucher_type:
		query = query.where(lcv_voucher.receipt_document_type == filters.raw_material_voucher_type)

	if filters.raw_material_voucher_no:
		query = query.where(lcv_voucher.receipt_document == filters.raw_material_voucher_no)

	data = query.run(as_dict=True) or []
	result = {}
	for row in data:
		result.setdefault((row.name), []).append(row)

	return result


def get_vendor_invoices(landed_vouchers):
	doctype = frappe.qb.DocType("Landed Cost Vendor Invoice")

	query = (
		frappe.qb.from_(doctype)
		.select(
			doctype.parent,
			doctype.vendor_invoice,
		)
		.where((doctype.docstatus == 1) & (doctype.parent.isin(landed_vouchers)))
		.orderby(
			doctype.idx,
		)
	)

	data = query.run(as_dict=True) or []

	result = {}
	for row in data:
		result.setdefault(row.parent, []).append(row.vendor_invoice)

	return result


def add_location_allocations(data, filters):
	names = [row["name"] for row in data if row.get("name")]
	if not names:
		return data
	items = frappe.get_all("Landed Cost Item", filters={"parent": ["in", names]},
		fields=["parent", "receipt_document_type", "receipt_document", "purchase_receipt_item", "stock_entry_item", "applicable_charges"])
	groups = {}
	for item in items:
		if filters.get("raw_material_voucher_type") and item.receipt_document_type != filters.raw_material_voucher_type:
			continue
		if filters.get("raw_material_voucher_no") and item.receipt_document != filters.raw_material_voucher_no:
			continue
		warehouse = location = None
		if item.receipt_document_type == "Stock Entry" and item.stock_entry_item:
			warehouse, location = frappe.db.get_value("Stock Entry Detail", item.stock_entry_item, ["t_warehouse", "to_storage_location"]) or (None, None)
		elif item.receipt_document_type in ("Purchase Receipt", "Purchase Invoice", "Subcontracting Receipt") and item.purchase_receipt_item:
			warehouse, location = frappe.db.get_value(item.receipt_document_type + " Item", item.purchase_receipt_item, ["warehouse", "storage_location"]) or (None, None)
		key = (item.parent, warehouse or "", location_key({"storage_location": location}, filters), item.receipt_document_type, item.receipt_document)
		groups[key] = groups.get(key, 0) + item.applicable_charges
	rows = []
	for row in data:
		row = frappe._dict(row)
		row.indent = 0
		rows.append(row)
		if not row.get("name"):
			continue
		for (name, warehouse, location, voucher_type, voucher_no), value in groups.items():
			if name == row.name:
				rows.append(frappe._dict(warehouse=warehouse or None, storage_location=location or None,
					voucher_type=voucher_type, voucher_no=voucher_no, allocated_cost=value, currency=row.currency, indent=1))
	return rows
