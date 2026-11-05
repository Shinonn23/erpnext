"""Shared pieces of the Custody Loan reports: who requested the loan, and the open serials / batches of a loan row.

The employee who requested a loan answers for the items until they come back (Connect Diagnostics, Siwat,
5 Nov 2026, relayed by the user), so every loan report shows the requester and can be filtered by them. Loans
submitted before the requester was stored on the loan fall back to the Employee of the user who raised the
Material Request.
"""

import frappe
from frappe import _

REQUEST_JOIN = "LEFT JOIN `tabMaterial Request` request ON request.name = loan.material_request"

_OWNER_EMPLOYEE = """(SELECT {field} FROM `tabEmployee` owner_employee
	WHERE owner_employee.user_id = request.owner AND owner_employee.company = loan.company
	ORDER BY owner_employee.status = 'Active' DESC LIMIT 1)"""

REQUESTED_BY = (
	f"COALESCE(NULLIF(loan.requested_by, ''), {_OWNER_EMPLOYEE.format(field='owner_employee.name')})"
)
REQUESTED_BY_NAME = (
	"COALESCE(NULLIF(loan.requested_by_name, ''), "
	f"{_OWNER_EMPLOYEE.format(field='owner_employee.employee_name')})"
)
REQUESTED_BY_USER = "COALESCE(NULLIF(loan.requested_by_user, ''), request.owner)"

REQUESTER_SELECT = (
	f"{REQUESTED_BY} AS requested_by, {REQUESTED_BY_NAME} AS requested_by_name, "
	f"{REQUESTED_BY_USER} AS requested_by_user"
)


# What one unit of a loan row is worth: an Asset's book value, else the stock valuation captured when the goods
# were issued (per stock unit, times the request's conversion factor), else the value the request was approved at.
UNIT_VALUE = """(CASE WHEN COALESCE(item.asset, '') != '' THEN COALESCE(
		NULLIF((SELECT unit_asset.value_after_depreciation FROM `tabAsset` unit_asset WHERE unit_asset.name = item.asset), 0),
		(SELECT unit_asset.net_purchase_amount FROM `tabAsset` unit_asset WHERE unit_asset.name = item.asset), 0)
	WHEN item.valuation_rate > 0 THEN item.valuation_rate * COALESCE(NULLIF(
		(SELECT unit_request.conversion_factor FROM `tabMaterial Request Item` unit_request
		WHERE unit_request.name = item.material_request_item), 0), 1)
	ELSE COALESCE((SELECT unit_request.rate FROM `tabMaterial Request Item` unit_request
		WHERE unit_request.name = item.material_request_item), 0) END)"""


def value_columns():
	"""Currency columns: the privacy layer hides them from users who may not see stock value."""
	return [
		{"label": _("Loan Value"), "fieldname": "loan_value", "fieldtype": "Currency", "width": 120},
		{
			"label": _("Outstanding Value"),
			"fieldname": "outstanding_value",
			"fieldtype": "Currency",
			"width": 135,
		},
	]


def requester_columns():
	return [
		{
			"label": _("Responsible Employee"),
			"fieldname": "requested_by",
			# Data, not Link: a Link column hides the rows whose Employee the viewer may not read, so the stock team
			# would lose every loan another employee requested (the id and name are all the report shows)
			"fieldtype": "Data",
			"width": 120,
		},
		{"label": _("Responsible Employee Name"), "fieldname": "requested_by_name", "fieldtype": "Data", "width": 170},
		{
			"label": _("Requester User"),
			"fieldname": "requested_by_user",
			"fieldtype": "Data",
			"width": 190,
		},
	]


def add_requester_filter(filters, conditions, values):
	if filters.get("requested_by"):
		conditions.append(f"{REQUESTED_BY} = %(requested_by)s")
		values["requested_by"] = filters.requested_by


def insert_columns_after(columns, fieldname, new_columns):
	index = next(i for i, column in enumerate(columns) if column["fieldname"] == fieldname) + 1
	return columns[:index] + new_columns + columns[index:]


def expand_serial_batches(rows):
	"""One report row per open batch / serial of a tracked loan row, from the loan's Serial / Batch Tracking.

	rows need loan, loan_item and outstanding_qty. A loan whose tracking table is still empty is computed live.
	"""
	from erpnext.loan.doctype.custody_loan.custody_loan_tracking import get_balances

	loan_names = sorted({row.loan for row in rows if row.get("loan_item")})
	tracking = {}
	if loan_names:
		for entry in frappe.get_all(
			"Custody Loan Serial Batch",
			filters={"parent": ["in", loan_names], "parenttype": "Custody Loan"},
			fields=[
				"parent",
				"custody_loan_item",
				"batch_no",
				"serial_no",
				"issued_qty",
				"returned_qty",
				"adjusted_qty",
				"outstanding_qty",
			],
			order_by="parent, idx",
		):
			tracking.setdefault(entry.custody_loan_item, []).append(entry)
		live = {row.loan for row in rows if row.get("loan_item") and row.loan_item not in tracking}
		for loan_name in live:
			loan = frappe.get_doc("Custody Loan", loan_name)
			if loan.loan_type != "Stock":
				continue
			for entry in get_balances(loan).values():
				tracking.setdefault(entry.custody_loan_item, []).append(entry)

	result = []
	for row in rows:
		entries = [entry for entry in tracking.get(row.get("loan_item"), []) if entry.outstanding_qty > 0]
		if not entries:
			result.append(row)
			continue
		for entry in entries:
			expanded = frappe._dict(
				row, batch_no=entry.batch_no, serial_no=entry.serial_no, outstanding_qty=entry.outstanding_qty
			)
			if "unit_value" in row:
				expanded.loan_value = (entry.get("issued_qty") or 0) * (row.unit_value or 0)
				expanded.outstanding_value = (entry.outstanding_qty or 0) * (row.unit_value or 0)
			# the row's own quantities become this batch / serial's
			for field, source in (
				("qty", "issued_qty"),
				("returned_qty", "returned_qty"),
				("adjusted_qty", "adjusted_qty"),
			):
				if field in row:
					expanded[field] = entry.get(source)
			result.append(expanded)
	return result
