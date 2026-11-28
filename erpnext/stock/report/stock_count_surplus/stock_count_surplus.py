"""Approved count observations; repeated counts are separate evidence, not additive stock."""

import frappe
from frappe import _

from erpnext.stock.report_policy import allowed_warehouses


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = {"docstatus": 1}
	for field in ("company", "warehouse", "count_type"):
		if filters.get(field):
			conditions[field] = filters[field]
	if filters.get("from_date"):
		conditions["snapshot_datetime"] = (">=", filters.from_date + " 00:00:00")
	if filters.get("to_date"):
		conditions["snapshot_datetime"] = (
			"between",
			[filters.get("from_date", "1900-01-01") + " 00:00:00", filters.to_date + " 23:59:59"],
		)
	counts = frappe.get_list(
		"Stock Count",
		filters=conditions,
		fields=["name", "company", "warehouse", "count_type", "snapshot_datetime", "stock_reconciliation"],
		limit_page_length=0,
	)
	scope = allowed_warehouses()
	counts = {row.name: row for row in counts if scope is None or row.warehouse in scope}
	rows = (
		frappe.get_all(
			"Stock Count Item",
			filters={"parenttype": "Stock Count", "parent": ("in", list(counts)), "difference_qty": (">", 0)},
			fields=[
				"parent",
				"item_code",
				"item_name",
				"batch_no",
				"serial_no",
				"storage_location",
				"system_qty",
				"counted_qty",
				"difference_qty",
			],
			order_by="parent, idx",
		)
		if counts
		else []
	)
	for row in rows:
		row.update(counts[row.parent])
		row["stock_count"] = row.parent
		row["stock_uom"] = frappe.get_cached_value("Item", row.item_code, "stock_uom")
	columns = [
		{"fieldname": field, "label": _(label), "fieldtype": kind, "options": options, "width": width}
		for field, label, kind, options, width in (
			("stock_count", "Stock Count", "Link", "Stock Count", 160),
			("snapshot_datetime", "Count Date", "Datetime", None, 155),
			("count_type", "Count Type", "Data", None, 100),
			("company", "Company", "Link", "Company", 160),
			("warehouse", "Warehouse", "Link", "Warehouse", 160),
			("storage_location", "Storage Location", "Link", "Storage Location", 180),
			("item_code", "Item", "Link", "Item", 150),
			("item_name", "Item Name", "Data", None, 180),
			("batch_no", "Batch", "Data", None, 130),
			("serial_no", "Serial No", "Data", None, 150),
			("stock_uom", "Stock UOM", "Link", "UOM", 100),
			("system_qty", "System Qty", "Float", None, 100),
			("counted_qty", "Counted Qty", "Float", None, 110),
			("difference_qty", "Surplus Qty", "Float", None, 110),
			("stock_reconciliation", "Stock Reconciliation", "Link", "Stock Reconciliation", 170),
		)
	]
	return (
		columns,
		rows,
		_(
			"Each row is an approved count observation. Repeated counts may describe the same physical stock; do not add them together. The count never receives a surplus: accounting or the Stock Manager decides it by hand."
		),
	)
