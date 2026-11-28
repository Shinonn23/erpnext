"""Resolve site policies for stock reports which query beyond a permission-filtered parent."""

import frappe


def _providers(policy):
	return frappe.get_hooks("stock_report_policies", {}).get(policy, ())


def _scope(policy, doctype):
	providers = _providers(policy)
	if not providers:
		if not frappe.has_permission(doctype, "read"):
			return set()
		return set(frappe.get_list(doctype, pluck="name", limit_page_length=0))

	allowed = None
	for path in providers:
		values = frappe.get_attr(path)()
		if values is not None:
			if not isinstance(values, (list, tuple, set, frozenset)):
				raise TypeError(f"{path} must return a collection of names or None")
			values = set(values)
			allowed = values if allowed is None else allowed & values
	return allowed


def allowed_warehouses():
	return _scope("warehouses", "Warehouse")


def allowed_groups():
	return _scope("item_groups", "Item Group")


def _allows_values(policy):
	providers = _providers(policy)
	return bool(providers) and all(frappe.get_attr(path)() is True for path in providers)


def has_stock_value_access():
	return _allows_values("stock_values")


def has_purchase_price_access():
	return _allows_values("purchase_prices")


def freight_item_code():
	items = {frappe.get_attr(path)() for path in _providers("freight_item")}
	if len(items) == 1:
		item = items.pop()
		return item if isinstance(item, str) and item else None
	return None
