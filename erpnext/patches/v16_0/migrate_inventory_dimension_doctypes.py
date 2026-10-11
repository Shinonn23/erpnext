import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext.stock.doctype.inventory_dimension.inventory_dimension import field_exists

RETIRED_DOCTYPES = (
	"Maintenance Visit Purpose",
	"Packing Slip Item",
	"Purchase Receipt Item Supplied",
	"Quality Inspection",
	"Warranty Claim",
)
OLD_STOCK_TO_ASSET_DOCTYPE = "Stock to Asset Conversion Asset"
NEW_STOCK_TO_ASSET_DOCTYPE = "Stock to Asset Conversion Item"


def execute():
	dimensions = frappe.get_all(
		"Inventory Dimension",
		fields=[
			"name",
			"source_fieldname",
			"reference_document",
			"apply_to_all_doctypes",
			"document_type",
		],
	)

	for dimension in dimensions:
		if not dimension.source_fieldname:
			continue

		if not dimension.apply_to_all_doctypes and dimension.document_type == OLD_STOCK_TO_ASSET_DOCTYPE:
			frappe.db.set_value(
				"Inventory Dimension",
				dimension.name,
				"document_type",
				NEW_STOCK_TO_ASSET_DOCTYPE,
				update_modified=False,
			)

		move_stock_to_asset_dimension(dimension)
		for doctype in (*RETIRED_DOCTYPES, OLD_STOCK_TO_ASSET_DOCTYPE):
			remove_dimension_custom_fields(doctype, dimension)

	for doctype in (*RETIRED_DOCTYPES, OLD_STOCK_TO_ASSET_DOCTYPE):
		remove_layout_anchor_custom_fields(doctype, dimensions)


def move_stock_to_asset_dimension(dimension):
	fieldname = dimension.source_fieldname
	legacy_custom_field = frappe.db.get_value(
		"Custom Field",
		{
			"dt": OLD_STOCK_TO_ASSET_DOCTYPE,
			"fieldname": fieldname,
			"fieldtype": "Link",
			"options": dimension.reference_document,
		},
		"name",
	)
	if not legacy_custom_field and not (
		dimension.name == "Storage Location" and fieldname == "storage_location"
	):
		return

	if not frappe.db.has_column(OLD_STOCK_TO_ASSET_DOCTYPE, fieldname):
		return

	if not field_exists(NEW_STOCK_TO_ASSET_DOCTYPE, fieldname) and not frappe.db.exists(
		"Custom Field", {"dt": NEW_STOCK_TO_ASSET_DOCTYPE, "fieldname": fieldname}
	):
		dimension_doc = frappe.get_doc("Inventory Dimension", dimension.name)
		create_custom_fields(
			{NEW_STOCK_TO_ASSET_DOCTYPE: dimension_doc.get_dimension_fields(NEW_STOCK_TO_ASSET_DOCTYPE)}
		)

	if not frappe.db.has_column(NEW_STOCK_TO_ASSET_DOCTYPE, fieldname):
		return

	values_by_source_row = {}
	for row in frappe.get_all(
		OLD_STOCK_TO_ASSET_DOCTYPE,
		filters={fieldname: ["is", "set"], "source_item_row": ["is", "set"]},
		fields=["source_item_row", fieldname],
	):
		values_by_source_row.setdefault(row.source_item_row, set()).add(row.get(fieldname))

	for source_item_row, values in values_by_source_row.items():
		if len(values) != 1:
			# Multiple output rows disagree; keep their original values for manual resolution.
			frappe.log_error(
				title=f"Inventory Dimension migration conflict: {dimension.name}",
				message=frappe.as_json({"source_item_row": source_item_row, "values": sorted(values)}),
			)
			continue

		value = next(iter(values))
		current_value = frappe.db.get_value(NEW_STOCK_TO_ASSET_DOCTYPE, source_item_row, fieldname)
		if current_value:
			if current_value != value:
				frappe.log_error(
					title=f"Inventory Dimension migration conflict: {dimension.name}",
					message=frappe.as_json(
						{
							"source_item_row": source_item_row,
							"existing_item_value": current_value,
							"asset_values": [value],
						}
					),
				)
			continue

		frappe.db.set_value(
			NEW_STOCK_TO_ASSET_DOCTYPE,
			source_item_row,
			fieldname,
			value,
			update_modified=False,
		)


def remove_dimension_custom_fields(doctype, dimension):
	fieldnames = (
		dimension.source_fieldname,
		f"to_{dimension.source_fieldname}",
		f"from_{dimension.source_fieldname}",
		f"rejected_{dimension.source_fieldname}",
	)
	custom_fields = frappe.get_all(
		"Custom Field",
		filters={"dt": doctype, "fieldname": ["in", fieldnames]},
		fields=["name", "fieldname", "fieldtype", "options"],
	)
	for custom_field in custom_fields:
		if custom_field.fieldtype == "Link" and custom_field.options == dimension.reference_document:
			# Retain the database column in case historical rows still contain a dimension value.
			frappe.db.delete("Custom Field", custom_field.name)
			frappe.clear_cache(doctype=doctype)


def remove_layout_anchor_custom_fields(doctype, dimensions):
	fieldtypes = {
		"inventory_dimension": "Section Break",
		"inventory_dimension_col_break": "Column Break",
	}
	for dimension in dimensions:
		if not dimension.source_fieldname:
			continue
		fieldtypes[f"inventory_dimension_{dimension.source_fieldname}"] = "Section Break"
		fieldtypes[f"inventory_dimension_col_break_{dimension.source_fieldname}"] = "Column Break"

	for fieldname, fieldtype in fieldtypes.items():
		custom_field = frappe.db.get_value(
			"Custom Field",
			{"dt": doctype, "fieldname": fieldname, "fieldtype": fieldtype},
			"name",
		)
		if custom_field:
			frappe.db.delete("Custom Field", custom_field)
			frappe.clear_cache(doctype=doctype)
