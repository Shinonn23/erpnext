import frappe


def execute(filters=None):
	filters = frappe._dict(filters or {})
	conditions = ["stac.docstatus = 1"]
	values = {}
	for field in ("company", "conversion"):
		if filters.get(field):
			column = "stac.company" if field == "company" else "stac.name"
			conditions.append(f"{column} = %({field})s")
			values[field] = filters[field]
	for field in ("from_date", "to_date"):
		if filters.get(field):
			op = ">=" if field == "from_date" else "<="
			conditions.append(f"stac.posting_date {op} %({field})s")
			values[field] = filters[field]
	if filters.get("accounting_treatment"):
		conditions.append("output.accounting_treatment = %(accounting_treatment)s")
		values["accounting_treatment"] = filters.accounting_treatment
	if filters.get("customer"):
		conditions.append("request.customer = %(customer)s")
		values["customer"] = filters.customer
	if filters.get("request_progress_status"):
		conditions.append("request.request_progress_status = %(request_progress_status)s")
		values["request_progress_status"] = filters.request_progress_status

	columns = [
		{"label": "Conversion", "fieldname": "conversion", "fieldtype": "Link", "options": "Stock to Asset Conversion", "width": 165},
		{"label": "Posting Date", "fieldname": "posting_date", "fieldtype": "Date", "width": 100},
		{"label": "Company", "fieldname": "company", "fieldtype": "Link", "options": "Company", "width": 135},
		{"label": "Request", "fieldname": "request", "fieldtype": "Link", "options": "Material Request", "width": 165},
		{"label": "Customer", "fieldname": "customer", "fieldtype": "Link", "options": "Customer", "width": 155},
		{"label": "Stock Item", "fieldname": "stock_item", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": "Asset Item", "fieldname": "asset_item_code", "fieldtype": "Link", "options": "Item", "width": 140},
		{"label": "Source Warehouse", "fieldname": "warehouse", "fieldtype": "Link", "options": "Warehouse", "width": 140},
		{"label": "Source SN", "fieldname": "source_serial_no", "fieldtype": "Link", "options": "Serial No", "width": 115},
		{"label": "Source Batch", "fieldname": "source_batch_no", "fieldtype": "Link", "options": "Batch", "width": 115},
		{"label": "Created Asset", "fieldname": "asset", "fieldtype": "Link", "options": "Asset", "width": 145},
		{"label": "Asset SN", "fieldname": "asset_serial_number", "fieldtype": "Data", "width": 110},
		{"label": "Asset Value", "fieldname": "asset_value", "fieldtype": "Currency", "options": "Company:company:default_currency", "width": 105},
		{"label": "Accounting Treatment", "fieldname": "accounting_treatment", "fieldtype": "Data", "width": 160},
		{"label": "Depreciation Entry", "fieldname": "journal_entry", "fieldtype": "Link", "options": "Journal Entry", "width": 145},
		{"label": "Asset Location", "fieldname": "asset_location", "fieldtype": "Link", "options": "Location", "width": 135},
	]
	data = frappe.db.sql(
		f"""
		SELECT stac.name AS conversion, stac.posting_date, stac.company,
			request.name AS request, request.customer,
			input.item_code AS stock_item, output.asset_item_code, input.warehouse,
			output.source_serial_no, output.source_batch_no, output.asset,
			output.asset_serial_number, output.asset_value, output.accounting_treatment,
			output.journal_entry, output.asset_location
		FROM `tabStock to Asset Conversion` stac
		JOIN `tabStock to Asset Conversion Item` input ON input.parent = stac.name
		JOIN `tabStock to Asset Conversion Asset` output
			ON output.parent = stac.name AND output.source_item_row = input.name
		LEFT JOIN `tabMaterial Request` request ON request.name = stac.material_request
		WHERE {' AND '.join(conditions)}
		ORDER BY stac.posting_date DESC, stac.name DESC, output.idx
		""",
		values,
		as_dict=True,
	)
	return columns, data
