import frappe


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["loan.docstatus = 1"]
	values = {}
	for field in ("company", "loan_type", "borrower_type", "borrower", "status"):
		if filters.get(field):
			conditions.append(f"loan.{field} = %({field})s")
			values[field] = filters[field]
	if filters.get("from_date"):
		conditions.append("loan.expected_return_date >= %(from_date)s")
		values["from_date"] = filters.from_date
	if filters.get("to_date"):
		conditions.append("loan.expected_return_date <= %(to_date)s")
		values["to_date"] = filters.to_date

	columns = [
		{"label": "Custody Loan", "fieldname": "loan", "fieldtype": "Link", "options": "Custody Loan", "width": 165},
		{"label": "Material Request", "fieldname": "material_request", "fieldtype": "Link", "options": "Material Request", "width": 165},
		{"label": "Loan Type", "fieldname": "loan_type", "fieldtype": "Data", "width": 90},
		{"label": "Borrower Type", "fieldname": "borrower_type", "fieldtype": "Data", "width": 110},
		{"label": "Borrower", "fieldname": "borrower", "fieldtype": "Dynamic Link", "options": "borrower_type", "width": 170},
		{"label": "Expected Return", "fieldname": "expected_return_date", "fieldtype": "Date", "width": 115},
		{"label": "Items", "fieldname": "item_count", "fieldtype": "Int", "width": 65},
		{"label": "Issued Qty", "fieldname": "issued_qty", "fieldtype": "Float", "width": 90},
		{"label": "Returned Qty", "fieldname": "returned_qty", "fieldtype": "Float", "width": 95},
		{"label": "Written Off", "fieldname": "adjusted_qty", "fieldtype": "Float", "width": 90},
		{"label": "Outstanding", "fieldname": "outstanding_qty", "fieldtype": "Float", "width": 95},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 130},
		{"label": "Shipment", "fieldname": "shipment", "fieldtype": "Link", "options": "Shipment", "width": 145},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 130},
	]
	data = frappe.db.sql(
		f"""SELECT loan.name AS loan, loan.material_request, loan.loan_type,
			loan.borrower_type, loan.borrower, loan.expected_return_date,
			COUNT(item.name) AS item_count, SUM(item.qty) AS issued_qty,
			SUM(item.returned_qty) AS returned_qty, SUM(item.adjusted_qty) AS adjusted_qty,
			SUM(item.outstanding_qty) AS outstanding_qty, loan.status,
			COALESCE(stock.shipment, movement.shipment) AS shipment, loan.company
		FROM `tabCustody Loan` loan
		LEFT JOIN `tabCustody Loan Item` item ON item.parent = loan.name
		LEFT JOIN `tabStock Entry` stock ON stock.name = loan.stock_entry
		LEFT JOIN `tabAsset Movement` movement ON movement.name = loan.asset_movement
		WHERE {' AND '.join(conditions)}
		GROUP BY loan.name
		ORDER BY loan.expected_return_date, loan.name""",
		values,
		as_dict=True,
	)
	return columns, data
