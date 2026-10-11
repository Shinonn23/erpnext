# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


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
	remove_loan_inventory_dimension_custom_fields()
	if frappe.db.exists("Inventory Dimension", "Storage Location"):
		dimension = frappe.get_doc("Inventory Dimension", "Storage Location")
		if (
			dimension.reference_document != "Storage Location"
			or dimension.source_fieldname != "storage_location"
			or dimension.target_fieldname != "storage_location"
		):
			frappe.throw(_("The existing Storage Location Inventory Dimension conflicts with the built-in dimension."))
		dimension.add_custom_fields()
		return
	dimension = frappe.get_doc(
		{
			"doctype": "Inventory Dimension",
			"dimension_name": "Storage Location",
			"reference_document": "Storage Location",
			"apply_to_all_doctypes": 1,
			"reqd": 0,
			"validate_negative_stock": 1,
		}
	)
	dimension.flags.setup_storage_location_dimension = True
	dimension.insert(ignore_permissions=True)


def remove_legacy_storage_location_custom_field():
	"""Remove generated fields now owned by native DocType schemas, retaining their columns."""
	doctypes = list(dict.fromkeys([*frappe.get_hooks("inventory_dimension_doctypes"), "Stock Ledger Entry"]))
	fieldnames = {
		"storage_location",
		"to_storage_location",
		"from_storage_location",
		"rejected_storage_location",
	}
	for doctype in doctypes:
		native_fields = frappe.get_all(
			"DocField",
			filters={"parent": doctype},
			fields=["fieldname", "fieldtype", "options"],
		)
		for native_field in native_fields:
			if native_field.fieldname not in fieldnames:
				continue
			custom_field = frappe.db.get_value(
				"Custom Field",
				{"dt": doctype, "fieldname": native_field.fieldname},
				["name", "fieldtype", "options"],
				as_dict=True,
			)
			if (
				custom_field
				and native_field.fieldtype == custom_field.fieldtype
				and native_field.options == custom_field.options
			):
				# Delete only duplicate metadata; the existing database column now belongs to DocType.
				frappe.db.delete("Custom Field", custom_field.name)
				frappe.clear_cache(doctype=doctype)

	legacy_anchors = {
		"inventory_dimension_storage_location": "Section Break",
		"inventory_dimension_col_break_storage_location": "Column Break",
	}
	for custom_field in frappe.get_all(
		"Custom Field",
		filters={"fieldname": ["in", list(legacy_anchors)]},
		fields=["name", "dt", "fieldname", "fieldtype"],
	):
		if custom_field.fieldtype != legacy_anchors[custom_field.fieldname]:
			continue
		frappe.delete_doc("Custom Field", custom_field.name, ignore_permissions=True)
		frappe.clear_cache(doctype=custom_field.dt)

	# Manufacturing tracks Storage Location through its source/WIP/target/scrap fields. Remove
	# the old generic Inventory Dimension field from Job Card while keeping its database column.
	for custom_field in frappe.get_all(
		"Custom Field",
		filters={
			"dt": "Job Card",
			"fieldname": "storage_location",
			"fieldtype": "Link",
			"options": "Storage Location",
			"insert_after": "inventory_dimension",
		},
		fields=["name"],
	):
		frappe.db.delete("Custom Field", custom_field.name)
		frappe.clear_cache(doctype="Job Card")


def remove_loan_inventory_dimension_custom_fields():
	"""Remove generated Inventory Dimension fields from loan item rows, preserving their columns."""
	loan_item_doctypes = [
		"Custody Loan Adjustment Item",
		"Custody Loan Item",
		"Custody Loan Return Item",
	]
	dimensions = frappe.get_all(
		"Inventory Dimension",
		fields=["dimension_name", "reference_document", "source_fieldname"],
	)

	for doctype in loan_item_doctypes:
		for dimension in dimensions:
			for fieldname, fieldtype in (
				(f"inventory_dimension_{dimension.source_fieldname}", "Section Break"),
				(f"inventory_dimension_col_break_{dimension.source_fieldname}", "Column Break"),
			):
				custom_field = frappe.db.get_value(
					"Custom Field",
					{"dt": doctype, "fieldname": fieldname, "fieldtype": fieldtype},
					"name",
				)
				if custom_field:
					frappe.db.delete("Custom Field", custom_field)
					frappe.clear_cache(doctype=doctype)

			custom_field = frappe.db.get_value(
				"Custom Field",
				{
					"dt": doctype,
					"fieldname": dimension.source_fieldname,
					"fieldtype": "Link",
					"options": dimension.reference_document,
				},
				["name", "insert_after"],
				as_dict=True,
			)
			if not custom_field or custom_field.insert_after not in (
				"inventory_dimension",
				f"inventory_dimension_{dimension.source_fieldname}",
			):
				continue

			# Keep the database column intact in case historical rows contain a dimension value.
			frappe.db.delete("Custom Field", custom_field.name)
			frappe.clear_cache(doctype=doctype)


def set_default_storage_locations(doc, method=None):
	"""Persist warehouse defaults before stock valuation and serial/batch processing."""
	# Historical submissions and cancellations must retain their original dimensions.
	if doc.docstatus == 2 or (not doc.is_new() and doc.get_doc_before_save()
		and doc.get_doc_before_save().docstatus == 1):
		return
	if doc.doctype in ("Sales Invoice", "Purchase Invoice", "POS Invoice") and not doc.get("update_stock"):
		return

	for table in doc.meta.get_table_fields():
		for row in doc.get(table.fieldname) or []:
			warehouses = {
				"storage_location": row.get("s_warehouse") if doc.doctype == "Stock Entry" else row.get("warehouse"),
				"to_storage_location": row.get("t_warehouse") or row.get("target_warehouse") or row.get("to_warehouse"),
				"from_storage_location": row.get("from_warehouse"),
				"rejected_storage_location": row.get("rejected_warehouse"),
			}
			if row.doctype == "Subcontracting Receipt Supplied Item":
				warehouses["storage_location"] = doc.get("supplier_warehouse")
			for fieldname, warehouse in warehouses.items():
				if not warehouse or not row.meta.has_field(fieldname) or row.get(fieldname):
					continue
				location = frappe.get_cached_value("Warehouse", warehouse, "default_storage_location")
				if location:
					validate_storage_location_warehouse(location, warehouse)
					row.set(fieldname, location)


def validate_storage_location(ledger_entry):
	location_name = ledger_entry.get("storage_location")
	if not location_name:
		if flt(ledger_entry.get("actual_qty")) < 0 and not ledger_entry.get("is_cancelled"):
			frappe.throw(
				_("Storage Location is required to issue Item {0} from Warehouse {1}.").format(
					ledger_entry.get("item_code"), ledger_entry.get("warehouse")
				),
				title=_("Storage Location Required"),
			)
		return
	location = frappe.get_cached_value("Storage Location", location_name, ["warehouse", "disabled"], as_dict=True)
	if not location or location.warehouse != ledger_entry.warehouse:
		frappe.throw(_("Storage Location {0} does not belong to Warehouse {1}.").format(location_name, ledger_entry.warehouse))
	if location.disabled and not ledger_entry.get("is_cancelled"):
		frappe.throw(_("Storage Location {0} is disabled.").format(location_name))


def validate_storage_location_warehouse(storage_location, warehouse):
	"""Validate a selected Storage Location against its warehouse before it reaches a stock ledger."""
	if not storage_location:
		return

	location = frappe.get_cached_value(
		"Storage Location", storage_location, ["warehouse", "disabled"], as_dict=True
	)
	if not location:
		frappe.throw(_("Storage Location {0} does not exist.").format(storage_location))
	if not warehouse or location.warehouse != warehouse:
		frappe.throw(
			_("Storage Location {0} does not belong to Warehouse {1}.").format(
				storage_location, warehouse or _("(not set)")
			)
		)
	if location.disabled:
		frappe.throw(_("Storage Location {0} is disabled.").format(storage_location))
