import json
from pathlib import Path

import frappe


def ensure_company_tax_settings(company):
	"""Create the Thai Tax Settings row required when test vouchers post GL Entries."""
	settings = frappe.get_single("Thai Tax Settings")
	if any(row.company == company for row in settings.company_accounts):
		return

	settings.append("company_accounts", {"company": company})
	settings.flags.ignore_links = True
	settings.save(ignore_permissions=True)


def before_tests():
	"""Add a Thai Tax Settings row for each ERPNext test company.

	This app's document hooks require a Thai Tax Settings row for the company.
	ERPNext creates its test records (e.g. Sales Invoices of "_Test Company")
	while this app's tests run, so give each test company a row without tax accounts.
	"""
	for company in frappe.get_test_records("Company"):
		ensure_company_tax_settings(company["company_name"])
	# Test companies are created later, together with the test records
	frappe.db.commit()


def doctype_fields(doctype):
	"""{fieldname: DocField} and the field_order of the DocType JSON of ERPNext that holds the Thailand fields (they are
	no longer Custom Fields made at install; Siwat, 8 Nov 2026)."""
	scrubbed = frappe.scrub(doctype)
	path = next(Path(__file__).parents[3].glob(f"*/doctype/{scrubbed}/{scrubbed}.json"))
	document = json.loads(path.read_text(encoding="utf-8"))
	return {row["fieldname"]: row for row in document["fields"]}, document["field_order"]
