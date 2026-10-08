# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document


class StorageLocation(Document):
	def validate(self):
		warehouse = frappe.get_cached_value("Warehouse", self.warehouse, ["is_group", "disabled"], as_dict=True)
		if not warehouse or warehouse.is_group or warehouse.disabled:
			frappe.throw(_("Storage Location requires an active, non-group Warehouse."))
		if not self.is_new() and self.has_value_changed("warehouse"):
			if frappe.db.has_column("Stock Ledger Entry", "storage_location") and frappe.db.exists(
				"Stock Ledger Entry", {"storage_location": self.name}
			):
				frappe.throw(_("Cannot change Warehouse after stock transactions exist for this Storage Location."))


def setup_storage_location_dimension():
	"""Provision the built-in dimension on installation and migration, preserving user settings."""
	if not frappe.db.exists("DocType", "Storage Location"):
		return
	remove_legacy_storage_location_custom_field()
	if frappe.db.exists("Inventory Dimension", "Storage Location"):
		dimension = frappe.get_doc("Inventory Dimension", "Storage Location")
		if dimension.reference_document != "Storage Location" or dimension.source_fieldname != "storage_location" or dimension.target_fieldname != "storage_location":
			frappe.throw(_("The existing Storage Location Inventory Dimension conflicts with the built-in dimension."))
		dimension.add_custom_fields()
		return
	frappe.get_doc({
		"doctype": "Inventory Dimension",
		"dimension_name": "Storage Location",
		"reference_document": "Storage Location",
		"apply_to_all_doctypes": 1,
		"reqd": 0,
		"validate_negative_stock": 1,
	}).insert(ignore_permissions=True)


def remove_legacy_storage_location_custom_field():
	"""Remove the old generated field now that Stock Count Item owns it natively."""
	doctype = "Stock Count Item"
	fieldname = "storage_location"
	native_field = frappe.db.get_value(
		"DocField", {"parent": doctype, "fieldname": fieldname}, ["fieldtype", "options"], as_dict=True
	)
	custom_field = frappe.db.get_value(
		"Custom Field", {"dt": doctype, "fieldname": fieldname}, ["name", "fieldtype", "options"], as_dict=True
	)
	if (
		native_field
		and custom_field
		and native_field.fieldtype == custom_field.fieldtype
		and native_field.options == custom_field.options
	):
		# Delete only the duplicate metadata; the database column now belongs to DocType.
		frappe.db.delete("Custom Field", custom_field.name)
		frappe.clear_cache(doctype=doctype)


def validate_storage_location(ledger_entry):
	location_name = ledger_entry.get("storage_location")
	if not location_name:
		return
	location = frappe.get_cached_value("Storage Location", location_name, ["warehouse", "disabled"], as_dict=True)
	if not location or location.warehouse != ledger_entry.warehouse:
		frappe.throw(_("Storage Location {0} does not belong to Warehouse {1}.").format(location_name, ledger_entry.warehouse))
	if location.disabled and not ledger_entry.get("is_cancelled"):
		frappe.throw(_("Storage Location {0} is disabled.").format(location_name))
