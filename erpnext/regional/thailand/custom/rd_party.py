"""A new Customer or Supplier filled from the Revenue Department gets its RD address as the primary Billing Address.

The form (public/js/thailand/party_tax_id.js) marks the new party with the non-stored ``rd_fill_address``. On
save the server asks RD again (cached for an hour, see ``utils.fetch_rd_vat_record``) and never trusts an address
sent by the browser. Nothing is made when the party already has an Address (another path made it), when the
Tax ID was changed by hand to one RD does not know, or when RD is unreachable (a comment says so; the save stands).
"""

import frappe
from frappe import _

from erpnext.regional.thailand.utils import (
	PARTY_DOCTYPES,
	branch_code_of,
	clean_branch,
	fetch_rd_vat_record,
	finalize_address_dict,
)

PRIMARY_FIELD = {"Customer": "customer_primary_address", "Supplier": "supplier_primary_address"}


def _linked_addresses(doctype, name):
	return frappe.get_all(
		"Dynamic Link",
		filters={"parenttype": "Address", "link_doctype": doctype, "link_name": name},
		pluck="parent",
	)


def address_values(doc, data):
	address = finalize_address_dict(data)
	return {
		"doctype": "Address",
		"address_title": address["name"] or doc.get(PARTY_DOCTYPES[doc.doctype]) or doc.name,
		"address_type": "Billing",
		"address_line1": address["address_line1"] or address["name"],
		"city": address["city"] or address["county"] or address["state"],
		"county": address["county"],
		"state": address["state"],
		"pincode": address["pincode"],
		"country": "Thailand",
		"tax_id": doc.tax_id,
		"branch_code": doc.branch_code,
		"is_primary_address": 1,
		"links": [{"link_doctype": doc.doctype, "link_name": doc.name}],
	}


def create_rd_address(doc, method=None):
	"""Customer / Supplier on_update: make the RD address of a party the form filled from RD."""
	if doc.doctype not in PARTY_DOCTYPES or not doc.get("rd_fill_address") or not doc.get("tax_id"):
		return
	doc.rd_fill_address = None  # once per save
	if _linked_addresses(doc.doctype, doc.name):
		return
	try:
		branch = clean_branch(doc.get("branch_code") or "0")
		data = fetch_rd_vat_record(doc.tax_id, branch)
	except frappe.ValidationError:
		if getattr(frappe.local, "message_log", None):
			frappe.local.message_log.pop()
		doc.add_comment(
			"Comment", _("The Revenue Department address could not be fetched; add the address by hand.")
		)
		return
	if not data:
		return
	if not doc.get("branch_code"):
		doc.db_set("branch_code", branch_code_of(branch), update_modified=False)
	address = frappe.get_doc(address_values(doc, data))
	address.flags.ignore_permissions = True
	address.insert()
	doc.db_set(PRIMARY_FIELD[doc.doctype], address.name, update_modified=False)
