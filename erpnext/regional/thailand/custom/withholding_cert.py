"""Withholding Tax Cert (หนังสือรับรองการหักภาษี ณ ที่จ่าย, 50 ทวิ) of a Payment Entry or Journal Entry that withheld
from a supplier.

Submitting the entry makes its certificates: one per income tax form (PND3 / PND53), with what the manual "Create
Withholding Tax Cert" popup used to default — the posting date, the form of the category (else PND3 for an
individual supplier, PND53 for a company), the entry's company tax address (else the company's own address) and the
supplier's address. A certificate with everything it needs is submitted; one that misses something (an address, the
Thai type of income of a category) stays a draft and the user is told what to fill in. Nothing here stops the
submit. Cancelling the entry cancels its certificates (drafts are deleted); an amended entry makes new ones. The
entry's Connections list them (dashboard_overrides.py) and the certificate links back through Voucher Type / Voucher
No. Receipts need no certificate from us: the customer issues it.
"""

import frappe
from frappe import _
from frappe.utils import flt, get_link_to_form

CERT = "Withholding Tax Cert"
VOUCHER_TYPES = ("Payment Entry", "Journal Entry")
SAVEPOINT = "thai_withholding_cert"


def linked_address(link_doctype, link_name, own=False):
	"""The only (or the Billing) address linked to a party or company; None when it is ambiguous."""
	filters = [
		["Dynamic Link", "link_doctype", "=", link_doctype],
		["Dynamic Link", "link_name", "=", link_name],
	]
	if own:
		filters.append(["is_your_company_address", "=", 1])
	addresses = frappe.get_all("Address", filters=filters, fields=["name", "address_type"], distinct=True)
	billing = [a for a in addresses if a.address_type == "Billing"]
	candidates = billing or addresses
	return candidates[0].name if len(candidates) == 1 else None


def default_form(supplier_type):
	"""The form a supplier is filed on when its category names none: PND3 for an individual, else PND53."""
	return "PND3" if supplier_type == "Individual" else "PND53"


def withholding_supplier(voucher):
	"""The supplier a Payment Entry (a payment to it) or a Journal Entry (its supplier line) withheld from."""
	if voucher.get("doctype") == "Journal Entry":
		row = next(
			(
				row
				for row in voucher.get("accounts") or []
				if row.get("party_type") == "Supplier" and row.get("party")
			),
			None,
		)
		return row.get("party") if row else None
	if voucher.get("party_type") == "Supplier" and voucher.get("payment_type") == "Pay":
		return voucher.get("party")
	return None


def certificate_groups(entries, supplier_type):
	"""{income tax form: {(type of income, rate): line}} from Tax Withholding Entries: a form per category (its own,
	else the supplier's), one certificate line per type of income and rate (advertising 2% and services 3% are both
	type 5 but separate lines on PND53)."""
	categories = {}
	groups = {}
	for entry in entries or []:
		amount = abs(flt(entry.get("withholding_amount")))
		category = entry.get("tax_withholding_category")
		if not amount or not category:
			continue
		if category not in categories:
			categories[category] = frappe._dict(
				frappe.db.get_value(
					"Tax Withholding Category",
					category,
					["category_name", "thai_type_of_income", "thai_income_tax_form"],
					as_dict=True,
				)
				or {}
			)
		details = categories[category]
		form = details.thai_income_tax_form or default_form(supplier_type)
		key = (details.thai_type_of_income or "", flt(entry.get("tax_rate")))
		line = groups.setdefault(form, {}).setdefault(
			key, {"tax_base": 0.0, "tax_amount": 0.0, "kinds": [], "categories": []}
		)
		line["tax_base"] += abs(flt(entry.get("taxable_amount")))
		line["tax_amount"] += amount
		kind = details.category_name or category
		if kind not in line["kinds"]:
			line["kinds"].append(kind)
		if category not in line["categories"]:
			line["categories"].append(category)
	return groups


def company_address_of(voucher):
	"""The entry's company tax address, else the company's own address (its Billing one, or its only one), else
	its default address."""
	from frappe.contacts.doctype.address.address import get_default_address

	from erpnext.regional.thailand.custom.custom_api import get_company_tax_address

	company = voucher.get("company")
	return (
		voucher.get("company_tax_address")
		or get_company_tax_address(company)
		or get_default_address("Company", company)
	)


def new_certificate(voucher, supplier, form, lines):
	"""A Withholding Tax Cert of the entry for one income tax form (not saved)."""
	details = (
		frappe.db.get_value("Supplier", supplier, ["supplier_name", "supplier_primary_address"], as_dict=True)
		or frappe._dict()
	)
	cert = frappe.new_doc(CERT)
	cert.company = voucher.get("company")
	cert.company_address = company_address_of(voucher)
	cert.supplier = supplier
	cert.supplier_name = details.supplier_name
	cert.supplier_address = details.supplier_primary_address or linked_address("Supplier", supplier)
	cert.income_tax_form = form
	cert.tax_payer = "Withholding"
	cert.date = voucher.get("posting_date")
	cert.voucher_type = voucher.get("doctype")
	cert.voucher_no = voucher.get("name")
	for (income_type, rate), line in lines.items():
		base = line["tax_base"]
		cert.append(
			"withholding_tax_items",
			{
				"type_of_income": income_type or None,
				"description": ", ".join(line["kinds"]),
				"tax_base": base,
				"tax_rate": rate or (line["tax_amount"] / base * 100 if base else 0),
				"tax_amount": line["tax_amount"],
			},
		)
	return cert


def missing_details(cert, lines):
	"""What the certificate still needs before it can be submitted."""
	missing = [
		_(label)
		for fieldname, label in (
			("company_address", "Company Address"),
			("supplier_address", "Supplier Address"),
			("date", "Date"),
		)
		if not cert.get(fieldname)
	]
	for (income_type, _rate), line in lines.items():
		if not income_type:
			missing.append(
				_("Thai Type of Income of Tax Withholding Category {0}").format(", ".join(line["categories"]))
			)
	return missing


def existing_certificates(voucher_type, voucher_no):
	"""{name: (income tax form, docstatus)} of the entry's certificates that are not cancelled."""
	return {
		row.name: (row.income_tax_form, row.docstatus)
		for row in frappe.get_all(
			CERT,
			filters={"voucher_type": voucher_type, "voucher_no": voucher_no, "docstatus": ("<", 2)},
			fields=["name", "income_tax_form", "docstatus"],
		)
	}


def create_withholding_certificates(doc, method=None):
	"""On submit of a Payment Entry / Journal Entry that withheld from a supplier: its certificates, one per income
	tax form it does not have yet. Submitted when complete, else a draft and a message; never stops the submit."""
	supplier = withholding_supplier(doc)
	entries = [
		entry for entry in doc.get("tax_withholding_entries") or [] if flt(entry.get("withholding_amount"))
	]
	if not supplier or not entries:
		return []
	have = {form for form, _docstatus in existing_certificates(doc.doctype, doc.name).values()}
	supplier_type = frappe.db.get_value("Supplier", supplier, "supplier_type")
	made = []
	for form, lines in certificate_groups(entries, supplier_type).items():
		if form in have:
			continue
		frappe.db.savepoint(SAVEPOINT)
		try:
			cert = new_certificate(doc, supplier, form, lines)
			missing = missing_details(cert, lines)
			cert.flags.ignore_permissions = True
			if missing:
				cert.flags.ignore_mandatory = True
				cert.insert()
				frappe.msgprint(
					_(
						"Withholding Tax Cert {0} ({1}) was saved as a draft because it needs: {2}. Fill it in "
						"and submit it."
					).format(get_link_to_form(CERT, cert.name), form, ", ".join(missing)),
					title=_("Withholding Tax Cert"),
					indicator="orange",
				)
			else:
				cert.insert()
				cert.submit()
				frappe.msgprint(
					_("Withholding Tax Cert {0} ({1}) created.").format(
						get_link_to_form(CERT, cert.name), form
					),
					indicator="green",
					alert=True,
				)
			made.append(cert.name)
		except Exception:
			frappe.db.rollback(save_point=SAVEPOINT)
			frappe.log_error(
				title=_("Withholding Tax Cert not created for {0}").format(doc.name),
				reference_doctype=doc.doctype,
				reference_name=doc.name,
			)
			frappe.msgprint(
				_(
					"The Withholding Tax Cert ({0}) of {1} could not be created automatically: use Create "
					"Withholding Tax Cert on it."
				).format(form, doc.name),
				title=_("Withholding Tax Cert"),
				indicator="red",
			)
	return made


def cancel_withholding_certificates(doc, method=None):
	"""On cancel of the entry: cancel its submitted certificates and delete its drafts."""
	for name, (_form, docstatus) in existing_certificates(doc.doctype, doc.name).items():
		if docstatus == 1:
			cert = frappe.get_doc(CERT, name)
			cert.flags.ignore_permissions = True
			cert.cancel()
		else:
			frappe.delete_doc(CERT, name, ignore_permissions=True)


@frappe.whitelist()
def get_voucher_certificates(voucher_type, voucher_no):
	"""Names of the certificates (not cancelled) of a Payment Entry / Journal Entry, for its fallback button."""
	if voucher_type not in VOUCHER_TYPES:
		frappe.throw(_("Withholding Tax Certs belong to a Payment Entry or a Journal Entry."))
	frappe.has_permission(voucher_type, "read", doc=voucher_no, throw=True)
	return list(existing_certificates(voucher_type, voucher_no))
