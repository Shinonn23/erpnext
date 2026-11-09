"""Focused native mapping and receipt limits, without a site or database writes."""

import ast
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe
from frappe.utils import flt


class Doc(frappe._dict):
	def precision(self, *args):
		return 2


def method(path, name, **namespace):
	root = Path(__file__).resolve().parents[3]
	node = next(
		n
		for n in ast.walk(ast.parse((root / path).read_text()))
		if isinstance(n, ast.FunctionDef) and n.name == name
	)
	exec(compile(ast.Module([node], []), path, "exec"), namespace)
	return namespace[name]


class TestReceiptInvoicePrices(TestCase):
	def setUp(self):
		self.enterContext(patch.object(frappe, "get_system_settings", return_value="Banker's Rounding"))

	def test_partial_fixed_discount_maps_as_the_same_effective_percentage(self):
		apply = method(
			"controllers/accounts_controller.py",
			"set_discount_amount_after_mapping",
			flt=flt,
			PURCHASE_TRANSACTION_TYPES={"Purchase Order", "Purchase Receipt", "Purchase Invoice"},
			SALES_TRANSACTION_TYPES={"Sales Order", "Delivery Note", "Sales Invoice"},
		)
		for source_type, target_type in (
			("Purchase Order", "Purchase Invoice"),
			("Purchase Receipt", "Purchase Invoice"),
			("Sales Order", "Sales Invoice"),
			("Delivery Note", "Sales Invoice"),
			("Sales Order", "Delivery Note"),
			("Purchase Order", "Purchase Receipt"),
		):
			for basis in ("Net Total", "Grand Total"):
				with self.subTest(source=source_type, target=target_type, basis=basis):
					meta = SimpleNamespace(get_field=lambda name: True)
					source = Doc(
						doctype=source_type,
						meta=meta,
						discount_amount=100,
						apply_discount_on=basis,
						net_total=900,
						grand_total=900,
					)
					target = Doc(doctype=target_type, meta=meta, discount_amount=100)
					apply(target, source)
					self.assertEqual(target.additional_discount_percentage, 10)
					# Native totals recalculate the partial quantity; after_mapping must keep that amount.
					target.discount_amount = 20
					apply(target, source)
					self.assertEqual(target.discount_amount, 20)

	def test_receipt_quantity_checks_remaining_billed_qty_and_forged_links(self):
		receipt = Doc(parent="PR", item_code="ITEM", uom="Pc", conversion_factor=1, qty=10)
		db = Mock(
			get_value=Mock(return_value=receipt),
			sql=Mock(return_value=[(7,)]),
			get_single_value=Mock(return_value=0),
		)

		def throw(message):
			raise ValueError(message)

		validate = method(
			"accounts/doctype/purchase_invoice/purchase_invoice.py",
			"validate_receipt_quantity",
			frappe=SimpleNamespace(db=db, throw=throw),
			get_pending_invoice_qty=method(
				"stock/doctype/purchase_receipt/purchase_receipt.py", "get_pending_invoice_qty", flt=flt
			),
			get_returned_qty_map=lambda parent: {},
			flt=flt,
			_=lambda text: text,
		)
		row = Doc(
			purchase_receipt="PR", pr_detail="PRI", item_code="ITEM", uom="Pc", conversion_factor=1, qty=3
		)
		doc = Doc(name="PI", items=[row])
		validate(doc)
		db.get_value.assert_called_with(
			"Purchase Receipt Item",
			"PRI",
			["parent", "item_code", "uom", "conversion_factor", "qty", "received_qty", "rejected_qty"],
			as_dict=True,
			for_update=True,
		)
		self.assertEqual(db.sql.call_args.args[1], ("PRI", "PI"))
		row.qty = 4
		with self.assertRaisesRegex(ValueError, "unbilled"):
			validate(doc)
		row.qty = 2
		doc["items"].append(Doc(row))
		with self.assertRaisesRegex(ValueError, "unbilled"):
			validate(doc)
		doc["items"] = [row]
		for field, value in (
			("purchase_receipt", "OTHER"),
			("item_code", "OTHER"),
			("uom", "Box"),
			("conversion_factor", 2),
			("pr_detail", None),
		):
			old = row.get(field)
			row[field] = value
			with self.subTest(field=field), self.assertRaises(ValueError):
				validate(doc)
			row[field] = old
		doc.is_return = 1
		row.qty = 20
		validate(doc)

	def test_remaining_receipt_quantity_uses_native_rejected_and_return_rules(self):
		pending = method(
			"stock/doctype/purchase_receipt/purchase_receipt.py", "get_pending_invoice_qty", flt=flt
		)
		for bill_rejected, rejected, returned, billed, expected in (
			(1, 2, 0, 0, 12),
			(0, 2, 0, 0, 10),
			(0, 0, 4, 0, 6),
			(0, 2, 4, 0, 8),
			(0, 2, 4, 3, 5),
			(1, 2, 4, 3, 9),
		):
			with self.subTest(
				bill_rejected=bill_rejected, rejected=rejected, returned=returned, billed=billed
			):
				receipt = Doc(
					parent="PR",
					item_code="ITEM",
					uom="Pc",
					conversion_factor=1,
					qty=10,
					received_qty=10 + rejected,
					rejected_qty=rejected,
				)
				db = Mock(
					get_value=Mock(return_value=receipt),
					sql=Mock(return_value=[(billed,)]),
					get_single_value=Mock(return_value=bill_rejected),
				)

				def throw(message):
					raise ValueError(message)

				validate = method(
					"accounts/doctype/purchase_invoice/purchase_invoice.py",
					"validate_receipt_quantity",
					frappe=SimpleNamespace(db=db, throw=throw),
					flt=flt,
					_=lambda text: text,
					get_pending_invoice_qty=pending,
					get_returned_qty_map=lambda parent: {"PRI": returned},
				)
				row = Doc(
					purchase_receipt="PR",
					pr_detail="PRI",
					item_code="ITEM",
					uom="Pc",
					conversion_factor=1,
					qty=expected,
				)
				doc = Doc(name="PI", items=[row])
				validate(doc)
				row.qty += 1
				with self.assertRaisesRegex(ValueError, "unbilled"):
					validate(doc)

	def test_native_same_rate_uses_receipt_for_receipted_rows_and_order_for_other_rows(self):
		def throw(message, **kwargs):
			raise ValueError(message)

		validate = method(
			"utilities/transaction_base.py",
			"validate_rate_with_reference_doc",
			frappe=SimpleNamespace(
				get_cached_value=lambda *args: ("Stop", "Override"), get_roles=lambda: [], throw=throw
			),
			flt=flt,
			fmt_rate=str,
			_=lambda text: text,
		)
		row = Doc(
			idx=1, purchase_receipt="PR", pr_detail="PRI", purchase_order="PO", po_detail="POI", rate=90
		)
		doc = Doc(
			doctype="Purchase Invoice",
			items=[row],
			get_reference_details=lambda names, dt: (
				{"POI": 100} if dt == "Purchase Order Item" else {"PRI": 90}
			),
		)
		refs = [
			("Purchase Order", "purchase_order", "po_detail"),
			("Purchase Receipt", "purchase_receipt", "pr_detail"),
		]
		validate(doc, refs)
		row.rate = 100
		with self.assertRaises(ValueError):
			validate(doc, refs)
		row.purchase_receipt = row.pr_detail = None
		validate(doc, refs)
		row.rate = 90
		with self.assertRaises(ValueError):
			validate(doc, refs)
