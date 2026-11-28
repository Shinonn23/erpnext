"""Explicit preparation of approved Demo / Borrow requests.

Approval and Asset selection create no follow-up documents. The Stock team presses Create Borrowing to create and
submit the request's Stock and/or Asset loans. Submission prepares them without moving anything; Stock picking is
started explicitly and Confirm Delivery records the complete set's physical delivery in the ERPNext fork.

A failed preparation is reported on the request for the Stock team to correct and retry. Inspection, conversion,
placement and return rules keep their separate authorization and native document lifecycles.
"""

import frappe
from frappe import _

from .policy import sync_request_tasks

LOAN_TYPES = ("Customer Demo", "Borrow", "Customer Borrow")


def undecided_rows(request):
	"""The rows (their numbers) the Stock team still has to decide."""
	from .loan_release import rows_to_decide

	return [row.idx for row in rows_to_decide(request)]


def issue_approved_request(doc, method=None):
	"""Approval makes a request ready for preparation; it never creates or delivers a loan."""
	if doc.material_request_type not in LOAN_TYPES:
		return
	from .loan_release import refresh_pending

	refresh_pending(doc.name)


def issue(request):
	"""Make and submit each Borrowing the request needs. Idempotent: an existing loan of a type is kept."""
	from erpnext.borrowing.doctype.borrowing.borrowing_core import (
		make_borrowing_from_request,
		request_loan_types,
	)

	request = frappe.get_doc("Material Request", request if isinstance(request, str) else request.name)
	if request.docstatus != 1 or undecided_rows(request):
		return []
	made = []
	problems = []
	loan_types = request_loan_types(request)
	for loan_type in loan_types:
		if frappe.db.exists(
			"Borrowing", {"material_request": request.name, "loan_type": loan_type, "docstatus": 1}
		):
			continue
		if loan_type == "Asset" and _waiting_for_return(request):
			# a reserved Demo Asset is not back yet: nothing is wrong, the team is told when it is ready
			continue
		if (
			loan_type == "Asset"
			and "Stock" in loan_types
			and not frappe.db.exists(
				"Borrowing", {"material_request": request.name, "loan_type": "Stock", "docstatus": 1}
			)
		):
			# a set leaves together: the Assets wait until the stock loan is made (the problem is already told)
			continue
		savepoint = f"issue_{loan_type.lower()}_loan"
		frappe.db.savepoint(savepoint)
		try:
			if loan_type == "Asset" and request.material_request_type == "Customer Demo":
				from .demo_reservations import _prepare_reserved_assets

				_prepare_reserved_assets(request)
			values = make_borrowing_from_request(request, loan_type, ignore_permissions=True)
			loan = frappe.get_doc("Borrowing", values["name"])
			if loan.docstatus == 0:
				loan.flags.ignore_permissions = True
				loan.submit()
			made.append(loan.name)
		except Exception as error:
			frappe.db.rollback(save_point=savepoint)
			frappe.clear_last_message()
			problems.append(
				_("The {0} borrowing could not be made: {1}").format(_(loan_type).lower(), str(error)[:300])
			)
	_record_problem(request, problems)
	if made:
		request.add_comment("Info", _("Borrowings prepared: {0}").format(", ".join(made)))
	return made


def _waiting_for_return(request):
	from .demo_reservations import WAIT_RETURN, _asset_ready

	return any(
		row.get("demo_acquisition_method") == WAIT_RETURN
		and row.get("asset")
		and not _asset_ready(frappe.get_doc("Asset", row.asset))
		for row in request.get("items") or []
	)


def _record_problem(request, problems):
	"""Tell the Stock team what stopped the loan, in words (a task, see the module text), or clear the message."""
	from .loan_release import PROBLEM_FIELD

	message = "\n".join(problems) or None
	if (request.get(PROBLEM_FIELD) or None) == message:
		return
	frappe.db.set_value("Material Request", request.name, PROBLEM_FIELD, message, update_modified=False)
	if message:
		request.add_comment("Comment", message + " " + _("The Stock team has been asked to fix it."))
	sync_request_tasks(frappe.get_doc("Material Request", request.name))
