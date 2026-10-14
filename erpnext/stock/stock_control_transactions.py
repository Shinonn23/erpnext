# Copyright (c) 2026, ERPNext contributors
# License: GNU General Public License v3. See license.txt

from collections import Counter

import frappe
from frappe import _
from frappe.utils import cint, flt

from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import (
	_inventory_signature,
)
from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import (
	is_verified_pos_consolidation as _is_verified_pos_consolidation,
)
from erpnext.accounts.doctype.pos_invoice_merge_log.pos_invoice_merge_log import (
	pos_invoice_consolidation as stock_control_pos_consolidation,
)
from erpnext.stock.stock_control import (
	get_stock_control_policy,
	is_stock_control_enabled,
	lock_stock_control_items,
	lock_stock_control_policies,
	stock_control_permission_field,
	validate_stock_control_source,
)


def _request_operation(doc):
	if doc.get("doctype") in ("Delivery Note", "Stock Entry") and not (
		doc.get("custody_loan") and cint(doc.get("is_custody_loan_transaction"))
	):
		return None
	request = doc.get("material_request")
	if doc.get("custody_loan"):
		request = frappe.db.get_value("Custody Loan", doc.get("custody_loan"), "material_request")
	if doc.get("reference_doctype") == "Material Request":
		request = doc.get("reference_name")
	if request:
		kind = frappe.db.get_value("Material Request", request, "material_request_type")
		if kind == "Customer Demo":
			return "demo"
		if kind == "Customer Borrow / Loan":
			return "loan"
	return None


def get_stock_control_operation(doc, row=None):
	"""Classify the business operation, rather than trusting a client-provided exemption."""
	doctype = doc.get("doctype")
	if (
		doctype in ("Delivery Note", "Sales Invoice", "POS Invoice")
		and doc.get("is_internal_customer")
		and not doc.get("custody_loan")
		and doc.get("customer")
		and doc.get("company")
	):
		rows = [row] if row is not None else (doc.get("items") or [])
		if rows and all(item.get("target_warehouse") for item in rows):
			customer = frappe.db.get_value(
				"Customer", doc.customer, ["is_internal_customer", "represents_company"], as_dict=True
			)
			if customer and customer.is_internal_customer and customer.represents_company == doc.company:
				return "internal_transfer"
	if doctype in ("Purchase Receipt", "Purchase Invoice"):
		return "disposal" if doc.get("is_return") else None
	if doc.get("is_return"):
		return None
	if doctype in ("Quotation", "Sales Order", "Sales Invoice", "POS Invoice"):
		return "sales"
	if doctype == "Delivery Note":
		return _request_operation(doc) or "sales"
	if doctype == "Pick List":
		return _request_operation(doc) or {
			"Material Transfer": "internal_transfer",
			"Material Transfer for Manufacture": "manufacture",
		}.get(doc.get("purpose"), "sales")
	if doctype in ("Asset Capitalization", "Stock to Asset Conversion", "Subcontracting Receipt"):
		return "manufacture"
	if doctype == "Stock Reconciliation":
		return "reconciliation"
	if doctype == "Material Request":
		return {
			"Customer Demo": "demo",
			"Customer Borrow / Loan": "loan",
			"Material Issue": "issue",
			"Material Transfer": "internal_transfer",
			"Asset Transfer": "internal_transfer",
			"Manufacture": "manufacture",
		}.get(doc.get("material_request_type"))
	if doctype == "Custody Loan":
		return _request_operation(doc) or "loan"
	if doctype == "Asset Movement":
		purpose = doc.get("purpose")
		if purpose == "Receipt":
			return None
		if purpose == "Reassignment":
			return (
				"issue"
				if any(cint(row.get("change_custodian")) for row in (doc.get("assets") or []))
				else None
			)
		return _request_operation(doc) or (
			"internal_transfer"
			if purpose == "Transfer" and not (doc.get("customer") or doc.get("recipient"))
			else "issue"
		)
	if doctype == "Stock Entry":
		purpose = doc.get("purpose")
		if purpose == "Material Transfer":
			return _request_operation(doc) or "internal_transfer"
		if purpose in ("Material Receipt", "Receive from Customer", "Subcontracting Return"):
			return None
		if purpose in ("Material Issue", "Return Raw Material to Customer"):
			return "issue"
		if purpose in (
			"Manufacture",
			"Repack",
			"Disassemble",
			"Material Transfer for Manufacture",
			"Material Consumption for Manufacture",
			"Send to Subcontractor",
			"Subcontracting Delivery",
		):
			return "manufacture"
	return None


def _tracking(row, bundle_field="serial_and_batch_bundle"):
	serials = set((row.get("serial_no") or "").split())
	batches = {row.get("batch_no")} if row.get("batch_no") else set()
	if row.get(bundle_field):
		entries = frappe.get_all(
			"Serial and Batch Entry",
			filters={"parent": row.get(bundle_field)},
			fields=["serial_no", "batch_no", "qty"],
		)
		serials.update(entry.serial_no for entry in entries if entry.serial_no)
		batches.update(entry.batch_no for entry in entries if entry.batch_no)
	return batches, serials


def _bundle_quantities(bundle):
	quantities = Counter()
	if bundle:
		for entry in frappe.get_all(
			"Serial and Batch Entry", filters={"parent": bundle}, fields=["serial_no", "batch_no", "qty"]
		):
			quantities[(entry.serial_no, entry.batch_no)] += abs(flt(entry.qty))
	return quantities


def _validate_reconciliation(doc):
	for row in doc.get("items", []):
		old = _bundle_quantities(row.get("current_serial_and_batch_bundle"))
		new = _bundle_quantities(row.get("serial_and_batch_bundle"))
		removed = old - new
		decreased = flt(row.qty) < flt(row.current_qty)
		policy = get_stock_control_policy("Warehouse", row.warehouse)
		if not cint(policy.get("allow_reconciliation")):
			frappe.throw(_("Warehouse {0} does not allow Stock Reconciliation.").format(row.warehouse))
		if decreased or removed:
			if removed:
				serials = {serial for serial, _batch in removed if serial}
				batches = {batch for _serial, batch in removed if batch}
			else:
				batches, serials = _tracking(row, "current_serial_and_batch_bundle")
			validate_stock_control_source(
				doc.company, "Warehouse", row.warehouse, "reconciliation", row.item_code, batches, serials
			)


def validate_stock_ledger_entries(entries, via_landed_cost_voucher=False):
	"""Check the complete movement before any ledger mutation, including balance resets."""
	if not entries or via_landed_cost_voucher or frappe.flags.get("stock_control_reposting"):
		return
	if entries[0].get("is_cancelled") or not is_stock_control_enabled(entries[0].get("company")):
		return
	doc = frappe.get_doc(entries[0].get("voucher_type"), entries[0].get("voucher_no"))
	warehouses = {entry.get("warehouse") for entry in entries}
	lock_stock_control_policies(warehouse_names=warehouses)
	lock_stock_control_items({entry.get("item_code") for entry in entries})
	if (
		doc.doctype == "Delivery Note"
		and doc.get("is_return")
		and doc.get("custody_loan")
		and doc.get("custody_loan_source_doctype") == "Custody Loan Return"
		and cint(doc.get("is_custody_loan_transaction"))
	):
		from erpnext.loan.doctype.custody_loan.custody_loan_core import validate_generated_transaction

		# Only the protected loan workflow may return its already-dispatched stock under a hold.
		validate_generated_transaction(doc)
		return
	if _is_verified_pos_consolidation(doc):
		return
	if doc.doctype == "Stock Reconciliation":
		_validate_reconciliation(doc)
		return
	operation = get_stock_control_operation(doc)
	rows = {
		row.name: row
		for table in ("items", "packed_items", "stock_items", "supplied_items")
		for row in (doc.get(table) or [])
	}
	for entry in entries:
		if flt(entry.get("actual_qty")) >= 0:
			continue
		row = rows.get(entry.get("voucher_detail_no"), frappe._dict())
		entry_operation = get_stock_control_operation(doc, row) or operation
		target = (
			(row.get("t_warehouse") or row.get("target_warehouse"))
			if entry_operation == "internal_transfer"
			else None
		)
		if doc.doctype in ("Delivery Note", "Sales Invoice") and doc.get("is_return"):
			entry_operation = "sales"
			if row.get("target_warehouse") == entry.get("warehouse"):
				entry_operation = "internal_transfer"
				target = row.get("warehouse")
		if not entry_operation:
			frappe.throw(_("Stock control cannot classify this outward transaction."))
		if entry_operation == "internal_transfer" and doc.get("add_to_transit"):
			# Transit is a separate stock leg, never a direct frozen-stock move into QA.
			target = row.get("t_warehouse")
		batches, serials = _tracking(entry)
		validate_stock_control_source(
			doc.company,
			"Warehouse",
			entry.get("warehouse"),
			entry_operation,
			entry.get("item_code"),
			batches,
			serials,
			target=target,
			release_reason=doc.get("stock_control_release_reason"),
		)


def validate_selected_stock_control_sources(doc):
	"""Validate source selection without treating orders as a physical dispatch."""
	if not is_stock_control_enabled(doc.get("company")) or doc.get("is_return"):
		return
	if doc.doctype == "POS Invoice" and doc.get("_action") == "update_after_submit":
		return
	if doc.doctype == "Sales Invoice" and (
		not doc.get("update_stock") or _is_verified_pos_consolidation(doc, check_packed=False)
	):
		return
	operation = get_stock_control_operation(doc)
	if not operation:
		return
	for row in (doc.get("items") or []) + (doc.get("packed_items") or []):
		operation = get_stock_control_operation(doc, row) or operation
		if row.get("asset"):
			asset = frappe.db.get_value("Asset", row.get("asset"), ["location", "company"], as_dict=True)
			if not asset or asset.company != doc.company:
				frappe.throw(_("The selected Asset must belong to the transaction Company."))
			location = asset.location
			policy = get_stock_control_policy("Location", location)
			if not cint(policy.get(stock_control_permission_field("Location", operation))):
				frappe.throw(_("Asset Location {0} does not allow {1}.").format(location, operation))
		if doc.doctype == "Material Request":
			warehouse = row.get("from_warehouse") or doc.get("set_from_warehouse")
			if operation == "issue":
				warehouse = row.get("warehouse")
		else:
			warehouse = (
				row.get("s_warehouse")
				or row.get("source_warehouse")
				or row.get("from_warehouse")
				or row.get("warehouse")
			)
		if not warehouse or row.get("is_fixed_asset"):
			continue
		policy = get_stock_control_policy("Warehouse", warehouse)
		if frappe.db.get_value("Warehouse", warehouse, "company") != doc.company:
			frappe.throw(_("The selected Warehouse must belong to the transaction Company."))
		if not cint(policy.get(stock_control_permission_field("Warehouse", operation))):
			frappe.throw(_("Warehouse {0} does not allow {1}.").format(warehouse, operation))
	if doc.get("set_warehouse") and doc.get("doctype") in ("Quotation", "Sales Order"):
		policy = get_stock_control_policy("Warehouse", doc.get("set_warehouse"))
		if not cint(policy.get("allow_sales")):
			frappe.throw(_("Warehouse {0} does not allow Sales.").format(doc.get("set_warehouse")))


def validate_pos_stock_control(doc):
	if not is_stock_control_enabled(doc.company):
		return
	rows = doc.get("items", []) + (doc.get("packed_items") or [])
	lock_stock_control_policies(
		warehouse_names={
			row.get(field) for row in rows for field in ("warehouse", "target_warehouse") if row.get(field)
		}
	)
	lock_stock_control_items({row.item_code for row in rows})
	for row in rows:
		if not frappe.get_cached_value("Item", row.item_code, "is_stock_item"):
			continue
		operation = get_stock_control_operation(doc, row) or "sales"
		warehouse = row.warehouse
		target = row.get("target_warehouse") if operation == "internal_transfer" else None
		if doc.is_return and flt(row.qty) <= 0:
			if not row.get("target_warehouse"):
				continue
			warehouse, operation, target = row.target_warehouse, "internal_transfer", row.warehouse
		batches, serials = _tracking(row)
		validate_stock_control_source(
			doc.company, "Warehouse", warehouse, operation, row.item_code, batches, serials, target=target
		)


def preserve_pos_packed_items(doc):
	"""Deferred POS posting must retain the components actually handed over at checkout."""
	if (
		not frappe.flags.get("stock_control_pos_consolidation")
		or doc.doctype != "Sales Invoice"
		or not is_stock_control_enabled(doc.company)
	):
		return False
	if not _is_verified_pos_consolidation(doc, check_packed=False):
		return False
	from frappe.model.mapper import map_child_doc

	existing = list(doc.get("packed_items") or [])
	packed_items = []
	for main in doc.items:
		pos = frappe.get_doc("POS Invoice", main.pos_invoice)
		for original in pos.get("packed_items", []):
			if original.parent_detail_docname != main.pos_invoice_item:
				continue
			packed = next(
				(
					row
					for row in existing
					if row.parent_detail_docname == main.name
					and _inventory_signature([row]) == _inventory_signature([original])
				),
				None,
			)
			if packed:
				existing.remove(packed)
			else:
				packed = map_child_doc(original, doc, {"doctype": "Packed Item"})
				for field in (
					"serial_and_batch_bundle",
					"serial_no",
					"batch_no",
					"use_serial_batch_fields",
					"storage_location",
					"target_warehouse",
					"to_storage_location",
				):
					packed.set(field, original.get(field))
			packed.parent_detail_docname = main.name
			packed_items.append(packed)
	doc.set("packed_items", packed_items)
	return True
