import frappe

from erpnext.regional.thailand.custom.custom_api import SPLIT_ROW_DOCTYPES


def set_empty_link(doctype, name, tinv_doctype, tinv_name):
	"""Point a voucher header / row at its tax invoice when it points at none yet."""
	if frappe.db.get_value(doctype, name, "tax_invoice_ref"):
		return
	frappe.db.set_value(
		doctype,
		name,
		{"tax_invoice_ref_type": tinv_doctype, "tax_invoice_ref": tinv_name},
		update_modified=False,
	)


# columns every tax invoice doctype has / only some have (Sales Tax Invoice has no splitted_tax_invoice)
INVOICE_FIELDS = ("name", "voucher_type", "voucher_no", "gl_entry", "voucher_detail_no")
OPTIONAL_INVOICE_FIELDS = ("splitted_tax_invoice",)


def invoice_fields(doctype):
	"""The fields of a tax invoice the backfill reads: only columns the doctype really has."""
	return [
		*INVOICE_FIELDS,
		*(f for f in OPTIONAL_INVOICE_FIELDS if frappe.db.has_column(doctype, f)),
	]


def execute():
	"""Link the vouchers (Journal Entry row, split row, else header) to their submitted tax invoices and
	fill Voucher Detail No. Only empty fields are written, so it can run again.
	Cost: one list query per tax invoice doctype, then per invoice up to three point reads (GL Entry row name,
	existence, current link) and at most two writes. No schema change."""
	for doctype in ("Sales Tax Invoice", "Purchase Tax Invoice"):
		if not frappe.db.has_column(doctype, "voucher_detail_no"):
			continue
		invoices = frappe.get_all(
			doctype,
			filters={"docstatus": 1},
			fields=invoice_fields(doctype),
			order_by="creation asc",
		)
		for tinv in invoices:
			row_doctype, row_name = None, None
			if tinv.voucher_type == "Journal Entry" and tinv.gl_entry:
				row_name = frappe.db.get_value("GL Entry", tinv.gl_entry, "voucher_detail_no")
				row_doctype = "Journal Entry Tax Invoice Detail"
			elif tinv.splitted_tax_invoice and tinv.voucher_type in SPLIT_ROW_DOCTYPES:
				row_name = tinv.splitted_tax_invoice
				row_doctype = SPLIT_ROW_DOCTYPES[tinv.voucher_type]
			if row_name:
				if not tinv.voucher_detail_no:
					frappe.db.set_value(
						doctype, tinv.name, "voucher_detail_no", row_name, update_modified=False
					)
				if frappe.db.exists(row_doctype, row_name):
					set_empty_link(row_doctype, row_name, doctype, tinv.name)
			elif (
				not tinv.splitted_tax_invoice
				and tinv.voucher_type
				and tinv.voucher_no
				and frappe.db.has_column(tinv.voucher_type, "tax_invoice_ref")
				and frappe.db.exists(tinv.voucher_type, tinv.voucher_no)
			):
				set_empty_link(tinv.voucher_type, tinv.voucher_no, doctype, tinv.name)
