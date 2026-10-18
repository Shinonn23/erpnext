# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _, bold
from frappe.utils import flt

RESTRICT_SETTING = "allow_uom_with_conversion_rate_defined_in_item"


def is_item_uom_restricted():
	return bool(frappe.db.get_single_value("Stock Settings", RESTRICT_SETTING))


def get_item_uom_fields(doctype):
	"""(UOM field, Item field) pairs of a DocType whose UOM belongs to an Item.

	`uom` belongs to the row's Item; `<prefix>_uom` to `<prefix>` or `<prefix>_code`, so `stock_uom`,
	`weight_uom` and `pack_uom` stay free. Keep in step with erpnext/public/js/item_uom.js.
	"""
	meta = frappe.get_meta(doctype)
	item_fields = {df.fieldname for df in meta.fields if df.fieldtype == "Link" and df.options == "Item"}
	pairs = []
	for df in meta.fields:
		if df.fieldtype != "Link" or df.options != "UOM":
			continue
		if df.fieldname == "uom":
			candidates = ("item_code", "item", "production_item")
		elif df.fieldname.endswith("_uom"):
			prefix = df.fieldname[: -len("_uom")]
			candidates = (prefix, f"{prefix}_code")
		else:
			continue
		item_field = next((fieldname for fieldname in candidates if fieldname in item_fields), None)
		if item_field:
			pairs.append((df.fieldname, item_field))
	return pairs


def get_item_uoms(item_code):
	"""{UOM: conversion factor} an Item converts: its stock UOM, its UOMs table, then its template's,
	in the order get_conversion_factor reads them."""
	if not frappe.get_cached_value("Item", item_code, "name"):
		return {}

	item = frappe.get_cached_doc("Item", item_code)
	uoms = {item.stock_uom: 1.0} if item.stock_uom else {}
	for source in filter(None, (item, item.variant_of and frappe.get_cached_doc("Item", item.variant_of))):
		for row in source.get("uoms"):
			uoms.setdefault(row.uom, flt(row.conversion_factor))
	return uoms


def validate_item_uoms(doc, method=None):
	"""A UOM without a conversion factor in its Item would silently be converted at 1, so reject it."""
	if not is_item_uom_restricted():
		return

	allowed, fields = {}, {}
	for row in [doc, *doc.get_all_children()]:
		if row.doctype not in fields:
			fields[row.doctype] = get_item_uom_fields(row.doctype)
		for uom_field, item_field in fields[row.doctype]:
			uom, item_code = row.get(uom_field), row.get(item_field)
			if not uom or not item_code:
				continue
			if item_code not in allowed:
				allowed[item_code] = get_item_uoms(item_code)
			# A missing Item is reported by link validation.
			if allowed[item_code] and uom not in allowed[item_code]:
				message = _(
					"{0} {1} has no conversion factor in Item {2}. Add it to the Item's UOMs table first."
				).format(_(row.meta.get_label(uom_field)), bold(uom), bold(item_code))
				if row is not doc:
					message = _("{0} Row #{1}: {2}").format(
						_(doc.meta.get_label(row.parentfield)), row.idx, message
					)
				frappe.throw(message, title=_("UOM Not Defined for Item"))


def validate_item_default_uoms(item):
	"""The Item's own default and barcode UOMs must be ones its UOMs table (or its template's) converts."""
	if not is_item_uom_restricted():
		return

	allowed = {item.stock_uom, *(row.uom for row in item.get("uoms"))}
	if item.variant_of:
		allowed.update(get_item_uoms(item.variant_of))

	# A new stock UOM empties the UOMs table, so defaults it no longer converts are cleared, not rejected.
	stock_uom_changed = not item.is_new() and item.has_value_changed("stock_uom")
	cleared = []
	for fieldname in ("purchase_uom", "sales_uom"):
		uom = item.get(fieldname)
		if uom and uom not in allowed:
			if stock_uom_changed:
				cleared.append(uom)
				item.set(fieldname, None)
				continue
			frappe.throw(
				_("{0} {1} has no conversion factor. Add it to the UOMs table first.").format(
					_(item.meta.get_label(fieldname)), bold(uom)
				),
				title=_("UOM Not Defined for Item"),
			)
	for row in item.get("barcodes"):
		if row.uom and row.uom not in allowed:
			if stock_uom_changed:
				cleared.append(row.uom)
				row.uom = None
				continue
			frappe.throw(
				_(
					"Barcodes row #{0}: UOM {1} has no conversion factor. Add it to the UOMs table first."
				).format(row.idx, bold(row.uom)),
				title=_("UOM Not Defined for Item"),
			)
	if cleared:
		frappe.msgprint(
			_("Cleared {0}, which the new stock UOM has no conversion factor for.").format(
				", ".join(bold(uom) for uom in sorted(set(cleared)))
			),
			alert=True,
		)
