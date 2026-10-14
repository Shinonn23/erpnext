# Copyright (c) 2026, ERPNext contributors
# License: GNU General Public License v3. See license.txt

import unittest
from unittest.mock import MagicMock, patch

import frappe

from erpnext.stock import stock_control_transactions as control


class TestStockControlTransactions(unittest.TestCase):
	def setUp(self):
		for name, value in (("db", MagicMock()), ("flags", frappe._dict())):
			mock = patch.object(frappe, name, value)
			mock.start()
			self.addCleanup(mock.stop)

	def test_classification_distinguishes_handover_from_transfer(self):
		with patch.object(frappe.db, "get_value", return_value="Customer Demo"):
			self.assertEqual(
				control.get_stock_control_operation(
					frappe._dict(
						doctype="Asset Movement",
						purpose="Transfer",
						reference_doctype="Material Request",
						reference_name="REQ",
					)
				),
				"demo",
			)
			self.assertEqual(
				control.get_stock_control_operation(
					frappe._dict(doctype="Delivery Note", items=[frappe._dict(material_request="REQ")])
				),
				"sales",
			)
			self.assertEqual(
				control.get_stock_control_operation(
					frappe._dict(
						doctype="Stock Entry",
						purpose="Material Transfer",
						items=[frappe._dict(material_request="REQ")],
					)
				),
				"internal_transfer",
			)
		self.assertEqual(
			control.get_stock_control_operation(
				frappe._dict(doctype="Stock Entry", purpose="Material Transfer")
			),
			"internal_transfer",
		)
		self.assertEqual(
			control.get_stock_control_operation(frappe._dict(doctype="Asset Movement", purpose="Receipt")),
			None,
		)
		self.assertEqual(control.get_stock_control_operation(frappe._dict(doctype="Custody Loan")), "loan")

	def test_mixed_pos_checks_regular_stock_and_packed_components(self):
		doc = frappe._dict(
			company="Company",
			is_return=0,
			items=[frappe._dict(item_code="Frozen", warehouse="Main")],
			packed_items=[frappe._dict(item_code="Component", warehouse="Main")],
		)
		with (
			patch.object(control, "is_stock_control_enabled", return_value=True),
			patch.object(control, "lock_stock_control_policies"),
			patch.object(control, "lock_stock_control_items"),
			patch.object(frappe, "get_cached_value", return_value=1),
			patch.object(control, "validate_stock_control_source") as validate,
		):
			control.validate_pos_stock_control(doc)
			self.assertEqual([call.args[4] for call in validate.call_args_list], ["Frozen", "Component"])

	def test_reconciliation_reduction_with_zero_ledger_quantity_is_checked(self):
		doc = frappe._dict(
			company="Company", items=[frappe._dict(item_code="Item", warehouse="Main", qty=2, current_qty=5)]
		)
		with (
			patch.object(
				control, "get_stock_control_policy", return_value=frappe._dict(allow_reconciliation=1)
			),
			patch.object(control, "validate_stock_control_source") as validate,
		):
			control._validate_reconciliation(doc)
			validate.assert_called_once()

	def test_pos_return_checks_outward_legs_but_accepts_inbound_stock(self):
		doc = frappe._dict(
			company="Company",
			is_return=1,
			items=[
				frappe._dict(item_code="Inbound", warehouse="Main", qty=-1),
				frappe._dict(item_code="Moved", warehouse="Main", target_warehouse="QA", qty=-1),
				frappe._dict(item_code="Outbound", warehouse="Main", qty=1),
			],
		)
		with (
			patch.object(control, "is_stock_control_enabled", return_value=True),
			patch.object(control, "lock_stock_control_policies"),
			patch.object(control, "lock_stock_control_items"),
			patch.object(frappe, "get_cached_value", return_value=1),
			patch.object(control, "validate_stock_control_source") as validate,
		):
			control.validate_pos_stock_control(doc)
			self.assertEqual(
				[(call.args[2], call.args[3], call.args[4]) for call in validate.call_args_list],
				[("QA", "internal_transfer", "Moved"), ("Main", "sales", "Outbound")],
			)
			self.assertEqual(validate.call_args_list[0].kwargs["target"], "Main")

	def test_reconciliation_unchanged_identity_does_not_remove_held_serial(self):
		doc = frappe._dict(
			company="Company",
			items=[
				frappe._dict(
					item_code="Item",
					warehouse="Main",
					qty=1,
					current_qty=1,
					current_serial_and_batch_bundle="old",
					serial_and_batch_bundle="new",
				)
			],
		)
		with (
			patch.object(
				control, "_bundle_quantities", return_value=control.Counter({("Serial", "Batch"): 1})
			),
			patch.object(
				control, "get_stock_control_policy", return_value=frappe._dict(allow_reconciliation=1)
			),
			patch.object(control, "validate_stock_control_source") as validate,
		):
			control._validate_reconciliation(doc)
			validate.assert_not_called()

	def test_reconciliation_replacement_checks_removed_serial(self):
		doc = frappe._dict(
			company="Company",
			items=[
				frappe._dict(
					item_code="Item",
					warehouse="Main",
					qty=1,
					current_qty=1,
					current_serial_and_batch_bundle="old",
					serial_and_batch_bundle="new",
				)
			],
		)
		with (
			patch.object(
				control,
				"_bundle_quantities",
				side_effect=[
					control.Counter({("Held", "Batch"): 1}),
					control.Counter({("Other", "Batch"): 1}),
				],
			),
			patch.object(
				control, "get_stock_control_policy", return_value=frappe._dict(allow_reconciliation=1)
			),
			patch.object(control, "validate_stock_control_source") as validate,
		):
			control._validate_reconciliation(doc)
			self.assertEqual(validate.call_args.args[6], {"Held"})

	def test_feature_off_and_cancellation_do_not_read_voucher(self):
		with (
			patch.object(control, "is_stock_control_enabled", return_value=False),
			patch.object(frappe, "get_doc") as get_doc,
		):
			control.validate_stock_ledger_entries([frappe._dict(company="Company")])
			control.validate_stock_ledger_entries([frappe._dict(company="Company", is_cancelled=1)])
			get_doc.assert_not_called()

	def test_consolidation_context_is_restored_after_failure(self):
		previous = frappe.flags.get("stock_control_pos_consolidation")
		with self.assertRaises(ValueError), control.stock_control_pos_consolidation(["POS"]):
			self.assertEqual(frappe.flags.stock_control_pos_consolidation, frozenset({"POS"}))
			raise ValueError("Failed submit")
		self.assertEqual(frappe.flags.get("stock_control_pos_consolidation"), previous)
