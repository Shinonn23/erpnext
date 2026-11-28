"""Inventory and warehouse queries for the Lost location type."""

import frappe

from erpnext.stock.doctype.storage_location.storage_location import LOST


def counted_warehouses(lost_warehouse):
	"""Recover the physical warehouse of historical stock-count transfers into a legacy Lost warehouse."""
	if not lost_warehouse or not frappe.db.has_column("Stock Entry", "stock_count"):
		return []
	return frappe.db.sql(
		"""select distinct detail.s_warehouse
		from `tabStock Entry Detail` detail
		join `tabStock Entry` entry on entry.name = detail.parent
		where detail.t_warehouse = %(warehouse)s and detail.s_warehouse != %(warehouse)s
		  and entry.docstatus = 1 and coalesce(entry.stock_count, '') != ''
		order by detail.s_warehouse""",
		{"warehouse": lost_warehouse},
		pluck=True,
	)


def lost_warehouse_names():
	"""Warehouses with a Lost zone, with historical Lost warehouses still supported."""
	return sorted(
		set(
			frappe.get_all(
				"Storage Location", filters={"location_type": LOST, "disabled": 0}, pluck="warehouse"
			)
		)
		| set(frappe.get_all("Warehouse", filters={"warehouse_type": LOST, "disabled": 0}, pluck="name"))
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def lost_warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	from frappe.desk.search import search_widget

	filters = frappe.parse_json(filters) or {}
	required = {
		"name": ["in", lost_warehouse_names() or [""]],
		"is_group": 0,
		"disabled": 0,
	}
	if filters.get("company"):
		required["company"] = filters["company"]
	return search_widget(
		"Warehouse", txt, searchfield=searchfield, start=start, page_length=page_len, filters=required
	)


def lost_stock_rows(warehouses=None, company=None, *, holding_type=LOST):
	"""What the Lost warehouses hold now, one dict for each Item and warehouse with stock: ``warehouse``, ``item_code``,
	``qty``, ``value``, ``since`` (the date of the last receipt into the warehouse), ``days_held``, ``counted_warehouse``
	(the warehouse the Lost warehouse belongs to) and ``stock_count`` (the Stock Count whose transfer put it there, when
	the last receipt was one). ``warehouses`` limits the list; the caller decides who may see it."""
	from frappe.utils import date_diff, nowdate

	conditions = ["b.actual_qty > 0"]
	values = {"type": holding_type}
	if warehouses is not None:
		if not warehouses:
			return []
		conditions.append("b.warehouse in %(warehouses)s")
		values["warehouses"] = tuple(warehouses)
	if company:
		conditions.append("w.company = %(company)s")
		values["company"] = company
	last = (
		"select {column} from `tabStock Ledger Entry` sle where sle.warehouse = b.warehouse"
		" and sle.item_code = b.item_code and sle.actual_qty > 0 and sle.is_cancelled = 0"
		" and (b.storage_location is null or sle.storage_location = b.storage_location)"
		" order by sle.posting_datetime desc, sle.creation desc limit 1"
	)
	rows = frappe.db.sql(
		f"""select b.warehouse, b.storage_location, b.item_code, b.actual_qty as qty, b.stock_value as value,
			({last.format(column="sle.posting_date")}) as since,
			({last.format(column="sle.voucher_type")}) as voucher_type,
			({last.format(column="sle.voucher_no")}) as voucher_no
		from (
			select bin.warehouse, null as storage_location, bin.item_code, bin.actual_qty, bin.stock_value
			from `tabBin` bin join `tabWarehouse` legacy on legacy.name = bin.warehouse
			where legacy.warehouse_type = %(type)s
			union all
			select sle.warehouse, sle.storage_location, sle.item_code,
				sum(sle.actual_qty), sum(sle.stock_value_difference)
			from `tabStock Ledger Entry` sle
			join `tabStorage Location` sl on sl.name = sle.storage_location and sl.warehouse = sle.warehouse
			where sle.is_cancelled = 0 and sl.location_type = %(type)s
			group by sle.warehouse, sle.storage_location, sle.item_code
		) b join `tabWarehouse` w on w.name = b.warehouse
		where {" and ".join(conditions)}
		order by b.warehouse, b.item_code""",
		values,
		as_dict=True,
	)
	today = nowdate()
	for row in rows:
		row["days_held"] = date_diff(today, row.since) if row.since else 0
		counted = [row.warehouse] if row.get("storage_location") else counted_warehouses(row.warehouse)
		row["counted_warehouse"] = counted[0] if counted else None
		row["stock_count"] = (
			frappe.db.get_value("Stock Entry", row.voucher_no, "stock_count")
			if row.voucher_type == "Stock Entry" and frappe.db.has_column("Stock Entry", "stock_count")
			else None
		)
	return rows
