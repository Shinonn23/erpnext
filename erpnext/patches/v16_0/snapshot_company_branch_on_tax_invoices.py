import frappe

from erpnext.regional.thailand.custom.branches import (
	get_company_addresses,
	get_company_branch_snapshot,
)

HEAD_OFFICE = "00000"


def is_head_office(company, address, cache):
	"""A blank branch code counts as the head office only for the company's primary address or when the
	company has a single address."""
	if company not in cache:
		cache[company] = [a.name for a in get_company_addresses(company)]
	names = cache[company]
	if len(names) == 1:
		return names[0] == address
	return bool(frappe.db.get_value("Address", address, "is_primary_address"))


def execute():
	"""Freeze our branch code and tax id on the submitted and cancelled tax invoices (from their company
	tax address, as it is today). The tax id is always filled; a blank branch code on the address is taken
	as the head office only when that is certain (see is_head_office), else left empty. Rows already
	complete are left alone."""
	cache = {}
	for doctype in ("Sales Tax Invoice", "Purchase Tax Invoice"):
		if not frappe.db.has_column(doctype, "company_branch_code"):
			continue
		invoices = frappe.get_all(
			doctype,
			filters={"docstatus": ["in", [1, 2]]},
			or_filters={"company_branch_code": ["is", "not set"], "company_tax_id": ["is", "not set"]},
			fields=["name", "company", "company_tax_address", "company_branch_code", "company_tax_id"],
		)
		for invoice in invoices:
			if not invoice.company_tax_address:
				continue
			default = (
				HEAD_OFFICE if is_head_office(invoice.company, invoice.company_tax_address, cache) else ""
			)
			snapshot = get_company_branch_snapshot(invoice.company, invoice.company_tax_address, default)
			snapshot = {k: v for k, v in snapshot.items() if v and not invoice.get(k)}
			if snapshot:
				frappe.db.set_value(doctype, invoice.name, snapshot, update_modified=False)
