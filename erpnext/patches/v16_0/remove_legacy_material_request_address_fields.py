"""Drop the replaced Material Request address fields after their values are migrated."""

import frappe


def execute():
	legacy_fields = [
		field
		for field in (
			"customer_address",
			"customer_address_display",
			"loan_recipient_address",
			"loan_recipient_address_display",
		)
		if frappe.db.has_column("Material Request", field)
	]
	if legacy_fields:
		frappe.model.delete_fields({"Material Request": legacy_fields}, delete=1)
	frappe.clear_cache(doctype="Material Request")
