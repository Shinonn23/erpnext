"""Tax invoice numbers per company branch (owner decision 27-10-2026, replacing the 22-10 company-year series).

`ABBR + BRANCH - STI|PTI + YY(BE) + MM + ###`, e.g. CD00000-STI6910001 (head office, October 2026, first one)
and CD00003-PTI6905001: the company's abbreviation and OUR 5-digit branch code (that of the invoice's
company_tax_address; 00000 when the address has no valid code), STI (Sales) or PTI (Purchase), the last 2 digits
of the Buddhist-era year (Gregorian + 543: 2026 -> 69), the month, and a running number that restarts every month,
per branch and per type (make_autoname keys the counter by the text before the hashes)."""

import frappe
from frappe.model.naming import make_autoname
from frappe.utils import getdate, nowdate

from erpnext.regional.thailand.custom.company_branch import (
	get_company_branch_snapshot,
	is_valid_branch_code,
)

HEAD_OFFICE_BRANCH_CODE = "00000"
TYPE_CODES = {"Sales Tax Invoice": "STI", "Purchase Tax Invoice": "PTI"}


def tax_invoice_series(doc):
	"""The make_autoname format of this Sales/Purchase Tax Invoice, from its branch and its date.

	The date is OUR posting date, so that the month does not depend on the creation path: for a Purchase Tax
	Invoice always the posting date of its GL Entry (a split row passes the supplier's invoice date as `date`
	before insert; that is ignored), for a Sales Tax Invoice its `date`, else the GL Entry's posting date (the
	zero-tax path passes the voucher's posting date). Without either, a hand-made one: `date`, else today.
	The name is frozen at insert: a draft whose company tax address (and so branch) is changed later keeps its
	name; the branch snapshot follows the address, the name does not."""
	abbr = frappe.get_cached_value("Company", doc.company, "abbr")
	branch = get_company_branch_snapshot(doc.company, doc.get("company_tax_address"))["company_branch_code"]
	if not is_valid_branch_code(branch):
		branch = HEAD_OFFICE_BRANCH_CODE
	gl_date = doc.get("gl_entry") and frappe.db.get_value("GL Entry", doc.gl_entry, "posting_date")
	if doc.doctype == "Purchase Tax Invoice":
		date = gl_date or doc.get("date") or nowdate()
	else:
		date = doc.get("date") or gl_date or nowdate()
	date = getdate(date)
	year_be = f"{(date.year + 543) % 100:02d}"
	return f"{abbr}{branch}-{TYPE_CODES[doc.doctype]}{year_be}{date.month:02d}.###"


def make_tax_invoice_name(doc):
	return make_autoname(tax_invoice_series(doc), doc.doctype, doc)
