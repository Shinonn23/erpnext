"""Migration helpers for installing Thailand localization as ERPNext-owned data."""

import frappe


LEGACY_MODULES = ("Thai Tax", "Thai Billing", "Thai Deposit", "Petty Cash")
RETIRED_DUPLICATE_DOCTYPES = ("Withholding Tax Type", "Withholding Tax Type Account")


def transfer_thailand_module_ownership():
	"""Move localized DocTypes and module-linked records under ERPNext's Regional module.

	Frappe deletes every DocType and module-linked record owned by an app when that
	app is uninstalled. Run this idempotently while the Thailand app is still
	installed so uninstalling it cannot remove the migrated implementation.
	"""
	from erpnext.regional.thailand.install import make_custom_fields, make_property_setters

	make_custom_fields()
	frappe.db.sql(
		"""update `tabItem`
		set custom_thai_fda_applicability = 'ยังไม่ประเมิน'
		where ifnull(custom_thai_fda_applicability, '') = ''"""
	)
	make_property_setters()
	remove_duplicate_withholding_fields()

	if not frappe.db.exists("Module Def", "Regional"):
		frappe.get_doc(
			{"doctype": "Module Def", "module_name": "Regional", "app_name": "erpnext"}
		).insert(ignore_permissions=True)

	legacy_modules = [
		name
		for name in LEGACY_MODULES
		if frappe.db.exists("Module Def", name)
	]
	if not legacy_modules:
		return

	# Doctype definitions are themselves module-owned; move their metadata first.
	frappe.db.set_value(
		"DocType",
		{
			"module": ("in", legacy_modules),
			"name": ("not in", RETIRED_DUPLICATE_DOCTYPES),
		},
		"module",
		"Regional",
		update_modified=False,
	)

	# Frappe's uninstall routine deletes every record whose Module Def link points
	# to a module being removed (reports, workspaces, print formats, etc.).
	from frappe.installer import _get_module_linked_doctype_field_map

	for doctype, fieldname in _get_module_linked_doctype_field_map().items():
		if not frappe.db.exists("DocType", doctype):
			continue
		frappe.db.set_value(
			doctype,
			{fieldname: ("in", legacy_modules)},
			fieldname,
			"Regional",
			update_modified=False,
		)

	for module_name in legacy_modules:
		if frappe.db.exists("DocType", {"module": module_name}):
			continue
		if any(
			frappe.db.exists(doctype, {fieldname: module_name})
			for doctype, fieldname in _get_module_linked_doctype_field_map().items()
			if frappe.db.exists("DocType", doctype)
		):
			continue
		frappe.delete_doc("Module Def", module_name, ignore_permissions=True, force=True)

	frappe.clear_cache()


def remove_duplicate_withholding_fields():
	"""Remove the retired Thailand-only WHT fields now superseded by ERPNext TWC."""
	legacy_fields = {
		"Item": (
			"withholding_tax_type",
			"withholding_tax_type_pay_supplier",
			"withholding_tax_type_pay_individual",
			"section_break_6buh1",
			"column_break_lhwzh",
		),
		"Sales Invoice Item": ("withholding_tax_type",),
		"Purchase Invoice Item": ("withholding_tax_type",),
		"Payment Entry Deduction": (
			"withholding_tax_type",
			"withholding_tax_base",
			"section_break_s4fwa",
			"column_break_lx8hk",
		),
	}
	for doctype, fieldnames in legacy_fields.items():
		for fieldname in fieldnames:
			custom_field = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": fieldname})
			if custom_field:
				frappe.delete_doc("Custom Field", custom_field, ignore_permissions=True, force=True)
