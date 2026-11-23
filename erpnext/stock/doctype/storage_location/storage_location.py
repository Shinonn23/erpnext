# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.desk.reportview import get_match_cond
from frappe.model.document import Document
from frappe.utils import cint, cstr, flt, get_time, getdate

# Every active leaf Warehouse gets a default location so stock rows are filled in:
# STAGING (Receiving) for sellable warehouses, HOLDING (Holding) for holding warehouses.
DEFAULT_LOCATION_CODE = "STAGING"
DEFAULT_HOLDING_LOCATION_CODE = "HOLDING"

# What the bin is for (Location Type). The Warehouse Type says what the stock is; the Location Type says what the bin is
# for. A Receiving location is where goods land; they leave it only by a Stock Entry: never sold, issued, lent, converted
# or picked from it (Connect Diagnostics, Siwat 17 Nov 2026).
RECEIVING = "Receiving"
STORAGE = "Storage"
SALES_COUNTER = "Sales Counter"
HOLDING = "Holding"
QUALITY_INSPECTION = "Quality Inspection"
DAMAGE = "Damage"
LOST = "Lost"
HOLD_ZONE_TYPES = (QUALITY_INSPECTION, DAMAGE, LOST)
LOCATION_TYPES = (RECEIVING, STORAGE, SALES_COUNTER, HOLDING, *HOLD_ZONE_TYPES)
PUTAWAY_LOCATION_TYPES = (STORAGE, SALES_COUNTER)
# every native location field of a stock voucher row (cleared on a row whose Item is not a stock Item)
LOCATION_FIELDS = (
	"storage_location",
	"to_storage_location",
	"from_storage_location",
	"rejected_storage_location",
)


def is_receiving_location(location, warehouse=None):
	"""The Storage Location is an enabled Receiving location, of ``warehouse`` when given. Exactly the test of
	``receiving_location_sql``."""
	if not location:
		return False
	values = frappe.db.get_value(
		"Storage Location", location, ["warehouse", "location_type", "disabled"], as_dict=True
	)
	return bool(
		values
		and (not warehouse or values.warehouse == warehouse)
		and not cint(values.disabled)
		and values.location_type == RECEIVING
	)


def receiving_location_sql(expression, warehouse_expression=None):
	"""SQL condition that is true when the Storage Location named by ``expression`` is an enabled Receiving location
	(of the warehouse named by ``warehouse_expression`` when given): the test of ``is_receiving_location``. An empty or
	unknown location is not one. ``expression`` and ``warehouse_expression`` are trusted code, never user input."""
	own = f" and receiving_sl.warehouse = ({warehouse_expression})" if warehouse_expression else ""
	return (
		"exists (select 1 from `tabStorage Location` receiving_sl "
		f"where receiving_sl.name = ({expression}) and receiving_sl.location_type = 'Receiving' "
		f"and receiving_sl.disabled = 0{own})"
	)


def get_location_type(location, warehouse=None):
	if not location:
		return None
	row = frappe.get_cached_value("Storage Location", location, ("warehouse", "location_type"), as_dict=True)
	return row.location_type if row and (not warehouse or row.warehouse == warehouse) else None


def is_restricted_location(location, warehouse=None):
	return get_location_type(location, warehouse) in HOLD_ZONE_TYPES or is_receiving_location(
		location, warehouse
	)


def restricted_location_sql(expression, warehouse_expression=None):
	own = f" and restricted_sl.warehouse = ({warehouse_expression})" if warehouse_expression else ""
	return (
		"exists (select 1 from `tabStorage Location` restricted_sl "
		f"where restricted_sl.name = ({expression}){own} and ("
		"restricted_sl.location_type in ('Quality Inspection', 'Damage', 'Lost') "
		"or (restricted_sl.location_type = 'Receiving' and restricted_sl.disabled = 0)))"
	)


def typed_location(warehouse, item_code, location_type):
	"""Return the enabled zone that supports the Item's storage condition."""
	if not warehouse:
		return None
	condition = frappe.get_cached_value("Item", item_code, "storage_condition") if item_code else None
	rows = frappe.db.sql(
		"""select sl.name from `tabStorage Location` sl
		where sl.warehouse = %(warehouse)s and sl.location_type = %(kind)s and sl.disabled = 0
		and (%(condition)s = '' or exists (
			select 1 from `tabStorage Location Condition` c
			where c.parent = sl.name and c.parenttype = 'Storage Location'
				and c.parentfield = 'supported_storage_conditions' and c.storage_condition = %(condition)s
		))
		order by sl.name limit 1""",
		{"warehouse": warehouse, "kind": location_type, "condition": condition or ""},
		pluck=True,
	)
	return rows[0] if rows else None


def is_stock_type_warehouse(warehouse):
	"""Use the stock operation policy to distinguish sellable warehouses from holding warehouses."""
	from erpnext.stock.warehouse_policy import is_sellable_warehouse

	return is_sellable_warehouse(warehouse)


@frappe.whitelist()
def allowed_location_types(warehouse):
	"""Location Types allowed in ``warehouse``: Receiving/Storage/Sales Counter for a sellable warehouse, Holding only
	for a holding warehouse."""
	if not warehouse:
		return list(LOCATION_TYPES)
	return (
		[RECEIVING, STORAGE, SALES_COUNTER, *HOLD_ZONE_TYPES]
		if is_stock_type_warehouse(warehouse)
		else [HOLDING]
	)


def sales_counter_location(warehouse):
	"""The enabled Sales Counter location of ``warehouse`` (the first by name), else None."""
	if not warehouse:
		return None
	return frappe.db.get_value(
		"Storage Location",
		{"warehouse": warehouse, "location_type": SALES_COUNTER, "disabled": 0},
		"name",
		order_by="name asc",
	)


def preferred_location(item_code, warehouse):
	"""The Item's first preferred Storage Location of ``warehouse`` that is enabled and not a Receiving location."""
	if not item_code or not warehouse:
		return None
	rows = frappe.db.sql(
		"""select location.name
		from `tabItem Storage Location` preference
		join `tabStorage Location` location on location.name = preference.storage_location
		where preference.parent = %(item)s and preference.parenttype = 'Item'
			and preference.parentfield = 'preferred_storage_locations'
			and location.warehouse = %(warehouse)s and location.disabled = 0
			and location.location_type in ('Storage', 'Sales Counter', 'Holding')
		order by preference.idx
		limit 1""",
		{"item": item_code, "warehouse": warehouse},
	)
	return rows[0][0] if rows else None


class StorageLocation(Document):
	def before_insert(self):
		# before the name is made ("{warehouse} / {location_code}")
		self.set_location_code()

	def set_location_code(self):
		"""A blank Location Code is built from Zone, Rack and Shelf (A-01-02): the same parts are not typed twice
		(Siwat, 6 Nov 2026, UX review)."""
		if not cstr(self.location_code).strip():
			parts = [cstr(self.get(field)).strip() for field in ("zone", "rack", "shelf")]
			self.location_code = "-".join(part for part in parts if part)
		if not cstr(self.location_code).strip():
			frappe.throw(_("Enter the Location Code, or the Zone, Rack and Shelf it is built from."))

	def validate(self):
		self.set_location_code()
		conditions = [row.storage_condition for row in self.get("supported_storage_conditions") or []]
		if len(conditions) != len(set(conditions)):
			frappe.throw(_("Storage Conditions cannot be repeated."))
		warehouse = frappe.get_cached_value(
			"Warehouse", self.warehouse, ["is_group", "disabled"], as_dict=True
		)
		if not warehouse or warehouse.is_group or warehouse.disabled:
			frappe.throw(_("Storage Location requires an active, non-group Warehouse."))
		allowed = allowed_location_types(self.warehouse)
		if self.location_type and self.location_type not in allowed:
			frappe.throw(
				_("Warehouse {0} only allows Location Type: {1}.").format(
					self.warehouse, ", ".join(_(t) for t in allowed)
				)
			)
		previous = self.get_doc_before_save()
		if (
			previous
			and previous.location_type != self.location_type
			and ({previous.location_type, self.location_type} & set(HOLD_ZONE_TYPES))
			and frappe.db.exists("Stock Ledger Entry", {"storage_location": self.name})
		):
			frappe.throw(_("Move the stock to another location to change its hold status."))
		if not self.is_new() and self.has_value_changed("warehouse"):
			if frappe.db.has_column("Stock Ledger Entry", "storage_location") and frappe.db.exists(
				"Stock Ledger Entry", {"storage_location": self.name}
			):
				frappe.throw(
					_("Cannot change Warehouse after stock transactions exist for this Storage Location.")
				)


def setup_storage_location_dimension():
	"""Clean up legacy Inventory Dimension metadata; Storage Location is a native dimension."""
	if not frappe.db.exists("DocType", "Storage Location"):
		return
	remove_legacy_storage_location_custom_field()
	remove_loan_inventory_dimension_custom_fields()
	if frappe.db.exists("Inventory Dimension", "Storage Location"):
		frappe.db.delete("Inventory Dimension", "Storage Location")
		frappe.clear_cache()


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
	"""Remove generated Inventory Dimension fields from borrowing item rows, preserving their columns."""
	loan_item_doctypes = [
		"Borrowing Adjustment Item",
		"Borrowing Item",
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
	"""Give an active leaf Warehouse a default location when none is set: STAGING (Receiving) in a sellable warehouse,
	HOLDING (Holding) in a holding warehouse."""
	if warehouse.is_group or warehouse.disabled or warehouse.default_storage_location:
		return None
	sellable = is_stock_type_warehouse(warehouse.name)
	code = DEFAULT_LOCATION_CODE if sellable else DEFAULT_HOLDING_LOCATION_CODE
	location_type = RECEIVING if sellable else HOLDING
	location = frappe.db.get_value(
		"Storage Location",
		{"warehouse": warehouse.name, "location_code": code},
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
				"location_code": code,
				"location_type": location_type,
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
			and (location.location_type is null or location.location_type not in %(held_types)s)
		order by location.name = %(default)s desc, case when preference.idx is null then 1 else 0 end,
			preference.idx, location.name
		limit 1""",
		{
			"warehouse": warehouse,
			"item_code": item_code,
			"condition": condition,
			"default": default or "",
			"held_types": HOLD_ZONE_TYPES,
		},
	)
	if not location:
		frappe.throw(
			_(
				"Warehouse {0} has no Storage Location that supports Storage Condition {1} required by Item {2}."
			).format(warehouse, condition, item_code),
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


def is_outgoing_row(doc, row):
	"""The row takes goods out of its source warehouse: a Stock Entry row with a source warehouse (Material Issue,
	the source leg of a transfer ...), a Delivery Note, Sales Invoice or POS Invoice (with stock) that is not a return,
	and a Stock to Asset Conversion."""
	if doc.doctype == "Stock Entry":
		return bool(row.get("s_warehouse"))
	if doc.doctype == "Stock to Asset Conversion":
		return True
	return doc.doctype in ("Delivery Note", "Sales Invoice", "POS Invoice") and not cint(doc.get("is_return"))


def refuses_receiving_source(doc, row):
	"""The row may not take its goods from a Receiving location: stock leaves a Receiving location only by a Stock Entry
	that puts it away or holds it (Siwat, 17 Nov 2026). A non-return Delivery Note, a Sales Invoice or POS Invoice with
	Update Stock, a Stock to Asset Conversion, a Material Issue and the source leg of a transfer to another stock
	warehouse. A put-away inside one warehouse and a move into a holding warehouse (Quarantine, Write-Off ...) may."""
	if doc.doctype == "Delivery Note":
		return not cint(doc.get("is_return"))
	if doc.doctype in ("Sales Invoice", "POS Invoice"):
		return not cint(doc.get("is_return")) and bool(cint(doc.get("update_stock")))
	if doc.doctype == "Stock to Asset Conversion":
		return True
	if doc.doctype != "Stock Entry" or not row.get("s_warehouse"):
		return False
	target = row.get("t_warehouse")
	if not target:
		return doc.get("purpose") == "Material Issue"
	return target != row.get("s_warehouse") and is_stock_type_warehouse(target)


def row_stock_qty(doc, row):
	"""The quantity the row takes out, in the Item's stock UOM."""
	if doc.doctype == "Stock Entry":
		return flt(row.get("transfer_qty")) or flt(row.get("qty")) * (flt(row.get("conversion_factor")) or 1)
	return flt(row.get("stock_qty")) or flt(row.get("qty")) * (flt(row.get("conversion_factor")) or 1)


def choose_source_location(places, needed):
	"""(location, enough): the one Storage Location of ``places`` ({location: qty}) that holds ``needed``; None when no
	or more than one does. ``enough`` lists every location that holds it."""
	enough = sorted(location for location, qty in places.items() if flt(qty) + 1e-9 >= flt(needed))
	return (enough[0] if len(enough) == 1 else None), enough


def source_storage_location(doc, row, warehouse, item_code):
	"""The Storage Location an outgoing row takes its goods from, when its source location is empty (Connect
	Diagnostics, Siwat, 5 Nov 2026: never a silent STAGING). The location that holds the stock when exactly one
	location holds enough of it (the row's Serial Nos, its Batch No, or the Item); when located stock exists but no
	single location holds enough, or several do, the row is refused, naming the row and the locations that hold the
	stock. A row that may not take from a Receiving location (``refuses_receiving_source``) never counts the stock lying
	there. None, so the row stays empty and the user chooses (WP1, 18 Nov 2026: never the warehouse default of a stock
	warehouse), when no location holds any of it (no stock, or lab stock and legacy balances without a Storage
	Location), when the warehouse holds enough of it without a Storage Location, and when the stock cannot be read per
	location (not a stock Item, a row with a Serial and Batch Bundle, negative stock allowed)."""
	item = frappe.get_cached_value(
		"Item", item_code, ["is_stock_item", "has_serial_no", "has_batch_no"], as_dict=True
	)
	if (
		not item
		or not cint(item.is_stock_item)
		or row.get("serial_and_batch_bundle")
		or cint(frappe.db.get_single_value("Stock Settings", "allow_negative_stock"))
	):
		return None
	needed = row_stock_qty(doc, row)
	if needed <= 0:
		return None
	exclude_receiving = refuses_receiving_source(doc, row)
	serials = _row_serial_nos(row) if cint(item.has_serial_no) else []
	if serials:
		places = serial_locations(serials, warehouse, exclude_receiving=exclude_receiving)
		needed = len(serials)
	else:
		places = location_stock(
			item_code, warehouse, row.get("batch_no") or None, exclude_receiving=exclude_receiving
		)
	location, enough = choose_source_location(places, needed)
	if location:
		return location
	if not places:
		# no stock sits at any Storage Location it may take from (none at all, only at a Receiving location, or lab
		# stock and legacy balances without one): the user chooses and the submit decides
		return None
	if not enough and not serials:
		# a balance that carries no Storage Location is enough: the user chooses
		unlocated = location_stock(item_code, warehouse, row.get("batch_no") or None, unlocated=True)
		if flt(unlocated.get("")) + 1e-9 >= flt(needed):
			return None
	where = ", ".join(f"{name} ({flt(qty):g})" for name, qty in sorted(places.items())) or _("none")
	if enough:
		frappe.throw(
			_(
				"Row {0}: {1} is held at more than one Storage Location of {2} ({3}). Choose the Storage Location to take it from."
			).format(row.get("idx"), item_code, warehouse, where),
			title=_("Choose the Storage Location"),
		)
	frappe.throw(
		_("Row {0}: no single Storage Location of {1} holds {2} of {3}. In stock by location: {4}.").format(
			row.get("idx"), warehouse, f"{flt(needed):g}", item_code, where
		),
		title=_("Not Enough Stock"),
	)


def set_default_storage_locations(doc, method=None):
	"""Fill the empty Storage Location fields of a stock voucher before stock valuation and serial/batch processing,
	with what the save will store, so the screen never shows one value and the save another (WP1, 18 Nov 2026):

	- a row whose Item is not a stock Item gets none;
	- an outgoing row takes the one location that holds its stock (``source_storage_location``), else stays empty for the
	  user to choose; it is never filled from the default of a stock warehouse (its Receiving location). The one bin of a
	  holding warehouse (Quarantine, Lost, Borrowed ...) is still its default;
	- the target of a Stock Entry transfer into a stock warehouse stays empty (a put-away inside one warehouse, a move out
	  of quarantine, a transfer into a normal warehouse), except into a warehouse with a Sales Counter location (a lab):
	  the Item's preferred location there, else the Sales Counter; into a holding warehouse it is that warehouse's bin;
	- an incoming leg (a receipt, a return, a Material Receipt, a count) gets the warehouse default."""
	# Historical submissions and cancellations must retain their original dimensions.
	if doc.docstatus == 2 or (
		not doc.is_new() and doc.get_doc_before_save() and doc.get_doc_before_save().docstatus == 1
	):
		return
	if doc.doctype in ("Sales Invoice", "Purchase Invoice", "POS Invoice") and not doc.get("update_stock"):
		return

	for table in doc.meta.get_table_fields():
		for row in doc.get(table.fieldname) or []:
			if (
				cint(doc.get("is_return"))
				and cint(row.get("return_qty_from_rejected_warehouse"))
				and not row.get("rejected_warehouse")
				and not flt(row.get("rejected_qty"))
			):
				# Older return drafts copied the unused rejected leg after moving its warehouse to the main leg.
				row.storage_location = row.get("storage_location") or row.get("rejected_storage_location")
				row.rejected_storage_location = None
			if doc.doctype == "Stock Reconciliation" and doc.is_warehouse_revaluation_row(row):
				continue
			item_code = row.get("item_code") or row.get("rm_item_code")
			if item_code and not cint(frappe.get_cached_value("Item", item_code, "is_stock_item")):
				continue
			for fieldname, warehouse in get_storage_location_warehouses(doc, row).items():
				if not warehouse or not row.meta.has_field(fieldname) or row.get(fieldname):
					continue
				try:
					location = default_row_location(doc, row, fieldname, warehouse, item_code)
				except frappe.ValidationError:
					# the Packing List of a Delivery Note appears only after its first save: the user cannot choose
					# before it, so a draft leaves the location empty and the submit refuses it
					if table.fieldname == "packed_items" and not doc.docstatus:
						if frappe.local.message_log:
							frappe.local.message_log.pop()
						continue
					raise
				if location:
					validate_storage_location_warehouse(location, warehouse, item_code=item_code)
					row.set(fieldname, location)


def default_row_location(doc, row, fieldname, warehouse, item_code):
	"""The location ``set_default_storage_locations`` stores in an empty ``fieldname`` of ``row``, or None (left to the
	user). The source of a POS Invoice row of a register (``pos_profile``) is the register's own Sales Storage Location,
	set by the Connect Diagnostics app after this hook (``workflows/pos_location.set_sale_location``): it is left empty
	here and never computed, so a cashier, who cannot choose a location on the POS page, is never refused for stock held
	at several locations."""
	if fieldname == "storage_location" and doc.doctype == "POS Invoice" and doc.get("pos_profile"):
		return None
	if (
		fieldname == "storage_location"
		and doc.doctype in ("Purchase Receipt", "Purchase Invoice")
		and not cint(doc.get("is_return"))
		and is_stock_type_warehouse(warehouse)
	):
		return preferred_purchase_location(warehouse, item_code)
	if fieldname == "storage_location" and is_outgoing_row(doc, row):
		location = source_storage_location(doc, row, warehouse, item_code)
		if location or is_stock_type_warehouse(warehouse):
			return location
		return get_default_storage_location(warehouse, item_code)
	if (
		fieldname == "to_storage_location"
		and doc.doctype == "Stock Entry"
		and doc.get("purpose") == "Material Transfer"
		and row.get("s_warehouse")
	):
		# only a Material Transfer: a transfer for manufacture or to a subcontractor keeps the target's default
		return transfer_target_location(row, warehouse, item_code)
	return get_default_storage_location(warehouse, item_code)


def preferred_purchase_location(warehouse, item_code):
	"""Use a configured, compatible place for put-away. Leave an unconfigured shelf for the receiver to choose."""
	for location in (
		preferred_location(item_code, warehouse),
		frappe.get_cached_value("Warehouse", warehouse, "default_storage_location"),
	):
		if not location:
			continue
		bin_details = frappe.get_cached_value(
			"Storage Location", location, ("warehouse", "disabled", "location_type"), as_dict=True
		)
		if (
			bin_details
			and bin_details.warehouse == warehouse
			and not bin_details.disabled
			and bin_details.location_type in PUTAWAY_LOCATION_TYPES
			and _meets_condition(location, item_code)
		):
			return location
	return None


def transfer_target_location(row, warehouse, item_code):
	"""Where a Stock Entry transfer row puts its goods when the user named no place: the bin of a holding warehouse;
	in a stock warehouse nothing for a put-away inside one warehouse, a move out of quarantine (``against_stock_entry``
	/ ``ste_detail``) and a transfer into a warehouse without a Sales Counter; into a lab (a warehouse with a Sales
	Counter location) the Item's preferred location there, else the Sales Counter, when it meets the Item's Storage
	Condition. Never a Receiving location."""
	if not is_stock_type_warehouse(warehouse):
		return get_default_storage_location(warehouse, item_code)
	if row.get("s_warehouse") == warehouse or row.get("against_stock_entry") or row.get("ste_detail"):
		return None
	counter = sales_counter_location(warehouse)
	if not counter:
		return None
	for location in (preferred_location(item_code, warehouse), counter):
		if location and _meets_condition(location, item_code):
			return location
	return None


def _meets_condition(location, item_code):
	try:
		validate_item_storage_condition(location, item_code)
	except frappe.ValidationError:
		if frappe.local.message_log:
			frappe.local.message_log.pop()
		return False
	return True


def stock_source_locations(row):
	"""Source locations carried by the row or its tracked stock."""
	locations = [row.get("storage_location")]
	bundle = row.get("serial_and_batch_bundle")
	if bundle:
		locations += frappe.get_all(
			"Serial and Batch Entry", filters={"parent": bundle}, pluck="storage_location", order_by="idx"
		)
	return dict.fromkeys(location for location in locations if location)


def receiving_source_locations(row, warehouse):
	return [
		location for location in stock_source_locations(row) if is_receiving_location(location, warehouse)
	]


def validate_receiving_source(doc, row):
	"""Refuse a row that may not take from a Receiving location (``refuses_receiving_source``) and does; at the submit,
	also an outgoing stock row with no Storage Location, naming the row."""
	warehouse = get_storage_location_warehouses(doc, row).get("storage_location")
	if not warehouse or not row.meta.has_field("storage_location"):
		return
	item_code = row.get("item_code") or row.get("rm_item_code")
	if doc.doctype != "Stock Entry" and is_outgoing_row(doc, row):
		for location in stock_source_locations(row):
			if get_location_type(location, warehouse) in HOLD_ZONE_TYPES:
				frappe.throw(
					_("Row {0}: stock held at {1} cannot be sold, issued, or consumed.").format(
						row.get("idx"), location
					)
				)
	if refuses_receiving_source(doc, row):
		for location in receiving_source_locations(row, warehouse):
			frappe.throw(
				_(
					"Row {0}: {1} is at the receiving location {2}; put it away with a Stock Entry first, or choose the Storage Location."
				).format(row.get("idx"), item_code, location),
				title=_("Receiving Location"),
			)
	if (
		cint(doc.get("docstatus")) == 1
		and is_outgoing_row(doc, row)
		and not row.get("storage_location")
		and not row.get("serial_and_batch_bundle")
		and flt(row_stock_qty(doc, row)) > 0
	):
		frappe.throw(
			_("Row {0}: choose the Storage Location of {1} in {2} the goods are taken from.").format(
				row.get("idx"), item_code, warehouse
			),
			title=_("Choose the Storage Location"),
		)


def is_warehouse_revaluation_entry(entry):
	"""Only a submitted, rate-only warehouse reconciliation may omit a physical location."""
	from erpnext.stock.doctype.inventory_dimension.inventory_dimension import get_inventory_dimensions
	from erpnext.stock.stock_ledger import get_previous_sle_of_current_voucher

	if (
		entry.get("voucher_type") != "Stock Reconciliation"
		or flt(entry.get("actual_qty"))
		or entry.get("serial_and_batch_bundle")
		or entry.get("batch_no")
		or entry.get("serial_no")
		or any(entry.get(d.fieldname) not in (None, "") for d in get_inventory_dimensions())
	):
		return False
	source = frappe.get_doc("Stock Reconciliation", entry.get("voucher_no"))
	row = next((row for row in source.items if row.name == entry.get("voucher_detail_no")), None)
	if (
		source.docstatus != 1
		or source.purpose != "Stock Reconciliation"
		or source.company != entry.get("company")
		or getdate(source.posting_date) != getdate(entry.get("posting_date"))
		or get_time(source.posting_time) != get_time(entry.get("posting_time"))
		or not row
		or row.item_code != entry.get("item_code")
		or row.warehouse != entry.get("warehouse")
		or source.get_inventory_dimensions_dict(row)
		or row.batch_no
		or row.serial_no
		or row.serial_and_batch_bundle
		or frappe.get_cached_value("Item", row.item_code, "has_serial_no")
		or frappe.get_cached_value("Item", row.item_code, "has_batch_no")
	):
		return False
	previous = get_previous_sle_of_current_voucher(
		frappe._dict(
			{
				field: entry.get(field)
				for field in (
					"item_code",
					"warehouse",
					"posting_date",
					"posting_time",
					"voucher_type",
					"voucher_no",
				)
			}
		),
		"<=",
		exclude_current_voucher=True,
	)
	return (
		flt(row.qty) > 0
		and flt(entry.get("qty_after_transaction")) == flt(row.qty)
		and flt(entry.get("valuation_rate")) == flt(row.valuation_rate)
		and flt(row.qty) == flt(previous.get("qty_after_transaction"))
	)


def validate_storage_location(ledger_entry):
	"""Require a location for new ledger postings, while permitting historical reversals."""
	location_name = ledger_entry.get("storage_location")
	if not location_name:
		if is_warehouse_revaluation_entry(ledger_entry):
			return
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
		voucher_type = ledger_entry.get("voucher_type")
		qty = flt(ledger_entry.get("actual_qty"))
		if qty and voucher_type not in ("Stock Entry", "Stock Reconciliation"):
			kind = get_location_type(location_name, ledger_entry.warehouse)
			if kind == LOST or (
				qty < 0
				and kind in HOLD_ZONE_TYPES
				and voucher_type not in ("Purchase Receipt", "Purchase Invoice")
			):
				frappe.throw(
					_("Stock held at {0} cannot be sold, issued, or consumed.").format(location_name)
				)
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


def location_stock(item_code, warehouse, batch_no=None, unlocated=False, exclude_receiving=False):
	"""{Storage Location: qty} of ``item_code`` in ``warehouse`` now (submitted, not cancelled ledger), only locations
	with a balance. With ``batch_no``, the batch's own balance (bundle rows and legacy ledger batch). With
	``unlocated``, the balance that carries no Storage Location (lab stock, legacy balances) instead. With
	``exclude_receiving``, the stock at a Receiving location is left out."""
	if not item_code or not warehouse:
		return {}
	if batch_no:
		bundle_location = "coalesce(nullif(entry.storage_location, ''), sle.storage_location)"
		rows = frappe.db.sql(
			f"""select {bundle_location} location, sum(entry.qty) qty
			from `tabStock Ledger Entry` sle
			join `tabSerial and Batch Entry` entry on entry.parent = sle.serial_and_batch_bundle
			where sle.item_code = %(item)s and sle.warehouse = %(warehouse)s and sle.is_cancelled = 0
				and entry.batch_no = %(batch)s{_not_receiving(bundle_location, exclude_receiving)}
			group by location
			union all
			select sle.storage_location location, sum(sle.actual_qty) qty
			from `tabStock Ledger Entry` sle
			where sle.item_code = %(item)s and sle.warehouse = %(warehouse)s and sle.is_cancelled = 0
				and ifnull(sle.serial_and_batch_bundle, '') = '' and sle.batch_no = %(batch)s
				{_not_receiving("sle.storage_location", exclude_receiving)}
			group by location""",
			{"item": item_code, "warehouse": warehouse, "batch": batch_no},
			as_dict=True,
		)
	else:
		rows = frappe.db.sql(
			f"""select storage_location location, sum(actual_qty) qty
			from `tabStock Ledger Entry`
			where item_code = %(item)s and warehouse = %(warehouse)s and is_cancelled = 0
				{_not_receiving("`tabStock Ledger Entry`.storage_location", exclude_receiving)}
			group by storage_location""",
			{"item": item_code, "warehouse": warehouse},
			as_dict=True,
		)
	totals = {}
	for row in rows:
		if bool(row.location) != bool(unlocated):
			totals[row.location or ""] = totals.get(row.location or "", 0) + flt(row.qty)
	return {location: qty for location, qty in totals.items() if qty > 0}


def _not_receiving(expression, exclude_receiving):
	"""`` and not <receiving>`` for a WHERE when ``exclude_receiving``, else nothing."""
	return f" and not {restricted_location_sql(expression)}" if exclude_receiving else ""


def serial_locations(serial_nos, warehouse, exclude_receiving=False):
	"""{Storage Location: count} where the given serial nos are now in ``warehouse`` (the serial's own location on
	its bundle row, else the ledger row's); with ``exclude_receiving``, not at a Receiving location."""
	serial_nos = [serial for serial in (serial_nos or []) if serial]
	if not serial_nos or not warehouse:
		return {}
	bundle_location = "coalesce(nullif(entry.storage_location, ''), sle.storage_location)"
	rows = frappe.db.sql(
		f"""select entry.serial_no, {bundle_location} location, sum(entry.qty) qty
		from `tabStock Ledger Entry` sle
		join `tabSerial and Batch Entry` entry on entry.parent = sle.serial_and_batch_bundle
		where sle.warehouse = %(warehouse)s and sle.is_cancelled = 0 and entry.serial_no in %(serials)s
			{_not_receiving(bundle_location, exclude_receiving)}
		group by entry.serial_no, location""",
		{"warehouse": warehouse, "serials": tuple(serial_nos)},
		as_dict=True,
	)
	found = {}
	for row in rows:
		if row.location and flt(row.qty) > 0:
			found[row.location] = found.get(row.location, 0) + 1
	return found


def _row_serial_nos(filters):
	"""The serial nos a row names: its Serial No text and/or its Serial and Batch Bundle."""
	serials = [line.strip() for line in cstr(filters.get("serial_no")).splitlines() if line.strip()]
	bundle = filters.get("serial_and_batch_bundle")
	if bundle and frappe.db.exists("Serial and Batch Bundle", bundle):
		serials += [
			serial
			for serial in frappe.get_all(
				"Serial and Batch Entry", filters={"parent": bundle}, pluck="serial_no", order_by="idx"
			)
			if serial
		]
	return list(dict.fromkeys(serials))


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def storage_location_query(doctype, txt, searchfield, start, page_len, filters):
	"""Rank Item preferences before other active locations satisfying the same condition, each with the Item's
	quantity there. A row that names serial nos is offered only the locations holding them; a source field
	(``only_stock``) only the locations holding the Item (and batch) when any does (Connect Diagnostics, 5 Nov
	2026). ``exclude_receiving``: "1" never offers a Receiving location (a source the goods may not leave from);
	"stock" only when ``warehouse`` is a stock warehouse (the target of a transfer); "target" only when the filter
	``target_warehouse`` is one (the source of a transfer to another warehouse)."""
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

	exclude = cstr(filters.get("exclude_receiving"))
	exclude_receiving = (
		exclude == "1"
		or (exclude == "stock" and is_stock_type_warehouse(warehouse))
		or (exclude == "target" and is_stock_type_warehouse(filters.get("target_warehouse")))
	)
	stock = (
		location_stock(item_code, warehouse, filters.get("batch_no"), exclude_receiving=exclude_receiving)
		if item_code
		else {}
	)
	serials = _row_serial_nos(filters)
	holding = serial_locations(serials, warehouse, exclude_receiving=exclude_receiving) if serials else {}
	candidates = frappe.db.sql(
		"""select `tabStorage Location`.name, `tabStorage Location`.location_code,
			`tabStorage Location`.location_type,
			case when preference.idx is null then 0 else 1 end preferred, preference.idx
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
			{match_cond}""".format(match_cond=get_match_cond("Storage Location")),
		{
			"warehouse": warehouse,
			"item_code": item_code or "",
			"condition": condition or "",
			"txt": f"%{txt}%",
		},
		as_dict=True,
	)
	if exclude_receiving:
		excluded_types = (RECEIVING,) if exclude == "stock" else (RECEIVING, *HOLD_ZONE_TYPES)
		candidates = [row for row in candidates if row.location_type not in excluded_types]
	location_types = filters.get("location_types")
	if isinstance(location_types, list):
		candidates = [row for row in candidates if row.location_type in location_types]
	if filters.get("location_name"):
		candidates = [row for row in candidates if row.name == filters["location_name"]]
	if serials:
		candidates = [
			row
			for row in candidates
			if holding.get(row.name, 0) >= (len(set(serials)) if cint(filters.get("require_stock")) else 1)
		]
	elif cint(filters.get("require_stock")) or (cint(filters.get("only_stock")) and stock):
		candidates = [row for row in candidates if row.name in stock]
	candidates.sort(key=lambda row: (-row.preferred, -stock.get(row.name, 0), row.idx or 0, row.name))
	start, page_len = cint(start), cint(page_len) or 20
	return [
		(
			row.name,
			row.location_code or "",
			_("Qty {0}").format(frappe.format(stock.get(row.name, 0), {"fieldtype": "Float"}))
			if item_code
			else "",
			_("Preferred for Item") if row.preferred else "",
		)
		for row in candidates[start : start + page_len]
	]


@frappe.whitelist()
@frappe.validate_and_sanitize_search_inputs
def warehouse_query(doctype, txt, searchfield, start, page_len, filters):
	"""Warehouses for an item row: only those with a location that meets the Item's Storage Condition."""
	from frappe.desk.search import search_widget

	filters = frappe.parse_json(filters) or {}
	item_code = filters.get("storage_condition_item")
	require_stock = cint(filters.get("require_stock"))
	base = filters.get("filters") or {}
	location_types = filters.get("location_types")
	location_types = location_types if isinstance(location_types, list) else []
	condition = None
	if item_code and frappe.db.exists("Item", item_code):
		item = frappe.get_cached_doc("Item", item_code)
		if frappe.has_permission("Item", "select", doc=item):
			condition = item.get("storage_condition")
		elif require_stock:
			return []
	elif require_stock:
		return []
	if condition or location_types or require_stock:
		if isinstance(base, dict):
			base = [
				["Warehouse", key, *(value if isinstance(value, list | tuple) else ["=", value])]
				for key, value in base.items()
			]
	if condition or location_types:
		warehouses = frappe.db.sql_list(
			"""select distinct location.warehouse from `tabStorage Location` location
			where location.disabled = 0
				and (%(restrict_types)s = 0 or location.location_type in %(location_types)s)
				and (%(condition)s = '' or exists (
					select 1 from `tabStorage Location Condition` supported
					where supported.parent = location.name and supported.parenttype = 'Storage Location'
						and supported.parentfield = 'supported_storage_conditions'
						and supported.storage_condition = %(condition)s
				))""",
			{
				"condition": condition or "",
				"restrict_types": bool(location_types),
				"location_types": location_types or [""],
			},
		)
		base = [*base, ["Warehouse", "name", "in", warehouses or [""]]]
	if require_stock:
		stocked = frappe.get_all(
			"Bin", filters={"item_code": item_code, "actual_qty": [">", 0]}, pluck="warehouse"
		)
		base = [*base, ["Warehouse", "name", "in", stocked or [""]]]
	return search_widget(
		"Warehouse", txt, searchfield=searchfield, start=start, page_length=page_len, filters=base
	)
