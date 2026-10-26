"""Per-branch helpers shared by the Thai tax reports (Sales/Purchase Tax Report, PND3, PND53).

VAT (PP30) and withholding tax (PND3/PND53) are filed per company branch, so the reports filter by Company and
Company Branch, and the print format only prints one branch at a time.
"""

import frappe
from frappe import _
from frappe.utils import escape_html

HEAD_OFFICE_CODE = "00000"
HEAD_OFFICE_CODES = ("", "-", HEAD_OFFICE_CODE)


def normalise_branch_code(code):
	"""'3' -> '00003'; blank and '-' (no branch) stay as they are."""
	code = (code or "").strip()
	return code.zfill(5) if code.isdigit() else code


def is_head_office(code):
	return normalise_branch_code(code) in HEAD_OFFICE_CODES


def branch_label(code):
	"""'Head Office' for the head office (blank, '-' or 00000), else 'Branch 00003'."""
	code = normalise_branch_code(code)
	return "Head Office" if is_head_office(code) else f"Branch {code}"


def branch_keys(rows):
	"""{row index: branch key} of the rows. A row without a company tax address (a tax invoice made before
	the field existed) belongs to the head office: it takes the key of the head-office address among the rows,
	or '' when there is none."""
	head_offices = sorted(
		{
			row.get("company_tax_address")
			for row in rows or []
			if row.get("company_tax_address") and is_head_office(row.get("company_branch_code"))
		}
	)
	blank_key = head_offices[0] if head_offices else ""
	return {
		index: (row.get("company_tax_address") or blank_key) for index, row in enumerate(rows or [])
	}


def is_mixed_branches(rows):
	"""True when the rows belong to more than one company branch (a blank address counts as head office)."""
	return len(set(branch_keys(rows).values())) > 1


def needs_branch_choice(rows, branch_selected, company_branch_count=0):
	"""True when the filing print must not show the table: several branches are in the rows, or no branch is
	selected while the company has more than one branch."""
	if is_mixed_branches(rows):
		return True
	return not branch_selected and (company_branch_count or 0) > 1


def branch_criterion(field, address):
	"""The condition on a document's company address column for the selected Company Branch. The head
	office also takes the documents without an address."""
	criterion = field == address
	if is_head_office(frappe.db.get_value("Address", address, "branch_code")):
		criterion = criterion | field.isnull() | (field == "")
	return criterion


def get_company_branch_count(company):
	if not company:
		return 0
	from erpnext.regional.thailand.custom.company_branch import get_company_addresses

	return len(get_company_addresses(company))


def branch_summary(rows, currency=None, tax_name="VAT"):
	"""Report summary cards: one per branch (its tax, with its tax base in the label) and the total."""
	keys = branch_keys(rows)
	branches = {}
	for index, row in enumerate(rows or []):
		branch = branches.setdefault(keys[index], {"code": "", "base": 0, "tax": 0})
		branch["code"] = branch["code"] or row.get("company_branch_code") or ""
		branch["base"] += float(row.get("tax_base") or 0)
		branch["tax"] += float(row.get("tax_amount") or 0)

	cards = []
	for _key, branch in sorted(branches.items(), key=lambda item: (item[1]["code"], item[0])):
		cards.append(
			{
				"value": round(branch["tax"], 2),
				"label": _("{0}: {1} (Tax Base {2})").format(
					escape_html(branch_label(branch["code"])), tax_name, f"{branch['base']:,.2f}"
				),
				"datatype": "Currency",
				"currency": currency,
			}
		)
	total_base = sum(b["base"] for b in branches.values())
	cards.append(
		{
			"value": round(sum(b["tax"] for b in branches.values()), 2),
			"label": _("Total {0} (Tax Base {1})").format(tax_name, f"{total_base:,.2f}"),
			"datatype": "Currency",
			"currency": currency,
			"indicator": "Blue",
		}
	)
	return cards


def finish_tax_report(rows, filters, summary=False, tax_name="VAT"):
	"""Normalise the branch codes, flag the rows for the filing print (`filing_notice` on every row) and
	return (rows, report_summary). The summary is only built for the all-branches view (no Company Branch
	selected)."""
	filters = filters or {}
	for row in rows:
		# our branch: a blank one is the head office; the live address code may be unpadded ('3')
		for fieldname in ("company_branch_code", "company_branch"):
			if fieldname in row:
				row[fieldname] = normalise_branch_code(row[fieldname]) or HEAD_OFFICE_CODE
		if row.get("branch"):
			row["branch"] = normalise_branch_code(row["branch"])
	selected = filters.get("company_tax_address") or filters.get("company_address")
	branch_count = 0 if selected else get_company_branch_count(filters.get("company"))
	notice = needs_branch_choice(rows, selected, branch_count)
	for row in rows:
		row["filing_notice"] = 1 if notice else 0
	report_summary = None
	if summary and not selected and rows:
		currency = (
			frappe.db.get_value("Company", filters.get("company"), "default_currency")
			if filters.get("company")
			else None
		)
		report_summary = branch_summary(rows, currency, tax_name)
	return rows, report_summary
