import frappe


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = [
		"loan.docstatus = 1",
		"loan.loan_type = 'Asset'",
		"loan.status IN ('Active', 'Partially Returned')",
		"item.outstanding_qty > 0",
	]
	values = {}
	for field in ("company", "borrower_type", "borrower"):
		if filters.get(field):
			conditions.append(f"loan.{field} = %({field})s")
			values[field] = filters[field]
	for bound, operator in (("from_date", ">="), ("to_date", "<=")):
		if filters.get(bound):
			conditions.append(f"loan.expected_return_date {operator} %({bound})s")
			values[bound] = filters[bound]
	if filters.get("overdue_only"):
		conditions.append("loan.expected_return_date < CURDATE()")

	columns = [
		{"label": "Custody Loan", "fieldname": "loan", "fieldtype": "Link", "options": "Custody Loan", "width": 165},
		{"label": "Material Request", "fieldname": "material_request", "fieldtype": "Link", "options": "Material Request", "width": 165},
		{"label": "Borrower Type", "fieldname": "borrower_type", "fieldtype": "Data", "width": 110},
		{"label": "Borrower", "fieldname": "borrower", "fieldtype": "Dynamic Link", "options": "borrower_type", "width": 170},
		{"label": "Expected Return", "fieldname": "expected_return_date", "fieldtype": "Date", "width": 115},
		{"label": "Asset", "fieldname": "asset", "fieldtype": "Link", "options": "Asset", "width": 155},
		{"label": "Asset Name", "fieldname": "asset_name", "fieldtype": "Data", "width": 170},
		{"label": "Item", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": "Serial No", "fieldname": "asset_serial_number", "fieldtype": "Data", "width": 120},
		{"label": "Current Location", "fieldname": "current_location", "fieldtype": "Link", "options": "Location", "width": 145},
		{"label": "Custodian", "fieldname": "custodian", "fieldtype": "Link", "options": "Employee", "width": 140},
		{"label": "Outstanding", "fieldname": "outstanding_qty", "fieldtype": "Float", "width": 95},
		{"label": "Status", "fieldname": "status", "fieldtype": "Data", "width": 125},
		{"label": "Asset Movement", "fieldname": "asset_movement", "fieldtype": "Link", "options": "Asset Movement", "width": 155},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 130},
	]
	data = frappe.db.sql(
		f"""SELECT loan.name AS loan, loan.material_request, loan.borrower_type,
			loan.borrower, loan.expected_return_date, item.asset, asset.asset_name,
			item.item_code, asset.asset_serial_number, asset.location AS current_location,
			asset.custodian, item.outstanding_qty, loan.status, loan.asset_movement, loan.company
		FROM `tabCustody Loan` loan
		JOIN `tabCustody Loan Item` item ON item.parent = loan.name
		JOIN `tabAsset` asset ON asset.name = item.asset
		WHERE {' AND '.join(conditions)}
		ORDER BY loan.expected_return_date, loan.borrower, item.item_code, item.asset""",
		values,
		as_dict=True,
	)
	return columns, data
