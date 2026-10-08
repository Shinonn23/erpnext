import frappe


def execute():
	"""Refresh shared DocType schemas; US-specific field setup was removed."""
	frappe.reload_doc("accounts", "doctype", "allowed_to_transact_with", force=True)
	frappe.reload_doc("accounts", "doctype", "pricing_rule_detail", force=True)
	frappe.reload_doc("crm", "doctype", "lost_reason_detail", force=True)
	frappe.reload_doc("setup", "doctype", "quotation_lost_reason_detail", force=True)
