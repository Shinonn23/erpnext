import frappe
from frappe import _
from frappe.utils import flt

from erpnext.assets.doctype.asset_repair.parts import REPAIR_TYPES, get_return_allocations


def execute(filters=None):
	filters = frappe._dict(filters or {})
	if filters.repair_type not in REPAIR_TYPES or not filters.repair:
		frappe.throw(_("Select the repair to view its parts."))
	frappe.get_doc(filters.repair_type, filters.repair).check_permission("read")
	columns = [
		{"fieldname": "request", "label": _("Material Request"), "fieldtype": "Link", "options": "Material Request", "width": 160},
		{"fieldname": "issue", "label": _("Issue"), "fieldtype": "Link", "options": "Stock Entry", "width": 155},
		{"fieldname": "item", "label": _("Item"), "fieldtype": "Link", "options": "Item", "width": 150},
		{"fieldname": "requested", "label": _("Requested"), "fieldtype": "Float", "width": 100},
		{"fieldname": "issued", "label": _("Issued"), "fieldtype": "Float", "width": 100},
		{"fieldname": "returned", "label": _("Returned"), "fieldtype": "Float", "width": 100},
		{"fieldname": "net_used", "label": _("Net Issued"), "fieldtype": "Float", "width": 100},
		{"fieldname": "uom", "label": _("Stock UOM"), "fieldtype": "Link", "options": "UOM", "width": 100},
		{"fieldname": "returns", "label": _("Return Documents"), "fieldtype": "Data", "width": 220},
	]
	result = []
	requests = frappe.get_all("Material Request", filters={"repair_source_type": filters.repair_type,
		"repair_source": filters.repair, "docstatus": ["<", 2]}, pluck="name")
	for name in requests:
		request = frappe.get_doc("Material Request", name)
		request.check_permission("read")
		for requested in request.items:
			result.append({"request": name, "item": requested.item_code,
				"requested": flt(requested.get("stock_qty") or flt(requested.qty) * flt(requested.conversion_factor or 1)),
				"uom": requested.stock_uom})
			details = frappe.get_all("Stock Entry Detail", filters={"material_request_item": requested.name,
				"parenttype": "Stock Entry", "docstatus": 1}, fields=["name", "parent", "item_code", "transfer_qty", "stock_uom"])
			for detail in details:
				issue = frappe.db.get_value("Stock Entry", detail.parent, ["name", "docstatus", "purpose"], as_dict=True)
				if not issue or issue.docstatus != 1 or issue.purpose != "Material Issue":
					continue
				returns, quantity = [], 0
				for return_name in frappe.get_all("Stock Entry", filters={"repair_return_issue": issue.name,
					"docstatus": 1}, pluck="name"):
					matching = frappe.get_all("Stock Entry Detail", filters={"parent": return_name,
						"repair_return_issue_row": detail.name}, fields=["item_code", "repair_return_issue_row",
						"batch_no", "serial_no", "serial_and_batch_bundle", "transfer_qty", "qty", "conversion_factor"])
					if matching:
						returns.append(return_name)
						quantity += sum(flt(row["qty"]) for row in get_return_allocations(matching))
				result.append({"request": name, "issue": issue.name, "item": detail.item_code,
					"issued": flt(detail.transfer_qty), "returned": quantity,
					"net_used": flt(detail.transfer_qty) - quantity, "uom": detail.stock_uom,
					"returns": ", ".join(returns)})
	return columns, result
