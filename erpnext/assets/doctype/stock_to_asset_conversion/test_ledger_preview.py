"""Offline tests: ledger preview permission gate and the side-effect free preview flag."""

import unittest
from unittest.mock import Mock, patch

import frappe


class TestLedgerPreview(unittest.TestCase):
	def module(self):
		from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as conversion

		return conversion

	def check(self, hook, roles):
		conversion = self.module()
		doc = frappe._dict(flags=frappe._dict())
		with (
			patch.object(conversion.frappe, "get_roles", return_value=roles),
			patch.object(conversion, "_", side_effect=lambda x: x),
			patch.object(
				conversion.frappe, "throw", side_effect=lambda msg, exc=None: (_ for _ in ()).throw(exc)
			),
			patch.object(conversion.frappe, "session", frappe._dict(user="someone@example.com")),
		):
			getattr(conversion.StocktoAssetConversion, hook)(doc)
		return doc

	def test_accounting_roles_may_preview_both(self):
		for role in (
			"Accounts User",
			"Accounts Manager",
			"Finance Controller",
			"Managing Director",
			"Chief Executive Officer",
			"System Manager",
		):
			for hook in ("before_gl_preview", "before_sl_preview"):
				with self.subTest(role=role, hook=hook):
					self.assertTrue(self.check(hook, [role]).flags.ledger_preview)

	def test_stock_manager_sees_stock_ledger_only(self):
		self.assertTrue(self.check("before_sl_preview", ["Stock Manager"]).flags.ledger_preview)
		with self.assertRaises(frappe.PermissionError):
			self.check("before_gl_preview", ["Stock Manager"])

	def test_custodian_and_plain_readers_get_nothing(self):
		for role in ("Asset Custodian", "Stock User", "Employee"):
			for hook in ("before_gl_preview", "before_sl_preview"):
				with self.subTest(role=role, hook=hook), self.assertRaises(frappe.PermissionError):
					self.check(hook, [role])

	def test_preview_does_not_write_assets_or_rows(self):
		conversion = self.module()
		output = Mock(
			source_item_row="R1",
			asset_value=100,
			accounting_treatment="Capitalized",
			asset_item_code="A",
			asset="ASSET-1",
		)
		output.flags = frappe._dict()
		doc = Mock()
		doc.stock_items = [frappe._dict(name="R1", qty=1, amount=100, warehouse="W")]
		doc.assets = [output]
		doc.flags = frappe._dict(ledger_preview=True)
		doc.use_item_inventory_account = 0
		doc.company = "CD"
		doc.name = "CONV-1"
		doc.get_stock_ledger_details.return_value = {"R1": [frappe._dict(stock_value_difference=-120)]}
		doc.get_debit_field_precision.return_value = 2
		doc.precision.return_value = 2
		doc.get_inventory_account_dict.return_value = {"account": "Stock - CD"}
		doc.get_gl_dict.side_effect = lambda args, *a, **k: frappe._dict(args)
		with (
			patch.object(conversion.erpnext, "is_perpetual_inventory_enabled", return_value=True),
			patch.object(conversion.erpnext, "get_default_cost_center", return_value="CC"),
			patch.object(conversion, "flt", side_effect=lambda v, p=None: round(float(v or 0), 2)),
			patch.object(conversion.frappe, "db", Mock()),
			patch.object(conversion.frappe, "get_doc") as get_doc,
			patch.object(conversion, "get_asset_category_account", return_value="Fixed Asset - CD"),
		):
			entries = conversion.StocktoAssetConversion.get_gl_entries(doc)
		get_doc.assert_not_called()
		output.db_set.assert_not_called()
		self.assertEqual(output.asset_value, 120)
		self.assertEqual(sum(e.get("debit", 0) for e in entries), sum(e.get("credit", 0) for e in entries))


if __name__ == "__main__":
	unittest.main()
