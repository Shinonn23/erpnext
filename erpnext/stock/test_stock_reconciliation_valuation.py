"""Pure valuation checks: importing this module does not install ERPNext test fixtures."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from erpnext.stock.doctype.stock_reconciliation.stock_reconciliation import StockReconciliation
from erpnext.stock.stock_ledger import get_stock_reco_qty_shift


class TestDimensionedCountValuation(unittest.TestCase):
	def setUp(self):
		settings = patch.object(frappe, "get_system_settings", return_value="Banker's Rounding")
		settings.start()
		self.addCleanup(settings.stop)

	def preview(self, method, quantities, queue):
		controller = SimpleNamespace(company="Company", posting_date="2026-10-16", posting_time="00:00:00")
		previous = frappe._dict(
			qty_after_transaction=sum(q for q, _ in queue),
			stock_value=sum(q * r for q, r in queue),
			valuation_rate=sum(q * r for q, r in queue) / sum(q for q, _ in queue),
			stock_queue=frappe.as_json(queue),
		)
		balances = {}
		with (
			patch("erpnext.stock.stock_ledger.get_previous_sle", return_value=previous),
			patch(
				"erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_valuation_method",
				return_value=method,
			),
		):
			results = [
				StockReconciliation.get_dimensioned_count_difference(
					controller,
					frappe._dict(item_code="Item", warehouse="Warehouse", qty=10 + delta),
					{"qty": 10, "rate": previous.valuation_rate},
					balances,
				)
				for delta in quantities
			]
		return results, balances[("Item", "Warehouse")]

	def test_fifo_uses_consumed_layers_and_advances_for_each_row(self):
		values, state = self.preview("FIFO", [-3, -3], [[5, 100], [5, 200]])
		self.assertEqual(values, [-300, -400])
		self.assertEqual(state.queue.state, [[4, 200]])
		self.assertEqual(state.value, 800)

	def test_lifo_keeps_native_layer_order(self):
		values, state = self.preview("LIFO", [-3, -3], [[5, 100], [5, 200]])
		self.assertEqual(values, [-600, -500])
		self.assertEqual(state.value, 400)

	def test_moving_average_uses_warehouse_rate(self):
		values, state = self.preview("Moving Average", [-1, 2], [[5, 100], [5, 200]])
		self.assertEqual(values, [-150, 300])
		self.assertEqual(state.value, 1650)

	def test_fifo_receipt_uses_current_warehouse_rate(self):
		values, state = self.preview("FIFO", [2, -6], [[5, 100], [5, 200]])
		self.assertEqual(values, [300, -700])
		self.assertEqual(state.queue.state, [[4, 200], [2, 150]])

	def test_fifo_issue_then_receipt_keeps_actual_row_incoming_rate(self):
		values, state = self.preview("FIFO", [-1, 1], [[5, 100], [5, 200]])
		self.assertEqual(values, [-100, 150])
		self.assertEqual(state.value, 1550)

	def test_zero_delta_never_revalues(self):
		values, state = self.preview("FIFO", [0], [[5, 100], [5, 200]])
		self.assertEqual(values, [0])
		self.assertEqual(state.value, 1500)

	def test_dimensional_qty_shift_is_movement_on_submit_and_cancel(self):
		with patch(
			"erpnext.stock.stock_ledger.get_inventory_dimensions",
			return_value=[frappe._dict(fieldname="storage_location")],
		):
			for cancel, delta in ((0, 1), (1, -1)):
				self.assertEqual(
					get_stock_reco_qty_shift(
						frappe._dict(
							storage_location="Shelf",
							actual_qty=delta,
							is_cancelled=cancel,
							qty_after_transaction=50,
							previous_qty_after_transaction=10,
						)
					),
					delta,
				)

	def test_posting_guard_compares_each_row_before_gl(self):
		row = frappe._dict(name="row", idx=1, precision=lambda field: 2)
		controller = SimpleNamespace(
			items=[row],
			purpose="Stock Reconciliation",
			doctype="Stock Reconciliation",
			name="RC",
			get_inventory_dimensions_dict=lambda row: {"storage_location": "Shelf"},
		)
		with (
			patch(
				"erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_row_stock_value_difference",
				return_value=-100,
			),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
		):
			StockReconciliation.validate_posted_values(controller, {"row": -100})
			with self.assertRaises(frappe.ValidationError):
				StockReconciliation.validate_posted_values(controller, {"row": -150})

	def test_dimension_balance_keeps_latest_warehouse_rate(self):
		from unittest.mock import Mock

		from erpnext.stock.utils import get_stock_balance

		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "db", Mock(sql=Mock(return_value=[[3]]))),
			patch(
				"erpnext.stock.utils.get_inventory_dimensions",
				return_value=[{"fieldname": "storage_location"}],
			),
			patch(
				"erpnext.stock.stock_ledger.get_previous_sle",
				side_effect=[
					frappe._dict(qty_after_transaction=3, valuation_rate=25),
					frappe._dict(qty_after_transaction=10, valuation_rate=50),
				],
			),
		):
			self.assertEqual(
				get_stock_balance(
					"Item",
					"Warehouse",
					"2026-10-16",
					"00:00:00",
					with_valuation_rate=True,
					inventory_dimensions_dict={"storage_location": "Shelf"},
				),
				(3, 50),
			)

	def test_only_source_verified_zero_quantity_revaluation_can_omit_location(self):
		from erpnext.stock.doctype.storage_location.storage_location import is_warehouse_revaluation_entry

		row = frappe._dict(name="row", item_code="Item", warehouse="Warehouse", qty=10, valuation_rate=1100)
		source = SimpleNamespace(
			docstatus=1,
			purpose="Stock Reconciliation",
			company="Company",
			items=[row],
			posting_date="2026-10-16",
			posting_time="00:00:00",
			get_inventory_dimensions_dict=lambda row: {},
		)
		entry = frappe._dict(
			voucher_type="Stock Reconciliation",
			voucher_no="RC",
			voucher_detail_no="row",
			item_code="Item",
			warehouse="Warehouse",
			company="Company",
			actual_qty=0,
			qty_after_transaction=10,
			valuation_rate=1100,
			posting_date="2026-10-16",
			posting_time="00:00:00",
		)
		with (
			patch.object(frappe, "get_doc", return_value=source),
			patch.object(frappe, "get_cached_value", return_value=0),
			patch(
				"erpnext.stock.doctype.inventory_dimension.inventory_dimension.get_inventory_dimensions",
				return_value=[frappe._dict(fieldname="storage_location")],
			),
			patch(
				"erpnext.stock.stock_ledger.get_previous_sle_of_current_voucher",
				return_value=frappe._dict(qty_after_transaction=10),
			),
		):
			self.assertTrue(is_warehouse_revaluation_entry(entry))
			for field, value in (
				("voucher_type", "Stock Entry"),
				("voucher_detail_no", "other-row"),
				("qty_after_transaction", 11),
				("actual_qty", 1),
				("valuation_rate", 1200),
				("company", "Other"),
				("posting_date", "2026-10-15"),
				("storage_location", "Shelf"),
			):
				with self.subTest(field=field):
					invalid = frappe._dict(entry)
					invalid[field] = value
					self.assertFalse(is_warehouse_revaluation_entry(invalid))
			source.purpose = "Opening Stock"
			self.assertFalse(is_warehouse_revaluation_entry(entry))

	def test_empty_dimension_still_uses_existing_warehouse_rate(self):
		from unittest.mock import Mock

		from erpnext.stock.utils import get_stock_balance

		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "db", Mock(sql=Mock(return_value=[[0]]))),
			patch(
				"erpnext.stock.utils.get_inventory_dimensions",
				return_value=[{"fieldname": "storage_location"}],
			),
			patch(
				"erpnext.stock.stock_ledger.get_previous_sle",
				side_effect=[
					frappe._dict(),
					frappe._dict(qty_after_transaction=10, valuation_rate=150),
				],
			),
		):
			self.assertEqual(
				get_stock_balance(
					"Item",
					"Warehouse",
					"2026-10-16",
					"00:00:00",
					with_valuation_rate=True,
					inventory_dimensions_dict={"storage_location": "New Shelf"},
				),
				(0, 150),
			)

	def test_warehouse_revaluation_cannot_share_voucher_with_dimension_count(self):
		for locations in [("Shelf", None), (None, "Shelf")]:
			controller = SimpleNamespace(
				items=[
					frappe._dict(item_code="Item", warehouse="Warehouse", storage_location=loc)
					for loc in locations
				],
				get_inventory_dimensions_dict=lambda row: (
					{"storage_location": row.storage_location} if row.storage_location else {}
				),
			)
			with patch.object(frappe, "throw", side_effect=frappe.ValidationError):
				with self.assertRaises(frappe.ValidationError):
					StockReconciliation.validate_inventory_dimension(controller)

	def test_cancel_repeated_dimensional_opening_reverses_full_addition(self):
		from unittest.mock import Mock

		row = frappe._dict(
			name="row",
			item_code="Item",
			warehouse="Warehouse",
			storage_location="Shelf",
			qty=4,
			current_qty=6,
			valuation_rate=100,
			amount_difference=400,
			precision=lambda field: 2,
		)
		controller = SimpleNamespace(
			posting_date="2026-10-16",
			posting_time="00:00:00",
			doctype="Stock Reconciliation",
			name="RC",
			company="Company",
			docstatus=2,
			purpose="Opening Stock",
			is_adjustment_row=lambda row: False,
			update_inventory_dimensions=lambda row, data: None,
		)
		with (
			patch.object(frappe, "db", Mock(get_value=Mock(return_value="Pc"))),
			patch(
				"erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_inventory_dimensions",
				return_value=[frappe._dict(fieldname="storage_location")],
			),
		):
			args = StockReconciliation.get_sle_for_items(controller, row)
			self.assertEqual(-args.actual_qty, -4)  # make_sl_entries negates cancellation movements.

	def test_repost_preserves_additive_opening_quantity_difference(self):
		from unittest.mock import Mock

		for purpose, posted_value, quantity_difference in (
			("Opening Stock", 400, 4),
			("Stock Reconciliation", -200, -2),
		):
			with self.subTest(purpose=purpose):
				row = frappe._dict(
					name="row", qty=4, valuation_rate=100, precision=lambda field: 2, db_set=Mock()
				)
				controller = SimpleNamespace(
					items=[row],
					doctype="Stock Reconciliation",
					name="RC",
					purpose=purpose,
					is_adjustment_row=lambda row: False,
					get_current_qty_from_ledger=lambda row: 6,
					get_inventory_dimensions_dict=lambda row: {"storage_location": "Shelf"},
					get_previous_ledger_entry=lambda row: {"valuation_rate": 100},
					db_set=Mock(),
					precision=lambda field: 2,
				)
				with patch(
					"erpnext.stock.doctype.stock_reconciliation.stock_reconciliation.get_row_stock_value_difference",
					return_value=posted_value,
				):
					StockReconciliation.recalculate_difference_amount_from_ledger(controller)
				values = row.db_set.call_args.args[0]
				self.assertEqual(values["quantity_difference"], quantity_difference)
				self.assertEqual(values["current_qty"], 6)
				self.assertEqual(values["current_amount"], 600)
				self.assertEqual(values["amount_difference"], posted_value)
