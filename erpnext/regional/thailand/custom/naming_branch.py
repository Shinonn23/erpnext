"""The `BRANCH` naming series token: the 5-digit branch code of the document's company branch address.

Naming runs before validate, so a blank address field is resolved here from the same source validate and
set_missing_values use, to give the branch the document will end up with:
- selling and buying documents: the company's default address (`get_default_address`, what
  `get_company_address` and the party details return), else its only enabled address, else the head office;
- Payment Entry: the single branch of its references, else its field, else the head office
  (as `custom_api.validate_company_address`);
- Journal Entry and Expense Claim: the field, else the head office;
- Sales Billing and Purchase Billing: the field, else the branch of the first invoice listed, else the head office;
- Payment Receipt: the field, else the single branch of its Payment Entries, else the head office;
- Withholding Tax Cert: the field, else its voucher's `company_tax_address`, else `company_address_of`.
"""

import frappe
from frappe.contacts.doctype.address.address import get_default_address

HEAD_OFFICE = "00000"

# doctype -> the field holding its company branch address
NAMING_BRANCH_FIELDS = {
	"Quotation": "company_address",
	"Sales Order": "company_address",
	"Delivery Note": "company_address",
	"Sales Invoice": "company_address",
	"POS Invoice": "company_address",
	"Purchase Order": "billing_address",
	"Purchase Receipt": "billing_address",
	"Purchase Invoice": "billing_address",
	"Supplier Quotation": "billing_address",
	"Payment Entry": "company_tax_address",
	"Journal Entry": "company_tax_address",
	"Expense Claim": "company_tax_address",
	"Withholding Tax Cert": "company_address",
	"Sales Billing": "company_tax_address",
	"Purchase Billing": "company_tax_address",
	"Payment Receipt": "company_tax_address",
}

# billing doctype -> (table, invoice field, invoice doctype, the invoice's branch address field)
BILLING_LINES = {
	"Sales Billing": ("sales_billing_line", "sales_invoice", "Sales Invoice", "company_address"),
	"Purchase Billing": ("purchase_billing_line", "purchase_invoice", "Purchase Invoice", "billing_address"),
}


def _log_error():
	try:
		frappe.log_error(title="Branch naming token")
	except Exception:
		pass


def resolve_branch_address(doc):
	"""The name of the company branch address the document is filed under (None when unknown).
	Never raises (an unexpected error is logged)."""
	try:
		return _resolve_branch_address(doc)
	except Exception:
		_log_error()
		return None


def _resolve_branch_address(doc):
	if not doc:
		return None
	doctype = doc.get("doctype")
	field = NAMING_BRANCH_FIELDS.get(doctype)
	if not field:
		return None

	from erpnext.regional.thailand.custom.company_branch import get_company_addresses
	from erpnext.regional.thailand.custom.custom_api import (
		get_company_tax_address,
		referenced_company_addresses,
	)

	company = doc.get("company")
	if doctype == "Payment Entry":
		addresses = referenced_company_addresses(doc)
		if len(addresses) == 1:
			return next(iter(addresses))
	if doc.get(field):
		return doc.get(field)
	if doctype in BILLING_LINES or doctype == "Payment Receipt":
		address = billing_document_address(doc)
		if address:
			return address
	if doctype == "Withholding Tax Cert":
		from erpnext.regional.thailand.custom.withholding_cert import company_address_of

		voucher_address = None
		if doc.get("voucher_type") and doc.get("voucher_no"):
			voucher_address = frappe.db.get_value(doc.voucher_type, doc.voucher_no, "company_tax_address")
		return company_address_of({"company": company, "company_tax_address": voucher_address})
	if not company:
		return None
	if field == "company_tax_address":
		return get_company_tax_address(company)
	branches = get_company_addresses(company)
	return get_default_address("Company", company) or (
		branches[0]["name"] if len(branches) == 1 else get_company_tax_address(company)
	)


def billing_line_addresses(doc):
	"""The branch addresses of the invoices a billing lists, in row order, each once (blanks ignored)."""
	table, invoice_field, invoice_doctype, address_field = BILLING_LINES[doc.get("doctype")]
	found = []
	for row in doc.get(table) or []:
		if row.get(invoice_field):
			address = frappe.db.get_value(invoice_doctype, row.get(invoice_field), address_field)
			if address and address not in found:
				found.append(address)
	return found


def billing_document_address(doc):
	"""The branch of a billing or receipt from what it lists: the first invoice of a billing, the single branch of the
	Payment Entries of a receipt (None when the rows name none, or a receipt spans several)."""
	doctype = doc.get("doctype")
	if doctype in BILLING_LINES:
		addresses = billing_line_addresses(doc)
		return addresses[0] if addresses else None
	addresses = {
		frappe.db.get_value("Payment Entry", row.get("payment_entry"), "company_tax_address")
		for row in doc.get("payment_references") or []
		if row.get("payment_entry")
	} - {None, ""}
	return next(iter(addresses)) if len(addresses) == 1 else None


def get_branch_code(doc):
	"""The 5-digit branch code for the BRANCH token; '00000' whenever it cannot be told. Never raises."""
	try:
		from erpnext.regional.thailand.custom.company_branch import (
			is_valid_branch_code,
			normalise_company_branch_code,
		)

		address = _resolve_branch_address(doc)
		if not address:
			return HEAD_OFFICE
		code = normalise_company_branch_code(frappe.db.get_value("Address", address, "branch_code"))
		return code if is_valid_branch_code(code) else HEAD_OFFICE
	except Exception:
		_log_error()
		return HEAD_OFFICE
