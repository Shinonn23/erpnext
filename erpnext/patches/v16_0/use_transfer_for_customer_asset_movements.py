import frappe


def execute():
	frappe.db.sql(
		"""UPDATE `tabAsset Movement` SET purpose = 'Transfer'
		WHERE reference_doctype = 'Customer Use Request'
		AND IFNULL(reference_name, '') != '' AND purpose = 'Issue'"""
	)
