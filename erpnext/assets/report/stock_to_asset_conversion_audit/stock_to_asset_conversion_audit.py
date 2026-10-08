import frappe

from erpnext.assets.report.material_request_report_utils import demo_request_where


def execute(filters=None):
	where, values = demo_request_where(filters, alias="r", date_field=None)
	conditions = ["stac.docstatus = 1", "stac.material_request = r.name", where]
	if (filters or {}).get("from_date"):
		conditions.append("stac.posting_date >= %(from_date)s")
		values["from_date"] = filters["from_date"]
	if (filters or {}).get("to_date"):
		conditions.append("stac.posting_date <= %(to_date)s")
		values["to_date"] = filters["to_date"]
	if (filters or {}).get("conversion"):
		conditions.append("stac.name = %(conversion)s")
		values["conversion"] = filters["conversion"]
	if (filters or {}).get("accounting_treatment"):
		conditions.append("output.accounting_treatment = %(accounting_treatment)s")
		values["accounting_treatment"] = filters["accounting_treatment"]
	columns = [
		{"label":"Conversion","fieldname":"conversion","fieldtype":"Link","options":"Stock to Asset Conversion","width":165},
		{"label":"Posting Date","fieldname":"posting_date","fieldtype":"Date","width":100},
		{"label":"Request","fieldname":"request","fieldtype":"Link","options":"Material Request","width":165},
		{"label":"Customer","fieldname":"customer","fieldtype":"Link","options":"Customer","width":155},
		{"label":"Stock Item","fieldname":"stock_item","fieldtype":"Link","options":"Item","width":140},
		{"label":"Asset Item","fieldname":"asset_item_code","fieldtype":"Link","options":"Item","width":140},
		{"label":"Source Warehouse","fieldname":"warehouse","fieldtype":"Link","options":"Warehouse","width":140},
		{"label":"Source SN","fieldname":"source_serial_no","fieldtype":"Link","options":"Serial No","width":115},
		{"label":"Source Batch","fieldname":"source_batch_no","fieldtype":"Link","options":"Batch","width":115},
		{"label":"Created Asset","fieldname":"asset","fieldtype":"Link","options":"Asset","width":145},
		{"label":"Asset SN","fieldname":"asset_serial_number","fieldtype":"Data","width":110},
		{"label":"Asset Value","fieldname":"asset_value","fieldtype":"Currency","options":"Company:company:default_currency","width":105},
		{"label":"Accounting Treatment","fieldname":"accounting_treatment","fieldtype":"Data","width":135},
		{"label":"Depreciation","fieldname":"calculate_depreciation","fieldtype":"Check","width":95},
		{"label":"Asset Location","fieldname":"asset_location","fieldtype":"Link","options":"Location","width":135},
		{"label":"Company","fieldname":"company","fieldtype":"Link","options":"Company","width":135},
	]
	data = frappe.db.sql(f"""
		SELECT stac.name AS conversion, stac.posting_date, r.name AS request, r.customer,
			input.item_code AS stock_item, output.asset_item_code, input.warehouse,
			output.source_serial_no, output.source_batch_no, output.asset,
			output.asset_serial_number, output.asset_value, output.accounting_treatment,
			output.calculate_depreciation, output.asset_location, stac.company
		FROM `tabStock to Asset Conversion` stac
		JOIN `tabMaterial Request` r ON r.name = stac.material_request
		JOIN `tabStock to Asset Conversion Item` input ON input.parent = stac.name
		JOIN `tabStock to Asset Conversion Asset` output ON output.parent = stac.name AND output.source_item_row = input.name
		WHERE {' AND '.join(conditions)}
		ORDER BY stac.posting_date DESC, stac.name DESC, output.idx
	""", values, as_dict=True)
	return columns, data
