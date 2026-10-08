import frappe


def execute(filters=None):
	filters = frappe._dict(filters or {})
	values = {}
	asset_conditions = ["loan.docstatus = 1", "movement.docstatus = 1", "movement.is_custody_loan_transaction = 1"]
	stock_conditions = ["loan.docstatus = 1", "entry.docstatus = 1", "entry.is_custody_loan_transaction = 1"]
	for field in ("company", "loan_type"):
		if filters.get(field):
			asset_conditions.append(f"loan.{field} = %({field})s")
			stock_conditions.append(f"loan.{field} = %({field})s")
			values[field] = filters[field]
	for bound, operator in (("from_date", ">="), ("to_date", "<=")):
		if filters.get(bound):
			asset_conditions.append(f"movement.transaction_date {operator} %({bound})s")
			stock_conditions.append(f"entry.posting_date {operator} %({bound})s")
			values[bound] = filters[bound]

	asset_rows = frappe.db.sql(
		f"""SELECT movement.transaction_date, loan.name AS loan,
			loan.material_request, loan.loan_type, loan.borrower_type, loan.borrower,
			'Asset Movement' AS source_doctype, movement.name AS source_document,
			CASE WHEN movement.custody_loan_source_doctype = 'Custody Loan'
				THEN 'Issue' ELSE 'Return' END AS action,
			item.asset_item_code AS item_code, asset.asset_name AS item_name,
			item.asset, asset.asset_serial_number, asset.source_serial_no,
			asset.source_batch_no AS batch_no, 1 AS qty,
			item.source_location AS from_location, item.target_location AS to_location,
			item.from_employee, item.to_employee, movement.company
		FROM `tabAsset Movement` movement
		JOIN `tabCustody Loan` loan ON loan.name = movement.custody_loan
		JOIN `tabAsset Movement Item` item ON item.parent = movement.name
		JOIN `tabAsset` asset ON asset.name = item.asset
		WHERE {' AND '.join(asset_conditions)}""",
		values,
		as_dict=True,
	)
	stock_rows = frappe.db.sql(
		f"""SELECT entry.posting_date AS transaction_date, loan.name AS loan,
			loan.material_request, loan.loan_type, loan.borrower_type, loan.borrower,
			'Stock Entry' AS source_doctype, entry.name AS source_document,
			CASE entry.custody_loan_source_doctype
				WHEN 'Custody Loan' THEN 'Issue'
				WHEN 'Custody Loan Return' THEN 'Return'
				ELSE 'Adjustment' END AS action,
			item.item_code, product.item_name, NULL AS asset, NULL AS asset_serial_number,
			item.serial_no AS source_serial_no, item.batch_no, item.qty,
			item.s_warehouse AS from_location, item.t_warehouse AS to_location,
			NULL AS from_employee, NULL AS to_employee, entry.company
		FROM `tabStock Entry` entry
		JOIN `tabCustody Loan` loan ON loan.name = entry.custody_loan
		JOIN `tabStock Entry Detail` item ON item.parent = entry.name
		JOIN `tabItem` product ON product.name = item.item_code
		WHERE {' AND '.join(stock_conditions)}""",
		values,
		as_dict=True,
	)
	data = asset_rows + stock_rows
	data.sort(key=lambda row: (str(row.transaction_date or ""), row.source_document or ""), reverse=True)
	columns = [
		{"label": "Date", "fieldname": "transaction_date", "fieldtype": "Datetime", "width": 145},
		{"label": "Custody Loan", "fieldname": "loan", "fieldtype": "Link", "options": "Custody Loan", "width": 165},
		{"label": "Material Request", "fieldname": "material_request", "fieldtype": "Link", "options": "Material Request", "width": 165},
		{"label": "Type", "fieldname": "loan_type", "fieldtype": "Data", "width": 80},
		{"label": "Borrower Type", "fieldname": "borrower_type", "fieldtype": "Data", "width": 110},
		{"label": "Borrower", "fieldname": "borrower", "fieldtype": "Dynamic Link", "options": "borrower_type", "width": 170},
		{"label": "Source Type", "fieldname": "source_doctype", "fieldtype": "Data", "width": 115},
		{"label": "Movement", "fieldname": "source_document", "fieldtype": "Dynamic Link", "options": "source_doctype", "width": 155},
		{"label": "Action", "fieldname": "action", "fieldtype": "Data", "width": 90},
		{"label": "Item", "fieldname": "item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": "Item / Asset Name", "fieldname": "item_name", "fieldtype": "Data", "width": 165},
		{"label": "Asset", "fieldname": "asset", "fieldtype": "Link", "options": "Asset", "width": 140},
		{"label": "Serial No", "fieldname": "source_serial_no", "fieldtype": "Data", "width": 135},
		{"label": "Batch", "fieldname": "batch_no", "fieldtype": "Link", "options": "Batch", "width": 105},
		{"label": "Qty", "fieldname": "qty", "fieldtype": "Float", "width": 75},
		{"label": "From", "fieldname": "from_location", "fieldtype": "Data", "width": 145},
		{"label": "To", "fieldname": "to_location", "fieldtype": "Data", "width": 145},
		{"label": "From Employee", "fieldname": "from_employee", "fieldtype": "Link", "options": "Employee", "width": 135},
		{"label": "To Employee", "fieldname": "to_employee", "fieldtype": "Link", "options": "Employee", "width": 135},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 130},
	]
	return columns, data
