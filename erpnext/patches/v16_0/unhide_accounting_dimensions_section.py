import frappe


def execute():
	hidden_setters = frappe.get_all(
		"Property Setter",
		filters={
			"field_name": "accounting_dimensions_section",
			"property": "hidden",
		},
		fields=["name", "doc_type"],
	)

	affected_doctypes = set()
	for ps in hidden_setters:
		frappe.delete_doc("Property Setter", ps.name, ignore_missing=True)
		if ps.doc_type:
			affected_doctypes.add(ps.doc_type)

	for dt in affected_doctypes:
		module = frappe.db.get_value("DocType", dt, "module")
		if module:
			frappe.reload_doc(frappe.scrub(module), "doctype", frappe.scrub(dt), force=True)

	frappe.clear_cache()
