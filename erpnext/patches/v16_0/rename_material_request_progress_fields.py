from frappe.model.utils.rename_field import rename_field


def execute():
	for old_fieldname, new_fieldname in (
		("customer_use_status", "request_progress_status"),
		("customer_use_remarks", "request_remarks"),
	):
		rename_field("Material Request", old_fieldname, new_fieldname)
