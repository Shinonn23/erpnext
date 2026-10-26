"""Company branches: each branch is a company Address with a 5-digit branch code, and VAT is filed per branch.

The selling documents (Quotation, Sales Order, Delivery Note, Sales Invoice) carry the branch in the standard
`company_address` field. A company with a single company address is left as it is today.

Known limitations (owner decision pending, not handled here): POS Invoices and Custody Loan documents do not
go through `validate_company_branch`, so their branch is not checked against the company's branches.
"""

import re

import frappe
from frappe import _

SOURCE_FIELDS = {
	# doctype -> (child item field, source doctype) pairs
	"Sales Invoice": (("sales_order", "Sales Order"), ("delivery_note", "Delivery Note")),
	"Delivery Note": (
		("against_sales_order", "Sales Order"),
		("against_sales_invoice", "Sales Invoice"),
	),
}


def normalise_branch_code(code):
	"""'0' -> '00000'; blank stays blank."""
	code = (code or "").strip()
	return code.zfill(5) if code.isdigit() else code


def is_valid_branch_code(code):
	return bool(re.fullmatch(r"\d{5}", normalise_branch_code(code)))


def get_company_addresses(company):
	"""The enabled addresses of the company itself (its branches), by name."""
	return frappe.db.get_all(
		"Address",
		filters=[
			["is_your_company_address", "=", 1],
			["disabled", "=", 0],
			["Dynamic Link", "link_doctype", "=", "Company"],
			["Dynamic Link", "link_name", "=", company],
		],
		fields=["name", "branch_code"],
		distinct=True,
		order_by="name",
	)


def get_company_branch_snapshot(company, address, default_branch_code=""):
	"""The branch code and tax id of our own address, for freezing on a tax invoice (tax id falls back to
	the company's). A blank branch code on the address becomes `default_branch_code`."""
	snapshot = {"company_branch_code": "", "company_tax_id": ""}
	if address:
		row = frappe.db.get_value("Address", address, ["branch_code", "tax_id"], as_dict=True) or {}
		snapshot["company_branch_code"] = normalise_branch_code(row.get("branch_code")) or default_branch_code
		snapshot["company_tax_id"] = (row.get("tax_id") or "").strip()
	if not snapshot["company_tax_id"] and company:
		snapshot["company_tax_id"] = (frappe.db.get_value("Company", company, "tax_id") or "").strip()
	return snapshot


def get_source_addresses(doc):
	"""{source document: its company address} of the Sales Orders / Delivery Notes the items come from."""
	sources = set()
	for fieldname, source_doctype in SOURCE_FIELDS.get(doc.doctype, ()):
		for item in doc.get("items") or []:
			if item.get(fieldname):
				sources.add((source_doctype, item.get(fieldname)))
	return {
		source: frappe.db.get_value(source[0], source[1], "company_address") for source in sorted(sources)
	}


def validate_company_branch(doc, method=None):
	"""The selling branch of a document: set, one of our branches, and consistent with the returned or
	source documents. Companies with at most one company address are not affected."""
	if not doc.get("company"):
		return
	addresses = get_company_addresses(doc.company)
	if len(addresses) <= 1:
		if addresses and not doc.get("company_address"):
			doc.company_address = addresses[0].name
		return

	if not doc.get("company_address"):
		frappe.throw(
			_("Company Branch (Company Address) is required: {0} has several branches.").format(
				frappe.bold(doc.company)
			)
		)
	branch = next((a for a in addresses if a.name == doc.company_address), None)
	if not branch:
		frappe.throw(
			_(
				"Company Branch {0} must be an enabled company address (Is Your Company Address) of {1}."
			).format(frappe.bold(doc.company_address), frappe.bold(doc.company))
		)
	if not is_valid_branch_code(branch.branch_code):
		frappe.throw(
			_("Company Branch {0} needs a 5-digit Branch Code (head office is 00000).").format(
				frappe.bold(doc.company_address)
			)
		)

	if doc.get("is_return") and doc.get("return_against"):
		original = frappe.db.get_value(doc.doctype, doc.return_against, "company_address")
		if original and original != doc.company_address:
			frappe.throw(
				_("Return {0} must use the Company Branch {1} of the original {2}, not {3}.").format(
					doc.get("name") or "",
					frappe.bold(original),
					doc.return_against,
					frappe.bold(doc.company_address),
				)
			)

	sources = get_source_addresses(doc)
	source_addresses = {address for address in sources.values() if address}
	if len(source_addresses) > 1:
		frappe.throw(
			_(
				"The source documents belong to different company branches ({0}). Create a separate {1} for each branch."
			).format(", ".join(sorted(source_addresses)), _(doc.doctype))
		)
	if source_addresses and doc.company_address not in source_addresses:
		frappe.throw(
			_("Company Branch {0} differs from that of the source documents ({1}).").format(
				frappe.bold(doc.company_address), ", ".join(sorted(source_addresses))
			)
		)


def set_company_branch_snapshot(tax_invoice):
	"""Freeze our branch code and tax id on a draft tax invoice from its company tax address (also on an
	amended copy or a hand-made one, as the snapshot fields are no-copy)."""
	if tax_invoice.docstatus != 0 or not tax_invoice.get("company_tax_address"):
		return
	snapshot = get_company_branch_snapshot(tax_invoice.company, tax_invoice.company_tax_address)
	for fieldname, value in snapshot.items():
		setattr(tax_invoice, fieldname, value)
