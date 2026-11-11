# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# See license.txt

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.controllers import stock_controller as preview
from erpnext.controllers.stock_controller import get_sl_entries_for_preview


class TestLedgerPreview(TestCase):
	def test_accounting_preview_prepares_legacy_bundle_before_stock_ledger(self):
		steps = []
		doc = frappe._dict(
			doctype="Purchase Receipt",
			name="PR-1",
			make_bundle_using_old_serial_batch_fields=lambda: steps.append("bundle"),
			update_stock_ledger=lambda: steps.append("stock"),
			make_gl_entries=lambda: steps.append("accounting"),
		)
		with (
			patch("erpnext.accounts.doctype.accounting_dimension.accounting_dimension.get_accounting_dimensions", return_value=[]),
			patch("erpnext.accounts.report.general_ledger.general_ledger.get_columns", return_value=[]),
			patch("erpnext.get_company_currency", return_value="THB"),
			patch.object(preview, "get_gl_entries_for_preview", return_value=[]),
		):
			preview.get_accounting_ledger_preview(doc, frappe._dict(company="CD"))
		self.assertEqual(steps, ["bundle", "stock", "accounting"])

	def test_in_out_rate_is_only_set_for_outgoing_entries(self):
		stock_ledger_entries = [
			frappe._dict(actual_qty=5, stock_value_difference=10),
			frappe._dict(actual_qty=-5, stock_value_difference=-15),
		]

		with patch("frappe.get_all", return_value=stock_ledger_entries):
			entries = get_sl_entries_for_preview("Delivery Note", "DN-0001", [])

		self.assertIsNone(entries[0].get("in_out_rate"))
		self.assertEqual(entries[1].in_out_rate, 3)

	def test_accounting_dimensions_follow_visible_report_column_order(self):
		columns = [
			{"fieldname": field, "label": field, "fieldtype": "Data", "hidden": field == "party"}
			for field in (
				"account",
				"department",
				"party",
				"territory",
				"business_unit",
				"debit",
				"arbitrary",
			)
		]
		doc = frappe._dict(doctype="Journal Entry", name="JE-1", make_gl_entries=Mock())
		with (
			patch(
				"erpnext.accounts.doctype.accounting_dimension.accounting_dimension.get_accounting_dimensions",
				return_value=["business_unit"],
			),
			patch("erpnext.accounts.report.general_ledger.general_ledger.get_columns", return_value=columns),
			patch("erpnext.get_company_currency", return_value="THB"),
			patch.object(
				preview,
				"get_gl_entries_for_preview",
				return_value=[
					frappe._dict(
						account="Bank", department="Sales", territory="North", business_unit="POCT", debit=0
					)
				],
			) as entries,
		):
			result, rows = preview.get_accounting_ledger_preview(doc, frappe._dict(company="CD"))
		fields = [column["fieldname"] for column in result]
		self.assertEqual(fields, ["account", "department", "territory", "business_unit", "debit"])
		self.assertEqual(rows, [["Bank", "Sales", "North", "POCT", 0]])
		self.assertEqual(entries.call_args.args[2], fields)
		doc.make_gl_entries.assert_called_once_with()

	def test_stock_dimensions_and_legacy_serials_follow_report_order(self):
		from erpnext.stock.report.stock_ledger import stock_ledger

		dimensions = [
			frappe._dict(fieldname="storage_location", doctype="Storage Location"),
			frappe._dict(fieldname="stock_owner", doctype="Customer"),
		]
		entry = frappe._dict(
			item_code="ITEM",
			stock_uom="Unit",
			actual_qty=-2,
			qty_after_transaction=8,
			warehouse="WH",
			storage_location="A001",
			stock_owner="CD",
			batch_no="B1",
			serial_no="S1\nS2",
			stock_value=80,
			stock_value_difference=-20,
		)
		doc = frappe._dict(
			doctype="Delivery Note",
			name="DN-1",
			update_stock_ledger=Mock(),
			make_bundle_using_old_serial_batch_fields=Mock(),
		)
		filters = frappe._dict(
			company="CD",
			valuation_field_type="Currency",
			show_location_wise_stock=1,
			segregate_serial_batch_bundle=1,
		)
		with (
			patch(
				"erpnext.stock.doctype.inventory_dimension.inventory_dimension.get_inventory_dimensions",
				return_value=dimensions,
			),
			patch.object(stock_ledger, "get_inventory_dimensions", return_value=dimensions),
			patch("erpnext.get_company_currency", return_value="THB"),
			patch("frappe.get_all", return_value=[entry]) as query,
			patch.object(stock_ledger, "_", side_effect=lambda text: text),
			patch.object(preview, "_", side_effect=lambda text: text),
		):
			columns, rows = preview.get_stock_ledger_preview(doc, filters)
		values = dict(zip([column["fieldname"] for column in columns], rows[0], strict=True))
		self.assertEqual(values["storage_location"], "A001")
		self.assertEqual(values["stock_owner"], "CD")
		self.assertEqual(values["batch_no"], "B1")
		self.assertEqual(values["serial_no"], "S1\nS2")
		self.assertEqual((values["in_qty"], values["out_qty"], values["in_out_rate"]), (0, 2, 10))
		self.assertEqual(values["qty_after_transaction"], 8)
		self.assertEqual(
			next(c["name"] for c in columns if c["fieldname"] == "qty_after_transaction"),
			"Balance Qty (Warehouse)",
		)
		self.assertIn("stock_owner", query.call_args.kwargs["fields"])
		doc.update_stock_ledger.assert_called_once_with()

	def test_bundle_rows_keep_each_batch_serial_quantity_and_warehouse_balance(self):
		entries = [
			frappe._dict(
				item_code="ITEM",
				warehouse="WH",
				storage_location="A001",
				serial_and_batch_bundle="BUNDLE",
				actual_qty=-2,
				qty_after_transaction=8,
				stock_value=80,
				stock_value_difference=-20,
			)
		]
		details = [
			frappe._dict(
				parent="BUNDLE",
				qty=-1,
				incoming_rate=10,
				stock_value_difference=-10,
				batch_no="B1",
				serial_no="S1",
			),
			frappe._dict(
				parent="BUNDLE",
				qty=-1,
				incoming_rate=10,
				stock_value_difference=-10,
				batch_no="B2",
				serial_no="S2",
			),
		]
		with patch("frappe.get_all", side_effect=[entries, details]) as query:
			rows = get_sl_entries_for_preview("Delivery Note", "DN-1", ["serial_and_batch_bundle"])
		self.assertEqual(
			[(row.batch_no, row.serial_no, row.out_qty) for row in rows], [("B1", "S1", 1), ("B2", "S2", 1)]
		)
		self.assertEqual([row.qty_after_transaction for row in rows], [9, 8])
		self.assertEqual([row.stock_value for row in rows], [90, 80])
		self.assertEqual([row.in_out_rate for row in rows], [10, 10])
		self.assertEqual([row.storage_location for row in rows], ["A001", "A001"])
		self.assertEqual(query.call_args_list[1].kwargs["filters"], {"parent": ("in", ["BUNDLE"])})

	def test_preview_endpoints_keep_permission_hooks_and_rollback(self):
		for kind in ("accounting", "stock"):
			with self.subTest(kind=kind):
				doc = Mock()
				with (
					patch("frappe.get_lazy_doc", return_value=doc),
					patch.object(preview, f"get_{kind}_ledger_preview", return_value=([], [])) as build,
					patch.object(frappe, "db", Mock()) as db,
				):
					getattr(preview, f"show_{kind}_ledger_preview")("CD", "Delivery Note", "DN-1")
					doc.check_permission.assert_called_once_with("read")
					doc.run_method.assert_called_once_with(
						"before_gl_preview" if kind == "accounting" else "before_sl_preview"
					)
					db.rollback.assert_called_once_with()
					if kind == "stock":
						self.assertEqual(build.call_args.args[1].segregate_serial_batch_bundle, 1)
					doc.check_permission.side_effect = frappe.PermissionError
					build.reset_mock()
					with self.assertRaises(frappe.PermissionError):
						getattr(preview, f"show_{kind}_ledger_preview")("CD", "Delivery Note", "DN-1")
					build.assert_not_called()

	def test_incoming_bundle_rows_and_zero_balance_remain_numeric(self):
		for sign, balance in ((1, 2), (-1, 0)):
			with self.subTest(sign=sign):
				entries = [
					frappe._dict(
						serial_and_batch_bundle="BUNDLE",
						actual_qty=sign * 2,
						qty_after_transaction=balance,
						stock_value=balance * 10,
						stock_value_difference=sign * 20,
					)
				]
				details = [
					frappe._dict(
						parent="BUNDLE",
						qty=sign,
						incoming_rate=10,
						stock_value_difference=sign * 10,
						batch_no="B1",
						serial_no=serial,
					)
					for serial in ("S1", "S2")
				]
				with patch("frappe.get_all", side_effect=[entries, details]):
					rows = get_sl_entries_for_preview("Stock Entry", "SE-1", [])
				self.assertEqual(sum(row.in_qty for row in rows), 2 if sign > 0 else 0)
				self.assertEqual(sum(row.out_qty for row in rows), 2 if sign < 0 else 0)
				self.assertEqual(rows[-1].qty_after_transaction, balance)
				self.assertEqual(preview.get_data(["qty_after_transaction"], rows)[-1], [balance])
				self.assertEqual(rows[-1].incoming_rate, 10 if sign > 0 else 0)
