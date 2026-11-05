import frappe

from erpnext.loan.report.requester import (
	REQUEST_JOIN,
	REQUESTER_SELECT,
	UNIT_VALUE,
	add_requester_filter,
	expand_serial_batches,
	insert_columns_after,
	requester_columns,
	value_columns,
)


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = [
		"loan.docstatus = 1",
		"loan.loan_type = 'Stock'",
		# items on a return not yet received are still the requester's responsibility
		"loan.status IN ('Active', 'Partially Returned', 'Pending Quality Inspection', 'Pending Receipt')",
		"item.outstanding_qty > 0",
	]
	values = {}
	for field in ("company", "borrower_type", "borrower", "status"):
		if filters.get(field):
			conditions.append(f"loan.{field} = %({field})s")
			values[field] = filters[field]
	for bound, operator in (("from_date", ">="), ("to_date", "<=")):
		if filters.get(bound):
			conditions.append(f"loan.expected_return_date {operator} %({bound})s")
			values[bound] = filters[bound]
	add_requester_filter(filters, conditions, values)

	columns = [
		{"label": "Custody Loan", "fieldname": "loan", "fieldtype": "Link", "options": "Custody Loan", "width": 165},
		{"label": "Material Request", "fieldname": "material_request", "fieldtype": "Link", "options": "Material Request", "width": 165},
		{"label": "Borrower Type", "fieldname": "borrower_type", "fieldtype": "Data", "width": 110},
		{"label": "Borrower", "fieldname": "borrower", "fieldtype": "Dynamic Link", "options": "borrower_type", "width": 170},
		{"label": "Expected Return", "fieldname": "expected_return_date", "fieldtype": "Date", "width": 115},
		{"label": "Item", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": "Item Name", "fieldname": "item_name", "fieldtype": "Data", "width": 170},
		{"label": "Source Warehouse", "fieldname": "source_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 145},
		{"label": "Loan Warehouse", "fieldname": "customer_loan_warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 145},
		{"label": "Batch", "fieldname": "batch_no", "fieldtype": "Link", "options": "Batch", "width": 110},
		{"label": "Serial Nos", "fieldname": "serial_no", "fieldtype": "Small Text", "width": 160},
		{"label": "Issued", "fieldname": "qty", "fieldtype": "Float", "width": 85},
		{"label": "Returned", "fieldname": "returned_qty", "fieldtype": "Float", "width": 85},
		{"label": "Written Off", "fieldname": "adjusted_qty", "fieldtype": "Float", "width": 90},
		{"label": "Outstanding", "fieldname": "outstanding_qty", "fieldtype": "Float", "width": 95},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 125},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 130},
	]
	columns = insert_columns_after(columns, "borrower", requester_columns())
	columns = insert_columns_after(columns, "outstanding_qty", value_columns())
	for column in columns:
		if column["fieldname"] == "serial_no":
			column.update(label="Serial No", fieldtype="Link", options="Serial No")
	data = frappe.db.sql(
		f"""SELECT loan.name AS loan, loan.material_request, loan.borrower_type,
			loan.borrower, {REQUESTER_SELECT}, loan.expected_return_date, item.name AS loan_item, item.item_code, item.item_name,
			item.source_warehouse, loan.customer_loan_warehouse, item.batch_no, item.serial_no,
			item.qty, item.returned_qty, item.adjusted_qty,
			item.outstanding_qty, {UNIT_VALUE} AS unit_value,
			item.qty * {UNIT_VALUE} AS loan_value, item.outstanding_qty * {UNIT_VALUE} AS outstanding_value, loan.status, loan.company
		FROM `tabCustody Loan` loan
		JOIN `tabCustody Loan Item` item ON item.parent = loan.name
		{REQUEST_JOIN}
		WHERE {' AND '.join(conditions)}
		ORDER BY loan.expected_return_date, loan.borrower, item.item_code""",
		values,
		as_dict=True,
	)
	return columns, expand_serial_batches(data)
