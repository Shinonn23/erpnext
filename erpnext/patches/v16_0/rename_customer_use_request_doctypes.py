import frappe


def execute():
	doctype_renames = (
		("Customer Demo Loan Request", "Customer Use Request"),
		("Customer Demo Loan Request Item", "Customer Use Request Item"),
	)

	for old_name, new_name in doctype_renames:
		if frappe.db.exists("DocType", old_name):
			if frappe.db.exists("DocType", new_name):
				frappe.throw(
					f"Cannot rename {old_name}: {new_name} already exists. Resolve the duplicate DocTypes before migrating."
				)
			frappe.rename_doc("DocType", old_name, new_name, force=True)
