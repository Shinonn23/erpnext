import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields

from erpnext.regional.thailand.constants import ERP_CUSTOM_FIELDS, HRMS_CUSTOM_FIELDS

LINK_FIELDS = ("tax_invoice_ref_type", "tax_invoice_ref")


def execute():
	"""Thailand custom fields are made at install only: add the Tax Invoice link fields (voucher headers)
	to sites that already have the Thailand set. Fields already there are left as they are."""
	if not frappe.db.exists("Custom Field", "Sales Invoice-tax_invoice_number"):
		return
	custom_fields = {}
	for fields_by_doctype in (ERP_CUSTOM_FIELDS, HRMS_CUSTOM_FIELDS):
		for doctype, fields in fields_by_doctype.items():
			if doctype == "Expense Claim" and "hrms" not in frappe.get_installed_apps():
				continue
			new = [
				f
				for f in fields
				if f["fieldname"] in LINK_FIELDS
				and not frappe.db.exists("Custom Field", {"dt": doctype, "fieldname": f["fieldname"]})
			]
			if new:
				custom_fields[doctype] = new
	if custom_fields:
		create_custom_fields(custom_fields, ignore_validate=True)
