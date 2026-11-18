"""Every item of every submitted Borrowing, one row per Batch / Serial No (Siwat, 18 Nov 2026): which Item, from
which Borrowing and Material Request, who borrowed it and who answers for it, and how much is still out. Unlike
Borrowing Stock Balance it keeps the loans not delivered yet and the ones already returned, and it shows Asset loans
with the Asset's serial number."""

import frappe
from frappe import _

from erpnext.borrowing.report.requester import (
	REQUEST_JOIN,
	REQUESTER_SELECT,
	add_requester_filter,
	insert_columns_after,
	requester_columns,
)

FILTER_FIELDS = ("company", "loan_type", "borrower_type", "borrower", "status")


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["loan.docstatus = 1"]
	values = {}
	for field in FILTER_FIELDS:
		if filters.get(field):
			conditions.append(f"loan.{field} = %({field})s")
			values[field] = filters[field]
	for field in ("item_code", "batch_no"):
		if filters.get(field):
			conditions.append(f"item.{field} = %({field})s")
			values[field] = filters[field]
	for bound, operator in (("from_date", ">="), ("to_date", "<=")):
		if filters.get(bound):
			conditions.append(f"DATE(loan.creation) {operator} %({bound})s")
			values[bound] = filters[bound]
	if filters.get("only_outstanding"):
		conditions.append("item.outstanding_qty > 0")
	add_requester_filter(filters, conditions, values)

	rows = frappe.db.sql(
		f"""SELECT loan.name AS loan, loan.material_request, loan.loan_type, DATE(loan.creation) AS posting_date,
			loan.expected_return_date, loan.borrower_type, loan.borrower, {REQUESTER_SELECT},
			item.name AS loan_item, item.item_code, item.item_name, item.source_warehouse,
			item.asset, asset.asset_serial_number, item.batch_no, item.serial_no,
			item.qty, item.issued_qty, item.returned_qty, item.adjusted_qty, item.sold_qty, item.outstanding_qty,
			loan.status, loan.company
		FROM `tabBorrowing` loan
		JOIN `tabBorrowing Item` item ON item.parent = loan.name
		LEFT JOIN `tabAsset` asset ON asset.name = item.asset
		{REQUEST_JOIN}
		WHERE {' AND '.join(conditions)}
		ORDER BY loan.creation DESC, loan.name, item.idx""",
		values,
		as_dict=True,
	)
	data = _expand(rows)
	if filters.get("serial_no"):
		data = [row for row in data if row.serial_no == filters.serial_no]
	_add_borrower_names(data)
	return _columns(), data


def _expand(rows):
	"""One row per Batch / Serial No: from the loan's Serial / Batch Tracking once it is delivered, else from the row's
	own Batch and Serial Nos (a loan still being picked)."""
	tracking = {}
	loans = sorted({row.loan for row in rows})
	if loans:
		for entry in frappe.get_all(
			"Borrowing Serial Batch",
			filters={"parent": ["in", loans], "parenttype": "Borrowing"},
			fields=["borrowing_item", "batch_no", "serial_no", "issued_qty", "returned_qty", "adjusted_qty", "outstanding_qty"],
			order_by="parent, idx",
		):
			tracking.setdefault(entry.borrowing_item, []).append(entry)
	result = []
	for row in rows:
		entries = tracking.get(row.loan_item)
		if entries:
			for entry in entries:
				result.append(
					frappe._dict(
						row,
						batch_no=entry.batch_no or row.batch_no,
						serial_no=entry.serial_no,
						issued_qty=entry.issued_qty,
						returned_qty=entry.returned_qty,
						adjusted_qty=entry.adjusted_qty,
						outstanding_qty=entry.outstanding_qty,
						qty=entry.issued_qty,
						sold_qty=None,
					)
				)
			continue
		serials = [s.strip() for s in (row.serial_no or "").splitlines() if s.strip()]
		if row.asset:
			serials = []
		if len(serials) > 1:
			for serial in serials:
				result.append(frappe._dict(row, serial_no=serial, qty=1, issued_qty=None, returned_qty=None,
					adjusted_qty=None, sold_qty=None, outstanding_qty=None))
		else:
			result.append(frappe._dict(row, serial_no=serials[0] if serials else None))
	return result


def _add_borrower_names(data):
	names = {}
	for doctype, field in (("Employee", "employee_name"), ("Customer", "customer_name")):
		keys = sorted({row.borrower for row in data if row.borrower_type == doctype and row.borrower})
		if keys:
			names[doctype] = dict(frappe.get_all(doctype, filters={"name": ["in", keys]}, fields=["name", field], as_list=True))
	for row in data:
		row.borrower_name = names.get(row.borrower_type, {}).get(row.borrower)


def _columns():
	columns = [
		{"label": _("Borrowing"), "fieldname": "loan", "fieldtype": "Link", "options": "Borrowing", "width": 130},
		{"label": _("Material Request"), "fieldname": "material_request", "fieldtype": "Link", "options": "Material Request", "width": 130},
		{"label": _("Loan Type"), "fieldname": "loan_type", "fieldtype": "Data", "width": 80},
		{"label": _("Created On"), "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": _("Expected Return"), "fieldname": "expected_return_date", "fieldtype": "Date", "width": 110},
		{"label": _("Borrower Type"), "fieldname": "borrower_type", "fieldtype": "Data", "width": 100},
		{"label": _("Borrower"), "fieldname": "borrower", "fieldtype": "Data", "width": 100},
		{"label": _("Borrower Name"), "fieldname": "borrower_name", "fieldtype": "Data", "width": 170},
		{"label": _("Item"), "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 130},
		{"label": _("Item Name"), "fieldname": "item_name", "fieldtype": "Data", "width": 170},
		{"label": _("Batch"), "fieldname": "batch_no", "fieldtype": "Link", "options": "Batch", "width": 110},
		{"label": _("Serial No"), "fieldname": "serial_no", "fieldtype": "Link", "options": "Serial No", "width": 150},
		{"label": _("Asset"), "fieldname": "asset", "fieldtype": "Link", "options": "Asset", "width": 120},
		{"label": _("Asset Serial No"), "fieldname": "asset_serial_number", "fieldtype": "Data", "width": 130},
		{"label": _("Source Warehouse"), "fieldname": "source_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": _("Qty"), "fieldname": "qty", "fieldtype": "Float", "width": 70},
		{"label": _("Issued"), "fieldname": "issued_qty", "fieldtype": "Float", "width": 70},
		{"label": _("Returned"), "fieldname": "returned_qty", "fieldtype": "Float", "width": 80},
		{"label": _("Written Off"), "fieldname": "adjusted_qty", "fieldtype": "Float", "width": 85},
		{"label": _("Sold"), "fieldname": "sold_qty", "fieldtype": "Float", "width": 70},
		{"label": _("Outstanding"), "fieldname": "outstanding_qty", "fieldtype": "Float", "width": 90},
		{"label": _("Status"), "fieldname": "status", "fieldtype": "Data", "width": 120},
		{"label": _("Company"), "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 130},
	]
	return insert_columns_after(columns, "borrower_name", requester_columns())
