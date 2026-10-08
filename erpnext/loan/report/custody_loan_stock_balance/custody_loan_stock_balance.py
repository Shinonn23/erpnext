import frappe


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = [
		"loan.docstatus = 1",
		"loan.loan_type = 'Stock'",
		"loan.status IN ('Active', 'Partially Returned')",
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
		{"label": "Replacement Received", "fieldname": "replacement_received_qty", "fieldtype": "Float", "width": 125},
		{"label": "Written Off", "fieldname": "adjusted_qty", "fieldtype": "Float", "width": 90},
		{"label": "Outstanding", "fieldname": "outstanding_qty", "fieldtype": "Float", "width": 95},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 125},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 130},
	]
	data = frappe.db.sql(
		f"""SELECT loan.name AS loan, loan.material_request, loan.borrower_type,
			loan.borrower, loan.expected_return_date, item.item_code, item.item_name,
			item.source_warehouse, loan.customer_loan_warehouse, item.batch_no, item.serial_no,
			item.qty, item.returned_qty, item.replacement_received_qty, item.adjusted_qty,
			item.outstanding_qty, loan.status, loan.company
		FROM `tabCustody Loan` loan
		JOIN `tabCustody Loan Item` item ON item.parent = loan.name
		WHERE {' AND '.join(conditions)}
		ORDER BY loan.expected_return_date, loan.borrower, item.item_code""",
		values,
		as_dict=True,
	)
	return columns, data
