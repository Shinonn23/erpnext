# Copyright (c) 2022, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _, bold, scrub
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields
from frappe.model.document import Document
from frappe.utils import cint
from frappe.utils.caching import request_cache


class DoNotChangeError(frappe.ValidationError):
	pass


class CanNotBeChildDoc(frappe.ValidationError):
	pass


class CanNotBeDefaultDimension(frappe.ValidationError):
	pass


class InventoryDimension(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		apply_to_all_doctypes: DF.Check
		condition: DF.Code | None
		dimension_name: DF.Data
		document_type: DF.Link | None
		fetch_from_parent: DF.Literal[None]
		istable: DF.Check
		mandatory_depends_on_backend: DF.SmallText | None
		reference_document: DF.Link
		reqd: DF.Check
		source_fieldname: DF.Data | None
		target_fieldname: DF.Data | None
		type_of_transaction: DF.Literal["", "Inward", "Outward", "Both"]
		validate_negative_stock: DF.Check
	# end: auto-generated types

	def onload(self):
		if not self.is_new() and frappe.db.has_column("Stock Ledger Entry", self.target_fieldname):
			self.set_onload("has_stock_ledger", self.has_stock_ledger())

	def has_stock_ledger(self) -> str:
		if not self.target_fieldname:
			return

		return frappe.get_all(
			"Stock Ledger Entry", filters={self.target_fieldname: ("is", "set"), "is_cancelled": 0}, limit=1
		)

	def validate(self):
		if (
			self.dimension_name == "Storage Location"
			or self.source_fieldname == "storage_location"
			or self.target_fieldname == "storage_location"
		) and not self.flags.get("setup_storage_location_dimension"):
			frappe.throw(
				_("Storage Location is managed directly and cannot be configured as an Inventory Dimension.")
			)

		self.validate_builtin_storage_location()
		self.validate_reference_document()

	def validate_builtin_storage_location(self):
		if self.is_new() or self.name != "Storage Location":
			return

		# Negative stock validation is a site-level setting. The built-in dimension's identity and
		# applicability must stay fixed so it cannot silently disappear from stock transactions.
		for field in frappe.get_meta("Inventory Dimension").fields:
			if field.fieldname == "validate_negative_stock":
				continue

			if self.get(field.fieldname) != frappe.db.get_value(
				"Inventory Dimension", self.name, field.fieldname
			):
				frappe.throw(
					_("Storage Location is a built-in Inventory Dimension and cannot be reconfigured.")
				)

	def before_save(self):
		self.do_not_update_document()
		self.reset_value()
		self.set_source_and_target_fieldname()
		self.set_type_of_transaction()

	def set_type_of_transaction(self):
		if self.apply_to_all_doctypes:
			self.type_of_transaction = "Both"

	def do_not_update_document(self):
		if self.is_new() or not self.has_stock_ledger():
			return

		old_doc = self._doc_before_save
		allow_to_edit_fields = [
			"fetch_from_parent",
			"type_of_transaction",
			"condition",
			"validate_negative_stock",
		]

		for field in frappe.get_meta("Inventory Dimension").fields:
			if field.fieldname not in allow_to_edit_fields and old_doc.get(field.fieldname) != self.get(
				field.fieldname
			):
				msg = f"""The user can not change value of the field {bold(field.label)} because
					stock transactions exists against the dimension {bold(self.name)}."""

				frappe.throw(_(msg), DoNotChangeError)

	def on_trash(self):
		if self.dimension_name == "Storage Location":
			frappe.throw(_("Storage Location is a built-in Inventory Dimension and cannot be deleted."))
		self.delete_custom_fields()

	def delete_custom_fields(self):
		filters = {
			"fieldname": (
				"in",
				[
					self.source_fieldname,
					f"to_{self.source_fieldname}",
					f"from_{self.source_fieldname}",
					f"rejected_{self.source_fieldname}",
				],
			)
		}

		if self.document_type:
			filters["dt"] = self.document_type

		for field in frappe.get_all("Custom Field", filters=filters):
			frappe.delete_doc("Custom Field", field.name)

		msg = f"Deleted custom fields related to the dimension {self.name}"
		frappe.msgprint(_(msg))

	def reset_value(self):
		if self.apply_to_all_doctypes:
			self.type_of_transaction = ""

			self.istable = 0
			for field in ["document_type", "condition"]:
				self.set(field, None)

	def validate_reference_document(self):
		if frappe.get_cached_value("DocType", self.reference_document, "istable") == 1:
			msg = f"The reference document {self.reference_document} can not be child table."
			frappe.throw(_(msg), CanNotBeChildDoc)

		if self.reference_document in ["Batch", "Serial No", "Warehouse", "Item"] or (
			self.reference_document == "Storage Location"
			and not self.flags.get("setup_storage_location_dimension")
		):
			msg = f"The reference document {self.reference_document} can not be an Inventory Dimension."
			frappe.throw(_(msg), CanNotBeDefaultDimension)

	def set_source_and_target_fieldname(self) -> None:
		if not self.source_fieldname:
			self.source_fieldname = scrub(self.dimension_name)

		if not self.target_fieldname:
			self.target_fieldname = scrub(self.dimension_name)

	def on_update(self):
		self.add_custom_fields()

	def get_dimension_fields(self, doctype=None):
		if not doctype:
			doctype = self.document_type

		label_start_with = ""
		if doctype in ["Purchase Invoice Item", "Purchase Receipt Item"]:
			label_start_with = "Target"
		elif doctype in [
			"Sales Invoice Item",
			"Delivery Note Item",
			"Packed Item",
			"Stock Entry Detail",
			"Stock to Asset Conversion Item",
		]:
			label_start_with = "Source"

		label = self.dimension_name
		if label_start_with:
			label = f"{label_start_with} {self.dimension_name}"

		# Note: `reqd` is intentionally NOT set on the custom fields. Mandatory enforcement
		# happens on the server side via StockController.validate_inventory_dimension_mandatory()
		# so that it can be gated (e.g. skip service rows) and never blocks documents that are
		# being cancelled.

		insert_after = "inventory_dimension"
		if doctype in ["Purchase Invoice Item", "Purchase Receipt Item"]:
			insert_after = "inventory_dimension_col_break"

		dimension_fields = [
			dict(
				fieldname=self.source_fieldname,
				fieldtype="Link",
				insert_after=insert_after,
				options=self.reference_document,
				label=_(label),
				depends_on="eval:doc.s_warehouse" if doctype == "Stock Entry Detail" else "",
				search_index=1,
			),
		]

		if doctype in ["Purchase Invoice Item", "Purchase Receipt Item"]:
			dimension_fields.append(
				dict(
					fieldname="rejected_" + self.source_fieldname,
					fieldtype="Link",
					insert_after="inventory_dimension",
					options=self.reference_document,
					label=_("Rejected " + self.dimension_name),
					search_index=1,
				)
			)

		return dimension_fields

	def add_custom_fields(self):
		custom_fields = {}

		dimension_fields = []
		if self.apply_to_all_doctypes:
			for (doctype,) in get_inventory_documents():
				dimension_fields = self.get_dimension_fields(doctype)
				self.add_transfer_field(doctype, dimension_fields)
				custom_fields.setdefault(doctype, dimension_fields)
		else:
			dimension_fields = self.get_dimension_fields()

			self.add_transfer_field(self.document_type, dimension_fields)
			custom_fields.setdefault(self.document_type, dimension_fields)

		for dt in ["Stock Ledger Entry", "Stock Closing Balance"]:
			if dimension_fields:
				ledger_fields = self.get_dimension_fields(dt)
				ledger_dimension_field = ledger_fields[0].copy()
				ledger_dimension_field["fieldname"] = self.target_fieldname
				custom_fields[dt] = [ledger_fields[0], ledger_dimension_field]

		filter_custom_fields = {}
		if custom_fields:
			for doctype, fields in custom_fields.items():
				if isinstance(fields, dict):
					fields = [fields]

				for field in fields:
					if field.get("fieldtype") == "Link" and (
						doctype
						in (
							"Stock Count Item",
							"Stock Entry Detail",
							"Stock Reconciliation Item",
							"Pick List Item",
						)
					):
						field["in_list_view"] = 1
						custom_field = frappe.db.get_value(
							"Custom Field", {"dt": doctype, "fieldname": field["fieldname"]}, "name"
						)
						if custom_field:
							frappe.db.set_value(
								"Custom Field", custom_field, "in_list_view", 1, update_modified=False
							)
							frappe.clear_cache(doctype=doctype)

					if not field_exists(doctype, field["fieldname"]):
						filter_custom_fields.setdefault(doctype, []).append(field)

		create_custom_fields(filter_custom_fields)

	def add_transfer_field(self, doctype, dimension_fields):
		if doctype not in [
			"Stock Entry Detail",
			"Sales Invoice Item",
			"Delivery Note Item",
			"Packed Item",
			"Purchase Invoice Item",
			"Purchase Receipt Item",
		]:
			return

		fieldname_start_with = "to"
		label_start_with = "Target"
		display_depends_on = ""

		if doctype in ["Purchase Invoice Item", "Purchase Receipt Item"]:
			fieldname_start_with = "from"
			label_start_with = "Source"
			display_depends_on = "eval:parent.is_internal_supplier == 1"
		elif doctype == "Packed Item":
			display_depends_on = "eval:doc.target_warehouse"
		elif doctype != "Stock Entry Detail":
			display_depends_on = "eval:parent.is_internal_customer == 1"
		elif doctype == "Stock Entry Detail":
			display_depends_on = "eval:doc.t_warehouse"

		fieldname = f"{fieldname_start_with}_{self.source_fieldname}"
		label = f"{label_start_with} {self.dimension_name}"
		if field_exists(doctype, fieldname):
			return

		insert_after = "inventory_dimension_col_break"
		if doctype in ["Purchase Invoice Item", "Purchase Receipt Item"]:
			insert_after = "inventory_dimension"

		dimension_fields.append(
			dict(
				fieldname=fieldname,
				fieldtype="Link",
				insert_after=insert_after,
				options=self.reference_document,
				label=label,
				depends_on=display_depends_on,
			)
		)


def field_exists(doctype, fieldname) -> str | None:
	return frappe.db.get_value("DocField", {"parent": doctype, "fieldname": fieldname}, "name")


@frappe.whitelist()
def get_inventory_documents(
	doctype=None, txt=None, searchfield=None, start=None, page_len=None, filters=None
):
	doctypes = list(dict.fromkeys(frappe.get_hooks("inventory_dimension_doctypes")))
	if txt:
		doctypes = [name for name in doctypes if txt.casefold() in name.casefold()]

	start = cint(start)
	page_len = cint(page_len) or len(doctypes)
	return [[name] for name in doctypes[start : start + page_len]]


def get_evaluated_inventory_dimension(doc, sl_dict, parent_doc=None):
	dimensions = get_document_wise_inventory_dimensions(doc.doctype)
	filter_dimensions = []
	for row in dimensions:
		if row.type_of_transaction and row.type_of_transaction != "Both":
			if (
				row.type_of_transaction == "Inward"
				if doc.docstatus == 1
				else row.type_of_transaction != "Inward"
			) and sl_dict.actual_qty < 0:
				continue
			elif (
				row.type_of_transaction == "Outward"
				if doc.docstatus == 1
				else row.type_of_transaction != "Outward"
			) and sl_dict.actual_qty > 0:
				continue

		evals = {"doc": doc}
		if parent_doc:
			evals["parent"] = parent_doc

		if row.condition and frappe.safe_eval(row.condition, evals):
			filter_dimensions.append(row)
		else:
			filter_dimensions.append(row)

	return filter_dimensions


@request_cache
def get_document_wise_inventory_dimensions(doctype) -> dict:
	return frappe.get_all(
		"Inventory Dimension",
		fields=[
			"name",
			"source_fieldname",
			"condition",
			"target_fieldname",
			"type_of_transaction",
			"fetch_from_parent",
		],
		or_filters={"document_type": doctype, "apply_to_all_doctypes": 1},
	)


@request_cache
def get_mandatory_inventory_dimensions(doctype) -> list:
	"""Return the inventory dimensions applicable to `doctype` (a child doctype such as
	`Stock Entry Detail`) that need server-side mandatory enforcement.

	A dimension qualifies only if it is configured as mandatory (`reqd`) or has a server-side
	mandatory condition (`mandatory_depends_on_backend`). Non-mandatory dimensions are never
	enforced, including the rejected dimension field on purchase rows."""
	dimensions = frappe.get_all(
		"Inventory Dimension",
		fields=[
			"name",
			"dimension_name",
			"source_fieldname",
			"reqd",
			"mandatory_depends_on_backend",
		],
		or_filters={"document_type": doctype, "apply_to_all_doctypes": 1},
	)

	return [d for d in dimensions if d.reqd or d.mandatory_depends_on_backend]


def get_mandatory_dimension_fields(doctype, dimension) -> list:
	"""For a mandatory `dimension` return the list of (fieldname, condition) tuples that must be
	filled on a row of `doctype`. `condition` is a python expression evaluated with `doc` (the row)
	and `parent`; a `None` condition means the field is unconditionally mandatory.

	Mirrors the mandatory logic that used to live on the custom fields in `get_dimension_fields`."""
	fields = []
	source_fieldname = dimension.source_fieldname
	# `mandatory_depends_on_backend` is a raw python expression evaluated server-side
	# (with `doc` and `parent`), so it can be used as a condition directly.
	backend_condition = (dimension.mandatory_depends_on_backend or "").strip() or None

	# Primary source dimension field
	if dimension.reqd and doctype == "Stock Entry Detail":
		fields.append((source_fieldname, "doc.s_warehouse"))
	elif dimension.reqd and doctype == "Subcontracting Receipt Supplied Item":
		fields.append((source_fieldname, "doc.reference_name"))
	elif dimension.reqd and doctype == "Packed Item":
		fields.append(
			(
				source_fieldname,
				"doc.parent_detail_docname and parent.doctype in ['Delivery Note', 'Sales Invoice', 'POS Invoice']",
			)
		)
	elif dimension.reqd:
		fields.append((source_fieldname, None))
	elif backend_condition:
		fields.append((source_fieldname, backend_condition))

	# Rejected dimension field (only present on purchase rows). Enforced only when the dimension
	# is mandatory for the row AND there is a rejected quantity.
	if doctype in ["Purchase Invoice Item", "Purchase Receipt Item"]:
		if dimension.reqd:
			fields.append((f"rejected_{source_fieldname}", "doc.rejected_qty > 0"))
		elif backend_condition:
			fields.append((f"rejected_{source_fieldname}", f"({backend_condition}) and doc.rejected_qty > 0"))

	# Target/transfer dimension field used for internal transfers (mirrors the old
	# `add_transfer_field` behaviour). When the dimension is `reqd` the field inherits the
	# transfer display condition, otherwise it inherits the server-side mandatory condition.
	if (dimension.reqd or backend_condition) and doctype in [
		"Stock Entry Detail",
		"Sales Invoice Item",
		"Delivery Note Item",
		"Purchase Invoice Item",
		"Purchase Receipt Item",
	]:
		if doctype in ["Purchase Invoice Item", "Purchase Receipt Item"]:
			transfer_fieldname, display_condition = (
				f"from_{source_fieldname}",
				"parent.is_internal_supplier == 1",
			)
		elif doctype == "Stock Entry Detail":
			transfer_fieldname, display_condition = f"to_{source_fieldname}", "doc.t_warehouse"
		else:
			transfer_fieldname, display_condition = (
				f"to_{source_fieldname}",
				"parent.is_internal_customer == 1",
			)

		# The transfer field only applies to internal transfers, so its mandatory check is always
		# gated on the display condition; the backend condition narrows it further.
		if dimension.reqd:
			transfer_condition = display_condition
		else:
			transfer_condition = f"({display_condition}) and ({backend_condition})"

		fields.append((transfer_fieldname, transfer_condition))

	return fields


@frappe.whitelist()
@request_cache
def get_inventory_dimensions():
	return frappe.get_all(
		"Inventory Dimension",
		fields=[
			"target_fieldname as fieldname",
			"source_fieldname",
			"reference_document as doctype",
			"validate_negative_stock",
			"name as dimension_name",
		],
		order_by="creation",
		distinct=True,
	)


@frappe.whitelist()
def delete_dimension(dimension):
	doc = frappe.get_doc("Inventory Dimension", dimension)
	doc.delete()


@frappe.whitelist()
def get_parent_fields(child_doctype, dimension_name):
	parent_doctypes = frappe.get_all("DocField", fields=["parent"], filters={"options": child_doctype})

	fields = []

	fields.extend(
		frappe.get_all(
			"DocField",
			fields=["fieldname as value", "label"],
			filters={"options": dimension_name, "parent": ("in", [d.parent for d in parent_doctypes])},
		)
	)

	fields.extend(
		frappe.get_all(
			"Custom Field",
			fields=["fieldname as value", "label"],
			filters={"options": dimension_name, "dt": ("in", [d.parent for d in parent_doctypes])},
		)
	)

	return fields
