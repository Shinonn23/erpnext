# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import is_verified_pos_consolidation

MOVEMENT_DOCTYPES = {
	"Stock Entry",
	"Stock Reconciliation",
	"Purchase Receipt",
	"Purchase Invoice",
	"Delivery Note",
	"Sales Invoice",
	"POS Invoice",
	"Subcontracting Receipt",
	"Pick List",
	"Stock to Asset Conversion",
	"Asset Capitalization",
}


def _current_doc(doctype, name):
	"""Current (shared-lock) read, so a policy cannot change while the ledger is being posted."""
	rows = frappe.db.multisql(
		{
			"mariadb": f"SELECT * FROM `tab{doctype}` WHERE name=%s LOCK IN SHARE MODE",
			"postgres": f'SELECT * FROM "tab{doctype}" WHERE name=%s FOR SHARE',
		},
		(name,),
		as_dict=True,
	)
	if not rows:
		frappe.throw(_("{0} {1} does not exist.").format(doctype, name))
	return frappe._dict(rows[0])


def lock_warehouse_policies(warehouse_names):
	# Warehouse rows precede type rows so incoming and outgoing movements share the same lock order.
	types = set()
	for name in sorted(set(filter(None, warehouse_names))):
		row = _current_doc("Warehouse", name)
		if row.get("warehouse_type"):
			types.add(row.warehouse_type)
	for name in sorted(types):
		_current_doc("Warehouse Type", name)


def validate_warehouse_operation(warehouse, operation, outgoing=False, cancellation=False, for_update=False):
	if not warehouse:
		return
	location = _current_doc("Warehouse", warehouse) if for_update else frappe.get_doc("Warehouse", warehouse)
	if location.is_group:
		frappe.throw(_("คลังกลุ่ม {0} ไม่สามารถทำรายการสต็อกได้ กรุณาเลือกคลังย่อย").format(warehouse))
	if not location.warehouse_type:
		return
	policy = (
		_current_doc("Warehouse Type", location.warehouse_type)
		if for_update
		else frappe.get_doc("Warehouse Type", location.warehouse_type)
	)
	# Reversals restore the original transaction even if its type policy has since changed.
	if cancellation:
		return
	if operation == "loan" and location.warehouse_type == "Transit":
		operation = "transfer"
	if not cint(policy.get(f"allow_{operation}")):
		frappe.throw(
			_("คลัง {0} ประเภท {1} ไม่อนุญาตรายการ {2} กรุณาเลือกคลังที่เหมาะสม").format(
				warehouse, policy.name, _(operation)
			)
		)
	if outgoing and policy.outgoing_role and policy.outgoing_role not in frappe.get_roles():
		frappe.throw(
			_("การนำสต็อกออกจากคลัง {0} ต้องมีบทบาท {1}").format(warehouse, policy.outgoing_role),
			frappe.PermissionError,
		)


def is_loan_movement(doc):
	if doc.doctype == "Pick List" and doc.get("custody_loan"):
		from erpnext.loan.doctype.custody_loan.custody_loan_core import validate_custody_loan_pick_list

		validate_custody_loan_pick_list(doc)
		return True
	if doc.doctype not in ("Stock Entry", "Delivery Note") or not doc.get("custody_loan"):
		return False
	from erpnext.loan.doctype.custody_loan.custody_loan_core import validate_generated_transaction

	validate_generated_transaction(doc)
	if doc.doctype == "Stock Entry":
		if (
			doc.get("custody_loan_source_doctype") != "Custody Loan Adjustment"
			or doc.purpose != "Material Issue"
		):
			frappe.throw(_("คลังยืมรับเฉพาะรายการที่สร้างจากเอกสารยืม คืน หรือปรับปรุงการยืมที่เชื่อมโยงถูกต้อง"))
		loan = frappe.get_doc("Custody Loan", doc.custody_loan)
		adjustment = frappe.get_doc("Custody Loan Adjustment", doc.custody_loan_source_name)
		if (
			adjustment.docstatus != 1
			or sorted((row.custody_loan_item, flt(row.qty)) for row in doc.get("items"))
			!= sorted((row.custody_loan_item, flt(row.qty)) for row in adjustment.get("items"))
			or any(
				row.s_warehouse != loan.customer_loan_warehouse or row.t_warehouse for row in doc.get("items")
			)
		):
			frappe.throw(_("รายการปรับปรุงคลังยืมต้องตรงกับเอกสารปรับปรุงการยืมและคลังยืมของลูกค้า"))
	return True


def movement_operation(doc, outgoing, loan=False, row=None):
	if (
		doc.doctype in ("Delivery Note", "Sales Invoice", "POS Invoice")
		and doc.get("is_internal_customer")
		and row
		and row.get("target_warehouse")
	):
		return "loan" if loan else "transfer"
	if doc.get("is_internal_supplier"):
		return "loan" if loan else "transfer"
	if loan:
		return "loan"
	if doc.doctype == "Stock Reconciliation":
		return "reconciliation"
	if doc.doctype == "Pick List":
		return "sales" if doc.purpose == "Delivery" else "transfer"
	if doc.doctype == "Stock Entry":
		if doc.purpose in (
			"Material Transfer",
			"Material Transfer for Manufacture",
			"Send to Subcontractor",
			"Subcontracting Delivery",
		):
			return "transfer"
		return "issue" if outgoing else "receipt"
	if doc.doctype in ("Delivery Note", "Sales Invoice", "POS Invoice"):
		return "sales"
	return "issue" if outgoing else "receipt"


def validate_document_policy(doc):
	if doc.doctype not in MOVEMENT_DOCTYPES:
		return
	if doc.doctype in ("Sales Invoice", "Purchase Invoice", "POS Invoice") and not doc.get("update_stock"):
		return
	cancellation = doc.docstatus == 2
	verified_pos = is_verified_pos_consolidation(doc, check_packed=False)
	loan = False if cancellation else is_loan_movement(doc)
	for table in ("items", "packed_items", "supplied_items", "locations", "stock_items"):
		for row in doc.get(table) or []:
			item = row.get("item_code") or row.get("rm_item_code")
			if not item or not frappe.get_cached_value("Item", item, "is_stock_item"):
				continue
			if doc.doctype == "Stock Entry":
				legs = [(row.get("s_warehouse"), True), (row.get("t_warehouse"), False)]
			else:
				outgoing = table == "stock_items" or doc.doctype in (
					"Delivery Note",
					"Sales Invoice",
					"POS Invoice",
					"Pick List",
				)
				if doc.get("is_return"):
					outgoing = not outgoing
				if doc.doctype == "Stock Reconciliation":
					outgoing = reconciliation_is_outgoing(row)
				legs = [
					(row.get("warehouse"), outgoing),
					(row.get("target_warehouse"), not outgoing),
					(row.get("rejected_warehouse"), outgoing),
					(row.get("from_warehouse"), True),
				]
				if table == "supplied_items" and row.get("rm_item_code"):
					legs.append((doc.get("supplier_warehouse"), not bool(doc.get("is_return"))))
			for warehouse, outgoing in legs:
				validate_warehouse_operation(
					warehouse,
					movement_operation(doc, outgoing, loan, row),
					outgoing,
					cancellation or verified_pos,
				)


def reconciliation_is_outgoing(row):
	return bool(row) and (
		flt(row.get("qty")) < flt(row.get("current_qty")) or flt(row.get("amount_difference")) < 0
	)


def validate_stock_ledger_policy(entries, via_landed_cost_voucher=False):
	lock_warehouse_policies({entry.get("warehouse") for entry in entries})
	vouchers = {}
	for entry in entries:
		key = (entry.get("voucher_type"), entry.get("voucher_no"))
		if key not in vouchers:
			vouchers[key] = frappe.get_doc(*key)
			# Ledger vouchers are reloaded from the database; their posting action is server-owned.
			vouchers[key]._action = "cancel" if vouchers[key].docstatus == 2 else "submit"
		doc = vouchers[key]
		verified_pos = is_verified_pos_consolidation(doc)
		cancellation = bool(entry.get("is_cancelled")) and (doc.docstatus == 2 or via_landed_cost_voucher)
		outgoing = flt(entry.get("actual_qty")) < 0
		row = next(
			(row for row in doc.get("items") or [] if row.name == entry.get("voucher_detail_no")), None
		)
		if doc.doctype == "Stock Reconciliation":
			outgoing = (
				outgoing or reconciliation_is_outgoing(row) or flt(entry.get("stock_value_difference")) < 0
			)
		operation = movement_operation(doc, outgoing, False if cancellation else is_loan_movement(doc), row)
		validate_warehouse_operation(
			entry.get("warehouse"),
			operation,
			outgoing,
			cancellation
			or verified_pos
			or via_landed_cost_voucher
			or bool(frappe.flags.get("stock_control_reposting")),
			for_update=True,
		)


@frappe.whitelist()
def validate_document(doc):
	doc = frappe.get_doc(frappe.parse_json(doc))
	doc.check_permission("read")
	# Browser flags must never qualify a transaction as a generated Loan movement.
	doc.flags.clear()
	doc._action = "save"
	validate_document_policy(doc)
