"""Lost Stock (Siwat, 7 Nov 2026): what the Lost warehouses hold, so accounting can prepare the write-off.

One row for each Item and Lost warehouse with stock: the quantity, the stock value (only for a user who may read stock
values, the privacy layer's rule; the column is left out for the others), the date of the last receipt, the days held, the
counted warehouse the Lost warehouse belongs to (``Warehouse.lost_warehouse``) and the Stock Count whose transfer put the
stock there. The monthly variance by Item and warehouse is this report grouped by the counted warehouse.

A user sees only the Lost warehouses she may read (the Warehouse record rules of the Role Profile: a laboratory sees its
own, because the Lost warehouse takes the laboratory's Operating Department) and, when she has a warehouse scope
(``workflows/warehouse_scope.py``), those inside it.
"""

import frappe
from frappe import _
from frappe.utils import cint

from erpnext.stock.lost_inventory import lost_stock_rows, lost_warehouse_names
from erpnext.stock.report_policy import allowed_warehouses, has_stock_value_access


def get_columns(show_value):
	columns = [
		{
			"fieldname": "warehouse",
			"label": _("Warehouse"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 220,
		},
		{
			"fieldname": "storage_location",
			"label": _("Lost Storage Location"),
			"fieldtype": "Link",
			"options": "Storage Location",
			"width": 200,
		},
		{
			"fieldname": "counted_warehouse",
			"label": _("Counted Warehouse"),
			"fieldtype": "Link",
			"options": "Warehouse",
			"width": 200,
		},
		{"fieldname": "item_code", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 160},
		{"fieldname": "item_name", "label": _("Item Name"), "fieldtype": "Data", "width": 200},
		{"fieldname": "qty", "label": _("Quantity"), "fieldtype": "Float", "width": 100},
	]
	if show_value:
		columns.append(
			{"fieldname": "stock_value", "label": _("Stock Value"), "fieldtype": "Currency", "width": 120}
		)
	columns.extend(
		[
			{"fieldname": "since", "label": _("Last Received"), "fieldtype": "Date", "width": 110},
			{"fieldname": "days_held", "label": _("Days Held"), "fieldtype": "Int", "width": 90},
			{
				"fieldname": "stock_count",
				"label": _("Stock Count"),
				"fieldtype": "Link",
				"options": "Stock Count",
				"width": 150,
			},
		]
	)
	return columns


def readable_lost_warehouses(filters):
	"""The Lost warehouses the user may read (record rules and permissions apply) inside her warehouse scope."""
	names = set(lost_warehouse_names())
	conditions = {"is_group": 0, "name": ["in", sorted(names) or [""]]}
	if filters.get("company"):
		conditions["company"] = filters["company"]
	if filters.get("warehouse"):
		if filters["warehouse"] not in names:
			return []
		conditions["name"] = filters["warehouse"]
	found = frappe.get_list("Warehouse", filters=conditions, pluck="name", limit_page_length=0)
	scope = allowed_warehouses()
	if scope is not None:
		found = [name for name in found if name in set(scope)]
	return found


def get_data(filters, show_value):
	filters = frappe._dict(filters or {})
	warehouses = readable_lost_warehouses(filters)
	rows = lost_stock_rows(warehouses)
	data = []
	for row in rows:
		if filters.get("item_code") and row.item_code != filters.item_code:
			continue
		if cint(filters.get("min_days")) and row.days_held < cint(filters.min_days):
			continue
		data.append(
			{
				"warehouse": row.warehouse,
				"storage_location": row.get("storage_location"),
				"counted_warehouse": row.counted_warehouse,
				"item_code": row.item_code,
				"item_name": frappe.db.get_value("Item", row.item_code, "item_name"),
				"qty": row.qty,
				"stock_value": row.value if show_value else None,
				"since": row.since,
				"days_held": row.days_held,
				"stock_count": row.stock_count,
			}
		)
	return data


def execute(filters=None):
	show_value = has_stock_value_access()
	return get_columns(show_value), get_data(filters, show_value)
