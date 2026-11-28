import frappe


def get_policy(name):
	"""Resolve an installed site's optional quarantine policy."""
	methods = (frappe.get_hooks("stock_quarantine_policies") or {}).get(name, [])
	return frappe.get_attr(methods[-1]) if methods else None
