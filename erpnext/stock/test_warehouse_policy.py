# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import logging
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.stock import warehouse_policy
from erpnext.stock.doctype.warehouse_type.warehouse_type import WarehouseType


class TestWarehousePolicy(unittest.TestCase):
	def setUp(self):
		self.warehouse = frappe._dict(is_group=0, warehouse_type="Normal")
		self.policy = frappe._dict(
			name="Normal",
			outgoing_role=None,
			**{
				f"allow_{operation}": 1
				for operation in ("receipt", "issue", "transfer", "sales", "loan", "reconciliation")
			},
		)
		self.addCleanup(patch.stopall)
		patch.object(
			frappe,
			"get_doc",
			side_effect=lambda doctype, name, **kwargs: (
				self.warehouse if doctype == "Warehouse" else self.policy
			),
		).start()
		patch.object(frappe, "get_roles", return_value=["Stock User"]).start()
		patch.object(
			frappe,
			"throw",
			side_effect=lambda message, exc=frappe.ValidationError: (_ for _ in ()).throw(exc(message)),
		).start()
		patch.object(warehouse_policy, "_", side_effect=lambda text: text).start()
		patch.object(frappe, "flags", frappe._dict()).start()
		patch.object(warehouse_policy, "lock_warehouse_policies").start()
		patch.object(
			warehouse_policy, "_current_doc", side_effect=lambda doctype, name: frappe.get_doc(doctype, name)
		).start()

	def test_normal_and_legacy_allow_movements(self):
		for operation in ("receipt", "issue", "transfer", "sales", "loan", "reconciliation"):
			warehouse_policy.validate_warehouse_operation("WH", operation, outgoing=True)
		self.warehouse.warehouse_type = None
		self.policy.allow_sales = 0
		warehouse_policy.validate_warehouse_operation("WH", "sales", outgoing=True)

	def test_restricted_type_matrix(self):
		for name, allowed in {
			"Transit": {"transfer"},
			"Loan": {"loan"},
			"Quarantine": {"receipt", "transfer"},
			"Write-Off": {"receipt", "transfer", "reconciliation"},
		}.items():
			self.policy.name = name
			for operation in ("receipt", "issue", "transfer", "sales", "loan", "reconciliation"):
				self.policy[f"allow_{operation}"] = operation in allowed
				if operation in allowed:
					warehouse_policy.validate_warehouse_operation("WH", operation)
				else:
					with self.assertRaises(frappe.ValidationError):
						warehouse_policy.validate_warehouse_operation("WH", operation)

	def test_cancellation_restores_the_original_movement(self):
		self.policy.allow_sales = 0
		with self.assertRaises(frappe.ValidationError):
			warehouse_policy.validate_warehouse_operation("WH", "sales")
		warehouse_policy.validate_warehouse_operation("WH", "sales", cancellation=True)

	def test_group_never_allows_stock(self):
		self.warehouse.is_group = 1
		with self.assertRaises(frappe.ValidationError):
			warehouse_policy.validate_warehouse_operation("WH", "receipt", cancellation=True)

	def test_outgoing_role_does_not_block_receipts(self):
		self.policy.outgoing_role = "Stock Manager"
		warehouse_policy.validate_warehouse_operation("WH", "transfer", outgoing=False)
		with self.assertRaises(frappe.PermissionError):
			warehouse_policy.validate_warehouse_operation("WH", "transfer", outgoing=True)
		with patch.object(frappe, "get_roles", return_value=["Stock Manager"]):
			warehouse_policy.validate_warehouse_operation("WH", "transfer", outgoing=True)

	def workflow_review_documents(self, **changes):
		def document(values, children=()):
			doc = Mock(**values)
			doc.get = lambda field, default=None: values.get(field, default)
			doc.get_valid_dict.return_value = {
				key: value for key, value in values.items() if key not in {"items", "_action"}
			}
			doc.get_all_children.return_value = children
			return doc

		row_values = dict(item_code="Item", s_warehouse="WH", qty=1, basic_rate=100)
		previous_row = document(row_values)
		row = document(row_values | changes.pop("row_changes", {}))
		values = dict(
			doctype="Stock Entry",
			name="SE",
			docstatus=0,
			purpose="Material Transfer",
			owner="stock",
			workflow_state="Pending Quality Review",
			items=[previous_row],
		)
		previous = document(values, [previous_row])
		doc = document(
			values | dict(workflow_state="Pending Stock Review", items=[row], _action="save") | changes,
			[row],
		)
		doc.get_doc_before_save.return_value = previous
		return previous, doc

	def test_unchanged_draft_review_does_not_require_dispatch_role(self):
		from frappe.model import workflow

		previous, doc = self.workflow_review_documents()
		self.policy.outgoing_role = "Stock Manager"
		with (
			patch.object(frappe, "get_cached_value", return_value=1),
			patch.object(frappe, "session", frappe._dict(user="quality")),
			patch.object(workflow, "get_workflow_name", return_value="Workflow"),
			patch.object(workflow, "get_workflow", return_value=frappe._dict(workflow_state_field="workflow_state")),
			patch.object(workflow, "get_transitions", return_value=[frappe._dict(next_state="Pending Stock Review")]),
		):
			warehouse_policy.validate_document_policy(doc)
			self.policy.allow_transfer = 0
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.validate_document_policy(doc)

	def test_review_allows_automatic_posting_time_but_keeps_explicit_time_locked(self):
		from frappe.model import workflow

		previous, doc = self.workflow_review_documents(posting_time="12:00:00")
		with (
			patch.object(frappe, "session", frappe._dict(user="quality")),
			patch.object(workflow, "get_workflow_name", return_value="Workflow"),
			patch.object(workflow, "get_workflow", return_value=frappe._dict(workflow_state_field="workflow_state")),
			patch.object(workflow, "get_transitions", return_value=[frappe._dict(next_state="Pending Stock Review")]),
		):
			self.assertTrue(warehouse_policy._is_unchanged_draft_workflow_transition(doc))
			previous.get_valid_dict.return_value["set_posting_time"] = 1
			doc.get_valid_dict.return_value["set_posting_time"] = 1
			previous_get, current_get = previous.get, doc.get
			previous.get = lambda field, default=None: (
				1 if field == "set_posting_time" else previous_get(field, default)
			)
			doc.get = lambda field, default=None: 1 if field == "set_posting_time" else current_get(field, default)
			self.assertFalse(warehouse_policy._is_unchanged_draft_workflow_transition(doc))

	def test_draft_review_cannot_bypass_dispatch_role_for_edits_or_submission(self):
		from frappe.model import workflow

		self.policy.outgoing_role = "Stock Manager"
		with (
			patch.object(frappe, "get_cached_value", return_value=1),
			patch.object(frappe, "session", frappe._dict(user="quality")),
			patch.object(workflow, "get_workflow_name", return_value="Workflow"),
			patch.object(workflow, "get_workflow", return_value=frappe._dict(workflow_state_field="workflow_state")),
			patch.object(workflow, "get_transitions", return_value=[frappe._dict(next_state="Pending Stock Review")]),
		):
			for changes in (
				{"row_changes": {"qty": 2}},
				{"row_changes": {"s_warehouse": "OTHER"}},
				{"row_changes": {"basic_rate": 200}},
				{"remarks": "Changed during approval"},
				{"owner": "quality"},
				{"workflow_state": "Pending Quality Review"},
				{"docstatus": 1, "_action": "submit"},
			):
				with self.subTest(changes=changes):
					previous, doc = self.workflow_review_documents(**changes)
					with self.assertRaises(frappe.PermissionError):
						warehouse_policy.validate_document_policy(doc)
			previous, doc = self.workflow_review_documents()
			doc.get_doc_before_save.return_value = None
			with self.assertRaises(frappe.PermissionError):
				warehouse_policy.validate_document_policy(doc)
			doc.get_doc_before_save.return_value = {"docstatus": 0}
			with self.assertRaises(frappe.PermissionError):
				warehouse_policy.validate_document_policy(doc)

	def test_draft_review_still_requires_workflow_and_self_approval_access(self):
		from frappe.model import workflow

		previous, doc = self.workflow_review_documents()
		self.policy.outgoing_role = "Stock Manager"
		with (
			patch.object(frappe, "get_cached_value", return_value=1),
			patch.object(frappe, "session", frappe._dict(user="quality")),
			patch.object(workflow, "get_workflow_name", return_value="Workflow"),
			patch.object(workflow, "get_workflow", return_value=frappe._dict(workflow_state_field="workflow_state")),
			patch.object(workflow, "get_transitions", return_value=[]) as transitions,
		):
			with self.assertRaises(frappe.PermissionError):
				warehouse_policy.validate_document_policy(doc)
			transitions.return_value = [frappe._dict(next_state="Other State")]
			with self.assertRaises(frappe.PermissionError):
				warehouse_policy.validate_document_policy(doc)
			transitions.return_value = [frappe._dict(next_state="Pending Stock Review")]
			with patch.object(frappe, "session", frappe._dict(user="stock")):
				with self.assertRaises(frappe.PermissionError):
					warehouse_policy.validate_document_policy(doc)

	def test_linked_loan_transit_leg_uses_transfer_policy(self):
		self.warehouse.warehouse_type = "Transit"
		self.policy.allow_loan = 0
		warehouse_policy.validate_warehouse_operation("WH", "loan")
		self.policy.allow_transfer = 0
		with self.assertRaises(frappe.ValidationError):
			warehouse_policy.validate_warehouse_operation("WH", "loan")

	def test_reconciliation_quantity_and_value_reductions_need_outgoing_role(self):
		self.policy.outgoing_role = "Stock Manager"
		for row in (
			frappe._dict(qty=1, current_qty=2, amount_difference=0),
			frappe._dict(qty=2, current_qty=2, amount_difference=-1),
		):
			self.assertTrue(warehouse_policy.reconciliation_is_outgoing(row))
			with self.assertRaises(frappe.PermissionError):
				warehouse_policy.validate_warehouse_operation(
					"WH", "reconciliation", outgoing=warehouse_policy.reconciliation_is_outgoing(row)
				)

	def test_document_blocks_disallowed_stock_entry_leg(self):
		doc = frappe._dict(
			doctype="Stock Entry",
			purpose="Material Receipt",
			docstatus=0,
			items=[frappe._dict(item_code="Item", t_warehouse="WH")],
		)
		with patch.object(frappe, "get_cached_value", return_value=1):
			warehouse_policy.validate_document_policy(doc)
			self.policy.allow_receipt = 0
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.validate_document_policy(doc)

	def test_asset_stock_consumption_uses_issue_policy_and_outgoing_role(self):
		with patch.object(frappe, "get_cached_value", return_value=1):
			for doctype in ("Stock to Asset Conversion", "Asset Capitalization"):
				doc = frappe._dict(
					doctype=doctype,
					docstatus=0,
					stock_items=[frappe._dict(item_code="Item", warehouse="WH", qty=1)],
				)
				self.policy.allow_receipt = 0
				warehouse_policy.validate_document_policy(doc)
				self.policy.allow_issue = 0
				with self.assertRaises(frappe.ValidationError):
					warehouse_policy.validate_document_policy(doc)
				self.policy.allow_issue = 1
				self.policy.outgoing_role = "Stock Manager"
				with self.assertRaises(frappe.PermissionError):
					warehouse_policy.validate_document_policy(doc)
				self.policy.outgoing_role = None
				self.warehouse.is_group = 1
				doc.docstatus = 2
				with self.assertRaises(frappe.ValidationError):
					warehouse_policy.validate_document_policy(doc)
				self.warehouse.is_group = 0

	def test_stock_entry_controller_draft_validation_checks_policy(self):
		with patch.object(frappe, "logger", return_value=logging.getLogger("offline")):
			from erpnext.stock.doctype.stock_entry.stock_entry import StockEntry
		data = dict(
			doctype="Stock Entry",
			docstatus=0,
			purpose="Material Receipt",
			work_order=None,
			from_bom=0,
			items=[frappe._dict(item_code="Item", t_warehouse="WH", qty=1)],
		)
		doc = Mock(**data)
		doc.get = lambda field, default=None: data.get(field, default)
		self.policy.allow_receipt = 0
		with patch.object(frappe, "get_cached_value", return_value=1):
			with self.assertRaises(frappe.ValidationError):
				StockEntry.validate(doc)

	def test_asset_stock_controller_draft_validation_checks_policy(self):
		with patch.object(frappe, "logger", return_value=logging.getLogger("offline")):
			from erpnext.assets.doctype.asset_capitalization.asset_capitalization import AssetCapitalization
			from erpnext.assets.doctype.stock_to_asset_conversion.stock_to_asset_conversion import (
				StocktoAssetConversion,
			)
		methods = (
			"set_department",
			"validate_company_settings",
			"sync_pick_list_selections",
			"sync_material_request_rows",
			"validate_storage_locations",
			"set_missing_values",
			"validate_stock_items",
			"build_asset_preview",
			"calculate_totals",
			"validate_posting_time",
			"validate_target_item",
			"validate_target_asset",
			"validate_consumed_stock_item",
			"validate_consumed_asset_item",
			"validate_service_item",
			"set_warehouse_details",
			"set_asset_values",
			"set_title",
			"validate_inventory_dimension_mandatory",
		)
		self.policy.allow_issue = 0
		with (
			patch.object(frappe, "get_cached_value", return_value=1),
			patch("erpnext.stock.doctype.storage_location.storage_location.set_default_storage_locations"),
		):
			for doctype, controller in (
				("Stock to Asset Conversion", StocktoAssetConversion),
				("Asset Capitalization", AssetCapitalization),
			):
				doc = SimpleNamespace(
					doctype=doctype,
					docstatus=0,
					stock_items=[frappe._dict(item_code="Item", warehouse="WH", qty=1)],
					**{method: Mock() for method in methods},
				)
				doc.get = lambda field, default=None: getattr(doc, field, default)
				with self.assertRaises(frappe.ValidationError):
					controller.validate(doc)

	def test_stockless_invoice_and_planning_are_ignored(self):
		with patch.object(warehouse_policy, "is_loan_movement") as loan:
			for doctype in (
				"Sales Invoice",
				"Purchase Invoice",
				"POS Invoice",
				"Purchase Order",
				"Material Request",
			):
				warehouse_policy.validate_document_policy(frappe._dict(doctype=doctype, update_stock=0))
			loan.assert_not_called()

	def test_loan_and_internal_transfers_do_not_become_sales(self):
		doc = frappe._dict(doctype="Delivery Note", is_internal_customer=1)
		row = frappe._dict(target_warehouse="Loan WH")
		self.assertEqual(warehouse_policy.movement_operation(doc, True, loan=True, row=row), "loan")
		self.assertEqual(warehouse_policy.movement_operation(doc, True, row=row), "transfer")
		doc.is_internal_customer = 0
		self.assertEqual(warehouse_policy.movement_operation(doc, True, row=row), "sales")

	def test_loan_marker_alone_does_not_qualify(self):
		doc = frappe._dict(doctype="Delivery Note", is_custody_loan_transaction=1)
		self.assertFalse(warehouse_policy.is_loan_movement(doc))
		doc.custody_loan = "Loan"
		core = SimpleNamespace(
			validate_generated_transaction=Mock(side_effect=frappe.ValidationError("invalid link"))
		)
		with patch.dict(sys.modules, {"erpnext.loan.doctype.custody_loan.custody_loan_core": core}):
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.is_loan_movement(doc)

	def test_ledger_cancellation_uses_stored_voucher_status(self):
		doc = frappe._dict(doctype="Stock Entry", purpose="Material Issue", docstatus=1, items=[])
		entry = frappe._dict(
			voucher_type="Stock Entry", voucher_no="SE", warehouse="WH", actual_qty=-1, is_cancelled=1
		)
		self.policy.allow_issue = 0
		with patch.object(
			frappe,
			"get_doc",
			side_effect=lambda doctype, name, **kwargs: (
				doc if doctype == doc.doctype else self.warehouse if doctype == "Warehouse" else self.policy
			),
		):
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.validate_stock_ledger_policy([entry])
			doc.docstatus = 2
			warehouse_policy.validate_stock_ledger_policy([entry])
			self.warehouse.is_group = 1
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.validate_stock_ledger_policy([entry])

	def test_loan_adjustment_matches_source_and_custody_warehouse(self):
		doc = frappe._dict(
			doctype="Stock Entry",
			purpose="Material Issue",
			custody_loan="Loan",
			custody_loan_source_doctype="Custody Loan Adjustment",
			custody_loan_source_name="Adjustment",
			items=[frappe._dict(custody_loan_item="Item", qty=2, s_warehouse="Loan WH", t_warehouse=None)],
		)
		loan = frappe._dict(customer_loan_warehouse="Loan WH")
		adjustment = frappe._dict(docstatus=1, items=[frappe._dict(custody_loan_item="Item", qty=2)])
		core = SimpleNamespace(validate_generated_transaction=Mock())
		with (
			patch.dict(sys.modules, {"erpnext.loan.doctype.custody_loan.custody_loan_core": core}),
			patch.object(frappe, "get_doc", side_effect=[loan, adjustment, loan, adjustment]),
		):
			self.assertTrue(warehouse_policy.is_loan_movement(doc))
			doc["items"][0].s_warehouse = "Other WH"
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.is_loan_movement(doc)

	def test_landed_cost_reversal_still_checks_the_warehouse(self):
		doc = frappe._dict(doctype="Purchase Receipt", docstatus=1, items=[])
		entry = frappe._dict(
			voucher_type="Purchase Receipt", voucher_no="PR", warehouse="WH", actual_qty=-1, is_cancelled=1
		)
		self.policy.allow_issue = 0
		with patch.object(
			frappe,
			"get_doc",
			side_effect=lambda doctype, name, **kwargs: (
				doc if doctype == doc.doctype else self.warehouse if doctype == "Warehouse" else self.policy
			),
		):
			warehouse_policy.validate_stock_ledger_policy([entry], via_landed_cost_voucher=True)
			self.warehouse.is_group = 1
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.validate_stock_ledger_policy([entry], via_landed_cost_voucher=True)

	def test_policy_permissions_apply_with_ignore_permissions(self):
		with (
			patch.object(frappe, "session", frappe._dict(user="ordinary")),
			patch("erpnext.stock.doctype.warehouse_type.warehouse_type._", side_effect=lambda text: text),
		):
			with self.assertRaises(frappe.PermissionError):
				WarehouseType.validate_policy_permission(
					frappe._dict(flags=frappe._dict(ignore_permissions=True))
				)
			with patch.object(frappe, "get_roles", return_value=["Stock Manager"]):
				WarehouseType.validate_policy_permission(frappe._dict())

	def test_supplied_materials_validate_supplier_header_warehouse(self):
		doc = frappe._dict(
			doctype="Subcontracting Receipt",
			docstatus=0,
			supplier_warehouse="WH",
			supplied_items=[frappe._dict(rm_item_code="RM", consumed_qty=1)],
		)
		self.policy.allow_issue = 0
		with patch.object(frappe, "get_cached_value", return_value=1):
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.validate_document_policy(doc)

	def test_pick_list_cancel_checks_policy_before_linked_actions(self):
		with patch.object(frappe, "logger", return_value=logging.getLogger("offline")):
			from erpnext.stock.doctype.pick_list.pick_list import PickList
		doc = frappe._dict(
			doctype="Pick List",
			docstatus=2,
			purpose="Delivery",
			locations=[frappe._dict(item_code="Item", warehouse="WH")],
		)
		self.warehouse.is_group = 1
		with patch.object(frappe, "get_cached_value", return_value=1):
			with self.assertRaises(frappe.ValidationError):
				PickList.before_cancel(doc)

	def test_reservation_mutations_check_warehouse_without_operation_restriction(self):
		with patch.object(frappe, "logger", return_value=logging.getLogger("offline")):
			from erpnext.stock.doctype.stock_reservation_entry.stock_reservation_entry import (
				StockReservationEntry,
			)
		doc = SimpleNamespace(
			warehouse="WH", validate_reserved_entries=Mock(), set_reservation_based_on=Mock()
		)
		doc.validate_warehouse_policy = lambda: StockReservationEntry.validate_warehouse_policy(doc)
		self.policy.allow_transfer = 0
		StockReservationEntry.before_cancel(doc)
		self.warehouse.is_group = 1
		for method in (
			StockReservationEntry.before_submit,
			StockReservationEntry.before_cancel,
			StockReservationEntry.before_update_after_submit,
			StockReservationEntry.update_reserved_stock_in_bin,
		):
			with self.assertRaises(frappe.ValidationError):
				method(doc)
		doc.set_reservation_based_on.assert_not_called()

	def test_direct_ledger_cannot_claim_landed_cost_reversal_context(self):
		with patch.object(frappe, "logger", return_value=logging.getLogger("offline")):
			from erpnext.stock.doctype.stock_ledger_entry.stock_ledger_entry import StockLedgerEntry
		doc = frappe._dict(doctype="Stock Entry", purpose="Material Issue", docstatus=1, items=[])
		entry = frappe._dict(
			voucher_type="Stock Entry",
			voucher_no="SE",
			warehouse="WH",
			actual_qty=-1,
			is_cancelled=1,
			via_landed_cost_voucher=1,
			flags=frappe._dict(),
		)
		self.policy.allow_issue = 0
		with (
			patch.object(frappe, "flags", frappe._dict()),
			patch.object(
				frappe,
				"get_doc",
				side_effect=lambda doctype, name, **kwargs: (
					doc
					if doctype == doc.doctype
					else self.warehouse
					if doctype == "Warehouse"
					else self.policy
				),
			),
		):
			with self.assertRaises(frappe.ValidationError):
				StockLedgerEntry.validate(entry)

	def test_reloaded_shipment_loan_voucher_has_server_posting_action(self):
		with patch.object(frappe, "logger", return_value=logging.getLogger("offline")):
			from erpnext.loan.doctype.custody_loan import custody_loan_core as core
		doc = SimpleNamespace(
			doctype="Delivery Note",
			name="DN",
			docstatus=1,
			company="Co",
			flags=frappe._dict(),
			is_system_generated=1,
			is_custody_loan_transaction=1,
			custody_loan="Loan",
			custody_loan_source_doctype="Custody Loan",
			custody_loan_source_name="Loan",
			items=[],
			is_internal_customer=1,
			represents_company="Co",
			customer="IC",
			is_return=0,
			requires_shipment=1,
		)
		doc.get = lambda field, default=None: getattr(doc, field, default)
		loan = SimpleNamespace(name="Loan", docstatus=1, company="Co", loan_type="Stock")
		stored = frappe._dict(
			is_custody_loan_transaction=1,
			custody_loan="Loan",
			custody_loan_source_doctype="Custody Loan",
			custody_loan_source_name="Loan",
		)

		def db_value(doctype, name, fields, **kwargs):
			if doctype == "Customer":
				return "IC"
			return stored if isinstance(fields, list) else 1

		entry = frappe._dict(voucher_type="Delivery Note", voucher_no="DN", warehouse="WH", actual_qty=-1)
		with (
			patch.object(frappe, "db", SimpleNamespace(exists=lambda *args: True, get_value=db_value)),
			patch.object(
				frappe,
				"get_doc",
				side_effect=lambda doctype, name, **kwargs: (
					doc
					if doctype == "Delivery Note"
					else loan
					if doctype == "Custody Loan"
					else self.warehouse
					if doctype == "Warehouse"
					else self.policy
				),
			),
			patch.object(core, "_", side_effect=lambda text: text),
			patch.object(
				core,
				"validate_delivery_note_shipment_link",
				side_effect=frappe.ValidationError("Shipment must be submitted"),
			) as shipment,
		):
			with self.assertRaisesRegex(frappe.ValidationError, "Shipment must be submitted"):
				warehouse_policy.validate_stock_ledger_policy([entry])
			shipment.assert_called_once_with(doc)
			self.assertEqual(doc._action, "submit")

	def test_verified_pos_consolidation_skips_changed_dispatch_policy(self):
		doc = frappe._dict(
			doctype="Sales Invoice",
			docstatus=1,
			update_stock=1,
			items=[frappe._dict(item_code="Item", warehouse="WH")],
		)
		self.policy.allow_sales = 0
		self.policy.outgoing_role = "Stock Manager"
		with (
			patch.object(warehouse_policy, "is_verified_pos_consolidation", return_value=True),
			patch.object(frappe, "get_cached_value", return_value=1),
		):
			warehouse_policy.validate_document_policy(doc)
		with patch.object(frappe, "get_cached_value", return_value=1):
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.validate_document_policy(doc)

	def test_landed_cost_reapplication_needs_no_new_receipt_permission(self):
		doc = frappe._dict(doctype="Purchase Receipt", docstatus=1, items=[])
		entry = frappe._dict(voucher_type="Purchase Receipt", voucher_no="PR", warehouse="WH", actual_qty=1)
		self.policy.allow_receipt = 0
		with patch.object(
			frappe,
			"get_doc",
			side_effect=lambda doctype, name, **kwargs: (
				doc if doctype == doc.doctype else self.warehouse if doctype == "Warehouse" else self.policy
			),
		):
			warehouse_policy.validate_stock_ledger_policy([entry], via_landed_cost_voucher=True)
			warehouse_policy.lock_warehouse_policies.assert_called_with({"WH"})
			self.warehouse.is_group = 1
			with self.assertRaises(frappe.ValidationError):
				warehouse_policy.validate_stock_ledger_policy([entry], via_landed_cost_voucher=True)

	def test_quality_manager_may_edit_policy(self):
		with (
			patch.object(frappe, "session", frappe._dict(user="quality")),
			patch.object(frappe, "get_roles", return_value=["Quality Manager"]),
		):
			WarehouseType.validate_policy_permission(frappe._dict())


class TestWarehouseTypeSummary(unittest.TestCase):
	def setUp(self):
		self.addCleanup(patch.stopall)
		patch.object(frappe, "has_permission", return_value=True).start()
		patch.object(warehouse_policy, "_", side_effect=lambda text: text).start()

	def test_no_type_means_no_restriction(self):
		summary = warehouse_policy.get_warehouse_type_summary(None)
		self.assertFalse(summary["restricted"])
		self.assertEqual(summary["operations"], [])

	def test_summary_reports_flags_role_and_documents(self):
		policy = frappe._dict(name="Lab", outgoing_role="Stock Manager", allow_receipt=1, allow_issue=0)
		patch.object(frappe, "get_cached_doc", return_value=policy).start()
		summary = warehouse_policy.get_warehouse_type_summary("Lab")
		by_operation = {row["operation"]: row for row in summary["operations"]}
		self.assertEqual(list(by_operation), list(warehouse_policy.OPERATION_ORDER))
		self.assertTrue(by_operation["receipt"]["allowed"])
		self.assertFalse(by_operation["issue"]["allowed"])
		self.assertFalse(by_operation["loan"]["allowed"])
		self.assertEqual(by_operation["issue"]["documents"], warehouse_policy.OPERATION_DOCUMENTS["issue"])
		self.assertEqual(summary["outgoing_role"], "Stock Manager")
		self.assertFalse(summary["loan_counts_as_transfer"])

	def test_transit_loan_counts_as_transfer(self):
		policy = frappe._dict(name="Transit", outgoing_role=None)
		patch.object(frappe, "get_cached_doc", return_value=policy).start()
		self.assertTrue(warehouse_policy.get_warehouse_type_summary("Transit")["loan_counts_as_transfer"])

	def test_requires_read_permission(self):
		patch.object(frappe, "has_permission", return_value=False).start()
		patch.object(
			frappe,
			"throw",
			side_effect=lambda message, exc=frappe.ValidationError: (_ for _ in ()).throw(exc(message)),
		).start()
		with self.assertRaises(frappe.PermissionError):
			warehouse_policy.get_warehouse_type_summary("Lab")

	def test_every_operation_has_documents(self):
		self.assertEqual(set(warehouse_policy.OPERATION_DOCUMENTS), set(warehouse_policy.OPERATION_ORDER))

	def test_summary_documents_match_movement_operation(self):
		"""Every line of the summary is proven against the real classifier, so the two cannot drift."""
		for operation, text, spec in warehouse_policy.OPERATION_RULES:
			with self.subTest(text=text):
				fields = {k: v for k, v in spec.items() if k not in ("outgoing", "loan")}
				doc = frappe._dict(fields)
				row = frappe._dict(target_warehouse="WH-T") if spec.get("is_internal_customer") else None
				self.assertEqual(
					warehouse_policy.movement_operation(
						doc, bool(spec.get("outgoing")), bool(spec.get("loan")), row
					),
					operation,
				)

	def test_summary_covers_branches_of_movement_operation(self):
		cases = [
			# (doc fields, outgoing, expected)
			({"doctype": "Stock Entry", "purpose": "Manufacture"}, True, "issue"),
			({"doctype": "Stock Entry", "purpose": "Manufacture"}, False, "receipt"),
			({"doctype": "Stock Entry", "purpose": "Repack"}, True, "issue"),
			({"doctype": "Stock Entry", "purpose": "Material Consumption for Manufacture"}, True, "issue"),
			({"doctype": "Stock Entry", "purpose": "Send to Subcontractor"}, False, "transfer"),
			({"doctype": "Stock Entry", "purpose": "Subcontracting Delivery"}, True, "transfer"),
			({"doctype": "Purchase Receipt", "is_return": 1}, True, "issue"),
			({"doctype": "Delivery Note", "is_return": 1}, False, "sales"),
			({"doctype": "Sales Invoice", "is_return": 1}, False, "sales"),
			({"doctype": "POS Invoice", "is_return": 1}, False, "sales"),
			({"doctype": "Subcontracting Receipt"}, False, "receipt"),
			({"doctype": "Subcontracting Receipt"}, True, "issue"),
			({"doctype": "Pick List", "purpose": "Delivery"}, True, "sales"),
			({"doctype": "Pick List", "purpose": "Material Transfer"}, True, "transfer"),
		]
		for fields, outgoing, expected in cases:
			with self.subTest(fields=fields, outgoing=outgoing):
				self.assertEqual(
					warehouse_policy.movement_operation(frappe._dict(fields), outgoing), expected
				)
				self.assertIn(expected, warehouse_policy.OPERATION_DOCUMENTS)

		internal = frappe._dict(doctype="Sales Invoice", is_internal_customer=1)
		self.assertEqual(
			warehouse_policy.movement_operation(internal, True, row=frappe._dict(target_warehouse="T")),
			"transfer",
		)
