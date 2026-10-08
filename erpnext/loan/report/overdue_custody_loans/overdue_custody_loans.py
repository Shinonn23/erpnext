import frappe


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = [
		"loan.docstatus = 1",
		"loan.status IN ('Active', 'Partially Returned')",
		"loan.expected_return_date < CURDATE()",
		"item.outstanding_qty > 0",
	]
	values = {}
	for field in ("company", "loan_type", "borrower_type", "borrower"):
		if filters.get(field):
			conditions.append(f"loan.{field} = %({field})s")
			values[field] = filters[field]

	columns = [
		{"label": "Custody Loan", "fieldname": "loan", "fieldtype": "Link", "options": "Custody Loan", "width": 165},
		{"label": "Material Request", "fieldname": "material_request", "fieldtype": "Link", "options": "Material Request", "width": 165},
		{"label": "Loan Type", "fieldname": "loan_type", "fieldtype": "Data", "width": 90},
		{"label": "Borrower Type", "fieldname": "borrower_type", "fieldtype": "Data", "width": 110},
		{"label": "Borrower", "fieldname": "borrower", "fieldtype": "Dynamic Link", "options": "borrower_type", "width": 170},
		{"label": "Expected Return", "fieldname": "expected_return_date", "fieldtype": "Date", "width": 115},
		{"label": "Overdue Days", "fieldname": "overdue_days", "fieldtype": "Int", "width": 95},
		{"label": "Item", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": "Item Name", "fieldname": "item_name", "fieldtype": "Data", "width": 170},
		{"label": "Asset", "fieldname": "asset", "fieldtype": "Link", "options": "Asset", "width": 145},
		{"label": "Batch", "fieldname": "batch_no", "fieldtype": "Link", "options": "Batch", "width": 105},
		{"label": "Serial Nos", "fieldname": "serial_no", "fieldtype": "Small Text", "width": 140},
		{"label": "Outstanding", "fieldname": "outstanding_qty", "fieldtype": "Float", "width": 100},
		{"label": "Loan Status", "fieldname": "status", "fieldtype": "Data", "width": 125},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 130},
	]
	data = frappe.db.sql(
		f"""SELECT loan.name AS loan, loan.material_request, loan.loan_type,
			loan.borrower_type, loan.borrower, loan.expected_return_date,
			DATEDIFF(CURDATE(), loan.expected_return_date) AS overdue_days,
			item.item_code, item.item_name, item.asset, item.batch_no, item.serial_no,
			item.outstanding_qty, loan.status, loan.company
		FROM `tabCustody Loan` loan
		JOIN `tabCustody Loan Item` item ON item.parent = loan.name
		WHERE {' AND '.join(conditions)}
		ORDER BY loan.expected_return_date, loan.borrower, item.item_code""",
		values,
		as_dict=True,
	)
	return columns, data
