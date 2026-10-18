# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.desk.reportview import get_match_cond
from frappe.model.document import Document

# Every active leaf Warehouse gets this location as its default, so stock rows are filled in.
DEFAULT_LOCATION_CODE = "STAGING"


class StorageLocation(Document):
	def validate(self):
		conditions = [row.storage_condition for row in self.get("supported_storage_conditions") or []]
		if len(conditions) != len(set(conditions)):
			frappe.throw(_("Storage Conditions cannot be repeated."))
		warehouse = frappe.get_cached_value(
			"Warehouse", self.warehouse, ["is_group", "disabled"], as_dict=True
		)
		if not warehouse or warehouse.is_group or warehouse.disabled:
			frappe.throw(_("Storage Location requires an active, non-group Warehouse."))
		if not self.is_new() and self.has_value_changed("warehouse"):
			if frappe.db.has_column("Stock Ledger Entry", "storage_location") and frappe.db.exists(
				"Stock Ledger Entry", {"storage_location": self.name}
			):
				frappe.throw(
					_("Cannot change Warehouse after stock transactions exist for this Storage Location.")
				)


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
			frappe.throw(
				_("The existing Storage Location Inventory Dimension conflicts with the built-in dimension.")
			)
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


def ensure_default_storage_location(warehouse):
	"""Give an active leaf Warehouse a STAGING location and make it the default when none is set."""
	if warehouse.is_group or warehouse.disabled or warehouse.default_storage_location:
		return None
	location = frappe.db.get_value(
		"Storage Location",
		{"warehouse": warehouse.name, "location_code": DEFAULT_LOCATION_CODE},
		["name", "disabled"],
		as_dict=True,
	)
	if location and location.disabled:
		return None
	if not location:
		# Storage Location reads the Warehouse through the cache, which may still hold the group state.
		frappe.clear_document_cache("Warehouse", warehouse.name)
		location = frappe.get_doc(
			{
				"doctype": "Storage Location",
				"warehouse": warehouse.name,
				"location_code": DEFAULT_LOCATION_CODE,
			}
		).insert(ignore_permissions=True)
	warehouse.db_set("default_storage_location", location.name, update_modified=False)
	return location.name


def delete_storage_locations(warehouse):
	"""Delete a Warehouse's Storage Locations with it, including Item preferences that point to them."""
	frappe.db.set_value("Warehouse", warehouse, "default_storage_location", None, update_modified=False)
	locations = frappe.get_all("Storage Location", filters={"warehouse": warehouse}, pluck="name")
	if locations:
		frappe.db.delete("Item Storage Location", {"storage_location": ["in", locations]})
	for location in locations:
		frappe.delete_doc("Storage Location", location, ignore_permissions=True)


def rename_storage_locations(warehouse):
	"""Keep Storage Location names prefixed with their Warehouse after it is renamed or merged."""
	for location in frappe.get_all(
		"Storage Location", filters={"warehouse": warehouse}, fields=["name", "location_code"]
	):
		target = f"{warehouse} / {location.location_code}"
		if location.name != target:
			frappe.rename_doc(
				"Storage Location",
				location.name,
				target,
				force=True,
				merge=bool(frappe.db.exists("Storage Location", target)),
				show_alert=False,
			)


def get_default_storage_location(warehouse, item_code=None):
	"""The Warehouse default, or its first location that meets the Item's Storage Condition."""
	default = frappe.get_cached_value("Warehouse", warehouse, "default_storage_location")
	condition = frappe.get_cached_value("Item", item_code, "storage_condition") if item_code else None
	if not condition:
		return default
	location = frappe.db.sql(
		"""select location.name
		from `tabStorage Location` location
		join `tabStorage Location Condition` supported
			on supported.parent = location.name and supported.parenttype = 'Storage Location'
			and supported.parentfield = 'supported_storage_conditions'
		left join `tabItem Storage Location` preference
			on preference.storage_location = location.name
			and preference.parent = %(item_code)s and preference.parenttype = 'Item'
			and preference.parentfield = 'preferred_storage_locations'
		where location.warehouse = %(warehouse)s and location.disabled = 0
			and supported.storage_condition = %(condition)s
		order by location.name = %(default)s desc, case when preference.idx is null then 1 else 0 end,
			preference.idx, location.name
		limit 1""",
		{"warehouse": warehouse, "item_code": item_code, "condition": condition, "default": default or ""},
	)
	if not location:
		frappe.throw(
			_("Warehouse {0} has no Storage Location that supports Storage Condition {1} required by Item {2}.").format(
				warehouse, condition, item_code
			),
			title=_("Incompatible Storage Location"),
		)
	return location[0][0]


def get_storage_location_warehouses(doc, row):
	"""Warehouse owning each native location field on a stock voucher row."""
	warehouses = {
		"storage_location": row.get("s_warehouse") if doc.doctype == "Stock Entry" else row.get("warehouse"),
		"to_storage_location": row.get("t_warehouse")
		or row.get("target_warehouse")
		or row.get("to_warehouse"),
		"from_storage_location": row.get("from_warehouse"),
		"rejected_storage_location": row.get("rejected_warehouse"),
	}
	if row.get("rm_item_code") and row.get("parentfield") == "supplied_items":
		warehouses["storage_location"] = doc.get("supplier_warehouse")
	return warehouses


def set_default_storage_locations(doc, method=None):
	"""Persist warehouse defaults before stock valuation and serial/batch processing."""
	# Historical submissions and cancellations must retain their original dimensions.
	if doc.docstatus == 2 or (
		not doc.is_new() and doc.get_doc_before_save() and doc.get_doc_before_save().docstatus == 1
	):
		return
	if doc.doctype in ("Sales Invoice", "Purchase Invoice", "POS Invoice") and not doc.get("update_stock"):
		return

	for table in doc.meta.get_table_fields():
		for row in doc.get(table.fieldname) or []:
			for fieldname, warehouse in get_storage_location_warehouses(doc, row).items():
				if not warehouse or not row.meta.has_field(fieldname) or row.get(fieldname):
					continue
				item_code = row.get("item_code") or row.get("rm_item_code")
				location = get_default_storage_location(warehouse, item_code)
				if location:
					validate_storage_location_warehouse(location, warehouse, item_code=item_code)
					row.set(fieldname, location)


def validate_storage_location(ledger_entry):
	"""Require a location for new ledger postings, while permitting historical reversals."""
	location_name = ledger_entry.get("storage_location")
	if not location_name:
		if not ledger_entry.get("is_cancelled"):
			frappe.throw(
				_("Storage Location is required to post stock for Item {0} in Warehouse {1}.").format(
					ledger_entry.get("item_code"), ledger_entry.get("warehouse")
				),
				title=_("Storage Location Required"),
			)
		return
	location = frappe.get_cached_value(
		"Storage Location", location_name, ["warehouse", "disabled"], as_dict=True
	)
	if not location or location.warehouse != ledger_entry.warehouse:
		frappe.throw(
			_("Storage Location {0} does not belong to Warehouse {1}.").format(
				location_name, ledger_entry.warehouse
			)
		)
	if location.disabled and not ledger_entry.get("is_cancelled"):
		frappe.throw(_("Storage Location {0} is disabled.").format(location_name))
	# Valuation-only LCV recreation keeps the historical location, as cancellations do.
	if (
		not ledger_entry.get("is_cancelled")
		and frappe.flags.get("warehouse_policy_lcv_entry") is not ledger_entry
	):
		validate_item_storage_condition(location_name, ledger_entry.get("item_code"))


def validate_storage_location_warehouse(storage_location, warehouse, item_code=None, storage_condition=None):
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
	validate_item_storage_condition(storage_location, item_code, storage_condition)


def validate_item_storage_condition(storage_location, item_code=None, storage_condition=None):
	"""Enforce Item requirements even when the location is supplied outside the picker."""
	if storage_condition is None:
		storage_condition = (
			frappe.get_cached_value("Item", item_code, "storage_condition") if item_code else None
		)
	if not storage_condition:
		return
	conditions = (
		frappe.get_cached_doc("Storage Location", storage_location).get("supported_storage_conditions") or []
	)
	if not any(row.storage_condition == storage_condition for row in conditions):
		frappe.throw(
			_("Storage Location {0} does not support Storage Condition {1} required by Item {2}.").format(
				storage_location, storage_condition, item_code or _("(not set)")
			),
			title=_("Incompatible Storage Location"),
		)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def storage_location_query(doctype, txt, searchfield, start, page_len, filters):
	"""Rank Item preferences before other active locations satisfying the same condition."""
	filters = frappe.parse_json(filters) or {}
	warehouse = filters.get("warehouse")
	if doctype != "Storage Location" or not warehouse:
		return []
	if not frappe.has_permission("Storage Location", "select"):
		frappe.throw(_("Not permitted"), frappe.PermissionError)
	warehouse_doc = frappe.get_cached_doc("Warehouse", warehouse)
	if (
		warehouse_doc.is_group
		or warehouse_doc.disabled
		or not frappe.has_permission("Warehouse", "read", doc=warehouse_doc)
	):
		return []
	item_code = filters.get("item_code")
	condition = filters.get("storage_condition")
	if item_code:
		item = frappe.get_cached_doc("Item", item_code)
		if not frappe.has_permission("Item", "select", doc=item):
			return []
		# An Item's persisted requirement cannot be overridden by request filters.
		condition = item.get("storage_condition")

	return frappe.db.sql(
		"""select `tabStorage Location`.name, `tabStorage Location`.location_code,
			case when preference.idx is null then '' else %(preferred_label)s end
		from `tabStorage Location`
		left join `tabItem Storage Location` preference
			on preference.storage_location = `tabStorage Location`.name
			and preference.parent = %(item_code)s and preference.parenttype = 'Item'
			and preference.parentfield = 'preferred_storage_locations'
		where `tabStorage Location`.warehouse = %(warehouse)s and `tabStorage Location`.disabled = 0
			and (%(condition)s = '' or exists (
				select 1 from `tabStorage Location Condition` supported
				where supported.parent = `tabStorage Location`.name
					and supported.parenttype = 'Storage Location'
					and supported.parentfield = 'supported_storage_conditions'
					and supported.storage_condition = %(condition)s
			))
			and (`tabStorage Location`.name like %(txt)s or `tabStorage Location`.location_code like %(txt)s
				or `tabStorage Location`.zone like %(txt)s or `tabStorage Location`.rack like %(txt)s
				or `tabStorage Location`.shelf like %(txt)s)
			{match_cond}
		order by case when preference.idx is null then 1 else 0 end,
			preference.idx, `tabStorage Location`.name
		limit %(page_len)s offset %(start)s""".format(match_cond=get_match_cond("Storage Location")),
		{
			"warehouse": warehouse,
			"item_code": item_code or "",
			"condition": condition or "",
			"txt": f"%{txt}%",
			"preferred_label": _("Preferred for Item"),
			"page_len": page_len,
			"start": start,
		},
	)


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	"""Warehouses for an item row: only those with a location that meets the Item's Storage Condition."""
	from frappe.desk.search import search_widget

	filters = frappe.parse_json(filters) or {}
	item_code = filters.get("storage_condition_item")
	base = filters.get("filters") or {}
	condition = None
	if item_code and frappe.db.exists("Item", item_code):
		item = frappe.get_cached_doc("Item", item_code)
		if frappe.has_permission("Item", "select", doc=item):
			condition = item.get("storage_condition")
	if condition:
		if isinstance(base, dict):
			base = [
				["Warehouse", key, *(value if isinstance(value, list | tuple) else ["=", value])]
				for key, value in base.items()
			]
		warehouses = frappe.db.sql_list(
			"""select distinct location.warehouse
			from `tabStorage Location` location
			join `tabStorage Location Condition` supported
				on supported.parent = location.name and supported.parenttype = 'Storage Location'
				and supported.parentfield = 'supported_storage_conditions'
			where location.disabled = 0 and supported.storage_condition = %s""",
			condition,
		)
		base = [*base, ["Warehouse", "name", "in", warehouses or [""]]]
	return search_widget(
		"Warehouse", txt, searchfield=searchfield, start=start, page_length=page_len, filters=base
	)
