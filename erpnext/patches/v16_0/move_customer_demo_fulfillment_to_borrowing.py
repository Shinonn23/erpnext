import frappe


def execute():
	# A standard report edited locally can be newer than its source file and skip fixture sync.
	name = "Customer Demo Fulfillment"
	if frappe.db.exists("Report", {"name": name, "module": "Assets", "is_standard": "Yes"}):
		frappe.db.set_value("Report", name, "module", "Borrowing", update_modified=False)
