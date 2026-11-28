"""Effective-date changes of an Item Price series."""

import frappe
from frappe.utils import add_days, getdate


def _general_price_scope(price_list, item_code, uom):
	stock_uom = frappe.get_cached_value("Item", item_code, "stock_uom")
	condition = """item_code = %(item_code)s and price_list = %(price_list)s and ifnull(uom, '') in %(uoms)s
		and ifnull(customer, '') = '' and ifnull(supplier, '') = '' and ifnull(batch_no, '') = ''
		and ifnull(packing_unit, 0) = 0"""
	values = {"item_code": item_code, "price_list": price_list, "uoms": (uom, "") if uom == stock_uom else (uom,)}
	return condition, values


def get_current_general_price(price_list, item_code, uom, on_date, *, for_update=False):
	"""Find a general price active on the given date, including legacy stock-UOM prices."""
	condition, values = _general_price_scope(price_list, item_code, uom)
	rows = frappe.db.sql(
		f"""select name, price_list_rate, valid_from, valid_upto from `tabItem Price`
		where {condition} and ifnull(valid_from, '2000-01-01') <= %(on_date)s
		and ifnull(valid_upto, '2500-12-31') >= %(on_date)s
		order by valid_from desc, uom desc limit 1{' for update' if for_update else ''}""",
		{**values, "on_date": getdate(on_date)},
		as_dict=True,
	)
	return rows[0] if rows else None


def _next_general_price_date(price_list, item_code, uom, start):
	condition, values = _general_price_scope(price_list, item_code, uom)
	rows = frappe.db.sql(
		f"""select valid_from from `tabItem Price`
		where {condition} and valid_from > %(start)s order by valid_from limit 1 for update""",
		{**values, "start": start},
		pluck=True,
	)
	return rows[0] if rows else None


def upsert_scheduled_item_price(
	price_list, item_code, uom, supplier, rate, start, note, *, include_general_history=False, ignore_permissions=False
) -> str:
	"""Write a price from ``start`` without changing the next price's effective date.

	By default the series is the exact UOM and supplier (with no customer or batch). General history includes
	legacy undated prices and blank-UOM prices for the stock UOM, and excludes packing-unit prices.
	"""
	start = getdate(start)
	savepoint = "upsert_item_price"
	frappe.db.savepoint(savepoint)
	try:
		# Even an empty series needs a shared lock so two scheduled writes cannot create competing rows.
		frappe.db.get_value("Price List", price_list, "name", for_update=True)
		price_name = _upsert(
			price_list, item_code, uom, supplier, rate, start, note, include_general_history, ignore_permissions
		)
	except Exception:
		frappe.db.rollback(save_point=savepoint)
		raise
	else:
		frappe.db.release_savepoint(savepoint)
		return price_name


def _upsert(price_list, item_code, uom, supplier, rate, start, note, include_general_history, ignore_permissions):
	if include_general_history:
		if supplier:
			raise ValueError("General Item Price history cannot be used for a supplier-specific price")
		current = get_current_general_price(price_list, item_code, uom, start, for_update=True)
		dated = current.name if current and current.valid_from and getdate(current.valid_from) == start else None
	else:
		identity = {
			"item_code": item_code,
			"price_list": price_list,
			"uom": uom,
			"supplier": supplier or ["is", "not set"],
			"customer": ["is", "not set"],
			"batch_no": ["is", "not set"],
		}
		dated = frappe.db.get_value("Item Price", {**identity, "valid_from": start}, "name", for_update=True)
		current = None
		if not dated:
			previous = frappe.db.get_value(
				"Item Price",
				{**identity, "valid_from": ["<", start]},
				["name", "valid_upto"],
				as_dict=True,
				order_by="valid_from desc",
				for_update=True,
			)
			if previous and (not previous.valid_upto or getdate(previous.valid_upto) >= start):
				current = previous

	if dated:
		price = frappe.get_doc("Item Price", dated, for_update=True)
		price.update({"price_list_rate": rate, "note": note})
		price.save(ignore_permissions=ignore_permissions)
		return price.name

	if current:
		valid_upto = add_days(start, -1)
		if include_general_history or not ignore_permissions:
			price = frappe.get_doc("Item Price", current.name, for_update=True)
			price.valid_upto = valid_upto
			price.save(ignore_permissions=ignore_permissions)
		else:
			# Existing procurement prices can carry legacy data that no longer passes form validation.
			frappe.db.set_value("Item Price", current.name, "valid_upto", valid_upto)

	if include_general_history:
		following = _next_general_price_date(price_list, item_code, uom, start)
	else:
		following = frappe.db.get_value(
			"Item Price",
			{**identity, "valid_from": [">", start]},
			"valid_from",
			order_by="valid_from asc",
			for_update=True,
		)
	return frappe.get_doc(
		{
			"doctype": "Item Price",
			"price_list": price_list,
			"item_code": item_code,
			"uom": uom,
			"supplier": supplier or None,
			"price_list_rate": rate,
			"valid_from": start,
			"valid_upto": add_days(following, -1) if following else None,
			"note": note,
		}
	).insert(ignore_permissions=ignore_permissions).name
