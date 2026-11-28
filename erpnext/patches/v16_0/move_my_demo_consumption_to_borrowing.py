import frappe


def execute():
	name = "my-demo-consumption"
	if frappe.db.exists("Page", {"name": name, "module": "Connect Diagnostics Setup", "standard": "Yes"}):
		# Preserve Page roles and advance its timestamp so native fixture sync leaves them intact.
		frappe.db.set_value("Page", name, "module", "Borrowing")
