"""Warehouse count-variance defaults and account validation for stock counts and reconciliations."""

from __future__ import annotations

import json

import frappe
from frappe import _

ACCOUNT_FIELD = "stock_adjustment_account"
DEPARTMENT_FIELD = "department"
COUNT_PURPOSE = "Stock Reconciliation"
MARKER_FIELD = "count_variance_applied"
MIXED_MESSAGE = "Count one laboratory warehouse per Stock Reconciliation."
_COUNT_RECONCILIATION = object()


def _may_edit(user):
	if user == "Administrator" or frappe.flags.get("in_migrate") or frappe.flags.get("in_patch"):
		return True
	roles = frappe.get_hooks("warehouse_count_variance_editor_roles") or ["System Manager"]
	return bool(set(roles) & set(frappe.get_roles(user)))


def validate_warehouse_count_variance(doc):
	"""Check the configured editors and validate the warehouse's account and department."""
	if not doc.meta.has_field(ACCOUNT_FIELD):
		return
	previous = doc.get_doc_before_save() if doc.get("name") and not doc.is_new() else None
	changed = [
		fieldname
		for fieldname in (ACCOUNT_FIELD, DEPARTMENT_FIELD)
		if ((previous.get(fieldname) if previous else None) or None) != (doc.get(fieldname) or None)
	]
	if not changed:
		return
	if not _may_edit(frappe.session.user):
		frappe.throw(
			_(
				"You are not permitted to change the Count Variance Account or Cost Department."
			),
			frappe.PermissionError,
		)
	if ACCOUNT_FIELD in changed and doc.get(ACCOUNT_FIELD):
		validate_variance_account(doc.get(ACCOUNT_FIELD), doc.get("company"), allow_disabled=True)
	if DEPARTMENT_FIELD in changed and doc.get(DEPARTMENT_FIELD):
		_validate_department(doc)


def _validate_department(doc):
	department = frappe.db.get_value(
		"Department", doc.get(DEPARTMENT_FIELD), ["is_group", "company", "disabled"], as_dict=True
	)
	if not department or department.get("is_group") or department.get("disabled"):
		frappe.throw(_("The Cost Department must be an enabled Department that is not a group."))
	if department.get("company") != doc.get("company"):
		frappe.throw(_("The Cost Department must belong to the company of the warehouse."))


def warehouse_terms(warehouse):
	"""(account, department) a warehouse names; (None, None) for one that names neither."""
	if not warehouse or not frappe.db.has_column("Warehouse", ACCOUNT_FIELD):
		return (None, None)
	row = frappe.db.get_value("Warehouse", warehouse, [ACCOUNT_FIELD, DEPARTMENT_FIELD], as_dict=True)
	if not row:
		return (None, None)
	return (row.get(ACCOUNT_FIELD) or None, row.get(DEPARTMENT_FIELD) or None)


def count_warehouses(doc):
	"""Every warehouse a reconciliation counts: its header and its rows'."""
	names = {row.get("warehouse") for row in doc.get("items") or [] if row.get("warehouse")}
	if doc.get("set_warehouse"):
		names.add(doc.get("set_warehouse"))
	return sorted(names)


def mark_count_reconciliation(doc):
	"""Mark a new reconciliation created by Stock Count in this transaction."""
	doc.flags.count_reconciliation = _COUNT_RECONCILIATION


def _recorded_count_account(doc, warehouses):
	if not doc.get("stock_count") or len(warehouses) != 1:
		return None
	count = frappe.db.get_value(
		"Stock Count",
		{
			"name": doc.get("stock_count"),
			"company": doc.get("company"),
			"warehouse": warehouses[0],
			"docstatus": 1,
			"posts_variance_snapshot": 1,
		},
		["stock_reconciliation", "variance_account_snapshot"],
		as_dict=True,
	)
	if count and (
		doc.flags.get("count_reconciliation") is _COUNT_RECONCILIATION
		or (doc.name and count.stock_reconciliation == doc.name)
	):
		return count.variance_account_snapshot
	return None


def apply_count_variance(doc):
	"""Set a draft Stock Reconciliation's account and department from its warehouses, subject to approval policy."""
	if int(doc.get("docstatus") or 0) != 0:
		return
	guards = frappe.get_hooks("stock_reconciliation_count_variance_guards")
	if not guards and (state := doc.get("workflow_state")) and not str(state).endswith(("Draft", "Rejected")):
		return
	for guard in guards:
		if not frappe.get_attr(guard)(doc):
			return
	if doc.get("purpose") != COUNT_PURPOSE:
		# Opening Stock / Adjustment Entry: give back what an earlier purpose of this Draft got from the warehouses.
		_undo_earlier_terms(doc, None, None)
		return
	warehouses = count_warehouses(doc)
	terms = {warehouse_terms(warehouse) for warehouse in warehouses}
	configured = any(account or department for account, department in terms)
	if configured and len(terms) > 1:
		frappe.throw(_(MIXED_MESSAGE))
	account, department = next(iter(terms)) if configured else (None, None)
	account = _recorded_count_account(doc, warehouses) or account
	_undo_earlier_terms(doc, account, department)
	if not account and not department:
		return
	if account:
		doc.set("expense_account", account)
	if department and doc.meta.has_field(DEPARTMENT_FIELD):
		doc.set(DEPARTMENT_FIELD, department)
		for row in _rows(doc):
			row.set(DEPARTMENT_FIELD, department)
	if doc.meta.has_field(MARKER_FIELD):
		doc.set(MARKER_FIELD, json.dumps([account, department]))


def _rows(doc):
	for table in doc.meta.get_table_fields():
		if frappe.get_meta(table.options).has_field(DEPARTMENT_FIELD):
			yield from doc.get(table.fieldname) or []


def _undo_earlier_terms(doc, account, department):
	"""Give back what this hook set earlier when the warehouses no longer name it (a count moved to CD STOCK, or the
	accountant cleared or changed the terms while the Draft was open). Only values still equal to the marker are
	reset, so nothing the creator typed afterwards is lost."""
	if not doc.meta.has_field(MARKER_FIELD) or not doc.get(MARKER_FIELD):
		return
	try:
		old_account, old_department = json.loads(doc.get(MARKER_FIELD))
	except (TypeError, ValueError):
		old_account = old_department = None
	if old_account and account != old_account and doc.get("expense_account") == old_account:
		doc.set(
			"expense_account",
			frappe.get_cached_value("Company", doc.get("company"), "stock_adjustment_account"),
		)
	if old_department and department != old_department:
		if doc.get(DEPARTMENT_FIELD) == old_department:
			from erpnext.setup.doctype.employee.employee import get_department_of_user

			doc.set(
				DEPARTMENT_FIELD,
				get_department_of_user(doc.get("owner") or frappe.session.user, doc.get("company")),
			)
		for row in _rows(doc):
			if row.get(DEPARTMENT_FIELD) == old_department:
				row.set(DEPARTMENT_FIELD, doc.get(DEPARTMENT_FIELD))
	doc.set(MARKER_FIELD, None)


def validate_variance_account(account, company, *, allow_disabled=False):
	data = frappe.db.get_value(
		"Account", account, ["company", "root_type", "is_group", "account_type", "disabled"], as_dict=True
	)
	if (
		not data
		or data.company != company
		or data.root_type != "Expense"
		or data.is_group
		or (data.disabled and not allow_disabled)
		or data.account_type == "Stock"
	):
		frappe.throw(
			_(
				"Count Variance Account {0} must be an enabled expense account of {1}, "
				"not a group or Stock account."
			).format(account, company)
		)
