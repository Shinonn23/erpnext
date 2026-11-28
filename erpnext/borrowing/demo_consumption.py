"""Confirm customer demonstration consumption from an employee's Stock Borrowing."""

import hashlib
import json
from math import isfinite

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate, today
from frappe.utils.user import as_user

OPEN_STATUSES = ("Active", "Partially Returned")
OUTSTANDING_STATUSES = (*OPEN_STATUSES, "Pending Quality Inspection", "Pending Receipt")


def expense_account(company):
	"""Return the active expense ledger configured for this company's demo consumption."""
	account = frappe.db.get_value("Company", company, "demo_consumable_expense_account")
	info = (
		frappe.db.get_value(
			"Account", account, ["company", "root_type", "is_group", "disabled"], as_dict=True
		)
		if account
		else None
	)
	if not info or info.company != company or info.root_type != "Expense" or info.is_group or info.disabled:
		frappe.throw(_("Set a valid Customer Demo Consumable Expense Account on the Company."))
	return account


def _loan_refusal(loan, *, statuses=OPEN_STATUSES):
	if (
		loan.docstatus != 1
		or loan.loan_type != "Stock"
		or loan.borrower_type != "Employee"
		or loan.status not in statuses
	):
		return _("Select an active employee stock borrowing."), frappe.ValidationError

	employee = frappe.db.get_value("Employee", loan.borrower, ["user_id", "status", "company"], as_dict=True)
	if (
		not employee
		or employee.user_id != frappe.session.user
		or employee.status != "Active"
		or employee.company != loan.company
	):
		return _(
			"Only the employee who holds this borrowing can confirm its demo consumption."
		), frappe.PermissionError
	if frappe.db.get_value("Material Request", loan.material_request, "material_request_type") != "Borrow":
		return _("Demo consumption requires an employee Borrow request."), frappe.ValidationError
	return None


def own_employee_loan(name, allow_closed=False, *, for_update=False):
	loan = frappe.get_doc("Borrowing", name, for_update=for_update)
	loan.check_permission("read")
	statuses = (*OUTSTANDING_STATUSES, "Closed") if allow_closed else OPEN_STATUSES
	if refusal := _loan_refusal(loan, statuses=statuses):
		frappe.throw(*refusal)
	return loan


@frappe.whitelist()
def options(name):
	loan = own_employee_loan(name)
	loan.refresh_balances(persist=False)
	return _available_options(loan)


def _borrowing_uom(row):
	return frappe.db.get_value(
		"Material Request Item", row.material_request_item, "uom"
	) or frappe.get_cached_value("Item", row.item_code, "stock_uom")


def _available_options(loan):
	from erpnext.borrowing.doctype.borrowing.borrowing_core import loan_item_available_to_adjust
	from erpnext.borrowing.doctype.borrowing.borrowing_tracking import (
		conversion_factors,
		grouped_open_rows,
		is_tracked,
	)

	rows = []
	factors = conversion_factors(loan)
	for row in loan.items:
		if (
			not cint(frappe.get_cached_value("Item", row.item_code, "loan_consumable"))
			or flt(row.outstanding_qty) <= 0
		):
			continue
		groups = (
			grouped_open_rows(loan, row)
			if is_tracked(row.item_code)
			else [{"qty": loan_item_available_to_adjust(loan.name, row), "batch_no": None, "serials": []}]
		)
		factor = flt(factors.get(row.name) or 1)
		for group in groups:
			qty = flt(group["qty"]) / factor if group["serials"] else flt(group["qty"])
			qty = min(qty, flt(row.outstanding_qty))
			if qty > 0:
				rows.append(
					{
						"loan_item": row.name,
						"item_code": row.item_code,
						"batch_no": group["batch_no"],
						"qty": qty,
						"uom": _borrowing_uom(row),
						"serial_nos": group["serials"],
						"serial_per_unit": factor,
					}
				)
	return rows


def _confirmation_rows(selections):
	from erpnext.borrowing.doctype.borrowing.borrowing_core import normalize_serials

	chosen = frappe.parse_json(selections) or []
	if not isinstance(chosen, list) or not chosen or any(not isinstance(row, dict) for row in chosen):
		frappe.throw(_("Select at least one consumed item."))
	rows = []
	for row in chosen:
		loan_item = row.get("loan_item")
		batch_no = row.get("batch_no") or None
		qty = flt(row.get("qty"))
		serials = normalize_serials(row.get("serial_no") or "")
		if (
			not isinstance(loan_item, str)
			or (batch_no is not None and not isinstance(batch_no, str))
			or not isfinite(qty)
			or qty <= 0
			or any(not isinstance(serial, str) for serial in serials)
		):
			frappe.throw(_("Consumed quantity or serial numbers exceed the available borrowing balance."))
		rows.append(
			{
				"loan_item": loan_item,
				"batch_no": batch_no,
				"qty": qty,
				"serials": sorted(serials),
			}
		)
	# Keep the stored token hash compatible with confirmations made before the move.
	return sorted(rows, key=lambda row: (row["loan_item"], row["batch_no"] or ""))


def _verified_adjustment_rows(loan, chosen):
	available = {(row["loan_item"], row["batch_no"] or None): row for row in _available_options(loan)}
	used = set()
	rows = []
	for row in chosen:
		key = (row["loan_item"], row["batch_no"])
		part = available.get(key)
		qty = row["qty"]
		serials = row["serials"]
		if (
			not part
			or key in used
			or qty > flt(part["qty"])
			or len(serials) != len(set(serials))
			or not set(serials).issubset(part["serial_nos"])
			or (part["serial_nos"] and len(serials) != qty * part["serial_per_unit"])
		):
			frappe.throw(_("Consumed quantity or serial numbers exceed the available borrowing balance."))
		used.add(key)
		rows.append(
			{
				"borrowing_item": key[0],
				"item_code": part["item_code"],
				"qty": qty,
				"batch_no": key[1],
				"serial_no": "\n".join(serials),
			}
		)
	return rows


def _submit_stock_entry(entry):
	from frappe.model.workflow import (
		apply_workflow,
		get_transitions,
		get_workflow,
		get_workflow_name,
		has_approval_access,
	)

	if not get_workflow_name(entry.doctype):
		entry.submit()
		return entry

	workflow = get_workflow(entry.doctype)
	submitted_states = {state.state for state in workflow.states if cint(state.doc_status) == 1}
	actions = [
		transition.action
		for transition in get_transitions(entry, workflow)
		if transition.next_state in submitted_states
		and has_approval_access(frappe.session.user, entry, transition)
	]
	if len(actions) != 1 or entry.meta.queue_in_background:
		frappe.throw(_("The generated Stock Entry cannot be submitted through its workflow."))

	previous_action = frappe.flags.get("applied_workflow_action")
	try:
		return apply_workflow(entry.as_json(), actions[0])
	finally:
		frappe.flags.applied_workflow_action = previous_action


@frappe.whitelist(methods=["POST"])
def confirm(name, customer, demo_date, selections, token):
	from erpnext.borrowing.doctype.borrowing.borrowing_core import set_loan_parties

	user = frappe.session.user
	own_employee_loan(name, allow_closed=True)
	if (
		not customer
		or not frappe.has_permission("Customer", "read", doc=customer)
		or frappe.db.get_value("Customer", customer, "disabled")
	):
		frappe.throw(_("Select an accessible active Customer."))
	if not demo_date or getdate(demo_date) > getdate(today()):
		frappe.throw(_("Enter a demo date that is not in the future."))
	if not isinstance(token, str) or len(token) < 16 or len(token) > 80:
		frappe.throw(_("A valid request token is required."))
	chosen = _confirmation_rows(selections)
	payload_hash = hashlib.sha256(
		json.dumps([name, customer, str(demo_date), chosen], sort_keys=True).encode()
	).hexdigest()

	loan = own_employee_loan(name, allow_closed=True, for_update=True)
	existing = frappe.db.get_value(
		"Borrowing Adjustment",
		{"demo_request_token": token},
		[
			"name",
			"borrowing",
			"demo_confirmed_by",
			"demo_customer",
			"demo_request_hash",
			"stock_entry",
			"docstatus",
		],
		as_dict=True,
		for_update=True,
	)
	if existing:
		if (
			existing.borrowing,
			existing.demo_confirmed_by,
			existing.demo_customer,
			existing.demo_request_hash,
		) != (loan.name, user, customer, payload_hash):
			frappe.throw(_("Request token already belongs to another confirmation."))
		if (
			existing.docstatus != 1
			or not existing.stock_entry
			or not frappe.db.get_value(
				"Stock Entry",
				{
					"name": existing.stock_entry,
					"docstatus": 1,
					"borrowing": loan.name,
					"borrowing_source_doctype": "Borrowing Adjustment",
					"borrowing_source_name": existing.name,
				},
				"name",
				for_update=True,
			)
		):
			frappe.throw(
				_("This demo confirmation was cancelled or is incomplete; start a new confirmation.")
			)
		return {"adjustment": existing.name, "stock_entry": existing.stock_entry}
	if loan.status not in OPEN_STATUSES:
		frappe.throw(_("No outstanding goods remain on this borrowing."))

	loan.refresh_balances(persist=False)
	rows = _verified_adjustment_rows(loan, chosen)
	account = expense_account(loan.company)
	# The employee authorizes the exact items; the system posts the validated movement.
	with as_user("Administrator"):
		adjustment = frappe.new_doc("Borrowing Adjustment")
		adjustment.update(
			{
				"company": loan.company,
				"borrowing": loan.name,
				"adjustment_date": demo_date,
				"demo_consumption": 1,
				"demo_customer": customer,
				"demo_confirmed_by": user,
				"demo_request_token": token,
				"demo_request_hash": payload_hash,
				"reason": "Customer demo consumption",
				"adjustment_account": account,
				"items": rows,
			}
		)
		set_loan_parties(adjustment, loan)
		adjustment.insert(ignore_permissions=True)
		adjustment.submit()
		entry = frappe.get_doc(adjustment.prepare_stock_entry())
		entry.insert(ignore_permissions=True)
		posted = _submit_stock_entry(entry)
		if (
			not posted
			or posted.docstatus != 1
			or frappe.db.get_value("Borrowing Adjustment", adjustment.name, "stock_entry") != entry.name
		):
			frappe.throw(_("The generated Stock Entry cannot be submitted through its workflow."))
		return {"adjustment": adjustment.name, "stock_entry": entry.name}


@frappe.whitelist()
def can_confirm(name):
	if not name or not frappe.has_permission("Borrowing", "read", doc=name):
		return False
	loan = frappe.db.get_value(
		"Borrowing",
		name,
		["docstatus", "loan_type", "borrower_type", "borrower", "status", "material_request", "company"],
		as_dict=True,
	)
	return bool(loan and not _loan_refusal(loan))


@frappe.whitelist()
def my_outstanding():
	user = frappe.session.user
	if user == "Guest":
		frappe.throw(_("Please log in."), frappe.PermissionError)
	employees = frappe.get_all("Employee", filters={"user_id": user, "status": "Active"}, pluck="name")
	if not employees:
		return {"loans": [], "totals": []}
	names = frappe.get_all(
		"Borrowing",
		filters={
			"borrower_type": "Employee",
			"borrower": ["in", employees],
			"loan_type": "Stock",
			"docstatus": 1,
			"status": ["in", OUTSTANDING_STATUSES],
		},
		pluck="name",
		order_by="creation",
	)
	loans, totals = [], {}
	for name in names:
		if not frappe.has_permission("Borrowing", "read", doc=name):
			continue
		loan = frappe.get_doc("Borrowing", name)
		if _loan_refusal(loan, statuses=OUTSTANDING_STATUSES):
			continue
		loan.refresh_balances(persist=False)
		for row in loan.items:
			if flt(row.outstanding_qty) > 0:
				key = (row.item_code, _borrowing_uom(row))
				totals[key] = totals.get(key, 0) + flt(row.outstanding_qty)
		if loan.status in OPEN_STATUSES and (parts := _available_options(loan)):
			loans.append({"name": name, "items": parts})
	return {
		"loans": loans,
		"totals": [
			{"item_code": item, "uom": uom, "qty": qty} for (item, uom), qty in sorted(totals.items())
		],
	}
