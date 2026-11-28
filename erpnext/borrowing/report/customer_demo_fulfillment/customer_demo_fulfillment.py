import frappe

from erpnext.assets.report.material_request_report_utils import demo_request_where


def execute(filters=None):
	where, values = demo_request_where(filters)
	where += " AND ri.item_code LIKE %(item_code)s" if (filters or {}).get("item_code") else ""
	if (filters or {}).get("item_code"):
		values["item_code"] = f"%{filters['item_code']}%"
	columns = [
		{"label":"Request","fieldname":"request","fieldtype":"Link","options":"Material Request","width":170},
		{"label":"Type","fieldname":"material_request_type","fieldtype":"Data","width":135},
		{"label":"Customer","fieldname":"customer","fieldtype":"Link","options":"Customer","width":160},
		{"label":"Customer Name","fieldname":"customer_name","fieldtype":"Data","width":175},
		{"label":"Customer Contact","fieldname":"customer_contact","fieldtype":"Link","options":"Contact","width":150},
		{"label":"Contact Name","fieldname":"contact_name","fieldtype":"Data","width":160},
		{"label":"Request Date","fieldname":"transaction_date","fieldtype":"Date","width":105},
		{"label":"Expected Return","fieldname":"expected_return_date","fieldtype":"Date","width":110},
		{"label":"Item","fieldname":"item_code","fieldtype":"Link","options":"Item","width":145},
		{"label":"Item Name","fieldname":"item_name","fieldtype":"Data","width":175},
		{"label":"Item Type","fieldname":"item_type","fieldtype":"Data","width":100},
		{"label":"Source Warehouse","fieldname":"from_warehouse","fieldtype":"Link","options":"Warehouse","width":145},
		{"label":"UOM","fieldname":"uom","fieldtype":"Link","options":"UOM","width":70},
		{"label":"Requested","fieldname":"qty","fieldtype":"Float","width":90},
		{"label":"Qty to Fulfill","fieldname":"unfulfilled_qty","fieldtype":"Float","width":100},
		{"label":"Fulfilled","fieldname":"fulfilled_qty","fieldtype":"Float","width":90},
		{"label":"Returned","fieldname":"returned_qty","fieldtype":"Float","width":90},
		{"label":"Qty With Customer","fieldname":"outstanding_qty","fieldtype":"Float","width":95},
		{"label":"Item Status","fieldname":"item_status","fieldtype":"Data","width":125},
		{"label":"Stock Entry","fieldname":"stock_entry","fieldtype":"Link","options":"Stock Entry","width":150},
		{"label":"Asset Movement","fieldname":"asset_movement","fieldtype":"Link","options":"Asset Movement","width":150},
	]
	data = frappe.db.sql(f"""
		SELECT r.name AS request, r.material_request_type, r.customer, c.customer_name,
			r.contact_person AS customer_contact, ct.full_name AS contact_name,
			r.transaction_date, r.expected_return_date, ri.item_code, i.item_name,
			ri.item_type, ri.from_warehouse, ri.uom, ri.qty,
			GREATEST(ri.qty - ri.fulfilled_qty, 0) AS unfulfilled_qty, ri.fulfilled_qty,
			ri.returned_qty, ri.outstanding_qty, ri.status AS item_status,
			ri.stock_entry, ri.asset_movement
		FROM `tabMaterial Request` r
		JOIN `tabMaterial Request Item` ri ON ri.parent = r.name
		LEFT JOIN `tabCustomer` c ON c.name = r.customer
		LEFT JOIN `tabContact` ct ON ct.name = r.contact_person
		LEFT JOIN `tabItem` i ON i.name = ri.item_code
		WHERE {where}
		ORDER BY r.transaction_date DESC, r.name DESC, ri.idx
	""", values, as_dict=True)
	return columns, data
