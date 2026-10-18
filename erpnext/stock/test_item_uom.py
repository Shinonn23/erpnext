# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from erpnext.controllers import queries
from erpnext.stock import item_uom


def link(fieldname, options):
	return frappe._dict(fieldname=fieldname, fieldtype="Link", options=options, label=fieldname.title())


def meta(*fields):
	return SimpleNamespace(
		fields=list(fields),
		get_label=lambda fieldname: next(
			(df.label for df in fields if df.fieldname == fieldname), fieldname.title()
		),
	)


METAS = {
	"Order": meta(link("item", "Item"), link("uom", "UOM"), frappe._dict(fieldname="items", label="Items")),
	"Order Item": meta(
		link("item_code", "Item"),
		link("uom", "UOM"),
		link("stock_uom", "UOM"),
		link("weight_uom", "UOM"),
		link("pack_uom", "UOM"),
		link("free_item", "Item"),
		link("free_item_uom", "UOM"),
	),
}

ITEMS = {
	"Kit": frappe._dict(
		stock_uom="Pc",
		variant_of=None,
		uoms=[frappe._dict(uom="Pc", conversion_factor=1), frappe._dict(uom="Box", conversion_factor=10)],
	),
	"Kit Red": frappe._dict(
		stock_uom="Pc",
		variant_of="Kit",
		uoms=[frappe._dict(uom="Box", conversion_factor=12), frappe._dict(uom="Pack", conversion_factor=5)],
	),
}


class Row(frappe._dict):
	@property
	def meta(self):
		return METAS[self.doctype]


class Doc(Row):
	def get_all_children(self):
		return self.children


class ItemDoc(frappe._dict):
	def is_new(self):
		return False

	def has_value_changed(self, fieldname):
		return fieldname == "stock_uom" and self.stock_uom_changed

	def set(self, fieldname, value):
		self[fieldname] = value


class TestItemUom(unittest.TestCase):
	def setUp(self):
		self.addCleanup(patch.stopall)
		patch.object(frappe, "get_meta", side_effect=lambda doctype: METAS[doctype]).start()
		patch.object(
			frappe,
			"get_cached_value",
			side_effect=lambda doctype, name, *args: name if name in ITEMS else None,
		).start()
		patch.object(frappe, "get_cached_doc", side_effect=lambda doctype, name: ITEMS[name]).start()
		self.msgprint = patch.object(frappe, "msgprint").start()
		patch.object(
			frappe,
			"throw",
			side_effect=lambda message, exc=frappe.ValidationError, **kwargs: (_ for _ in ()).throw(
				exc(message)
			),
		).start()
		patch.object(item_uom, "_", side_effect=lambda text: text).start()
		patch.object(item_uom, "bold", side_effect=lambda text: text).start()
		self.restricted = patch.object(item_uom, "is_item_uom_restricted", return_value=True).start()

	def order(self, header_uom="Pc", **row):
		child = Row(doctype="Order Item", idx=1, parentfield="items", item_code="Kit", uom="Pc")
		child.update(row)
		return Doc(doctype="Order", item="Kit", uom=header_uom, children=[child])

	def test_uom_fields_pair_with_their_own_item(self):
		self.assertEqual(
			item_uom.get_item_uom_fields("Order Item"), [("uom", "item_code"), ("free_item_uom", "free_item")]
		)
		self.assertEqual(item_uom.get_item_uom_fields("Order"), [("uom", "item")])

	def test_item_uoms_are_stock_uom_then_own_table_then_template(self):
		self.assertEqual(item_uom.get_item_uoms("Kit"), {"Pc": 1.0, "Box": 10.0})
		self.assertEqual(item_uom.get_item_uoms("Kit Red"), {"Pc": 1.0, "Box": 12.0, "Pack": 5.0})
		self.assertEqual(item_uom.get_item_uoms("Missing"), {})
		frappe.get_cached_doc.assert_any_call("Item", "Kit")

	def test_defined_uoms_and_free_uom_fields_save(self):
		item_uom.validate_item_uoms(
			self.order(header_uom="Box", stock_uom="Kg", weight_uom="Kg", pack_uom="Kg")
		)

	def test_uom_without_conversion_is_rejected_on_rows_and_header(self):
		with self.assertRaisesRegex(
			frappe.ValidationError, r"Items Row #1: Uom Pack has no conversion factor in Item Kit"
		):
			item_uom.validate_item_uoms(self.order(uom="Pack"))
		with self.assertRaisesRegex(
			frappe.ValidationError, r"^Uom Pack has no conversion factor in Item Kit"
		):
			item_uom.validate_item_uoms(self.order(header_uom="Pack"))
		with self.assertRaisesRegex(frappe.ValidationError, "Free_Item_Uom Pack"):
			item_uom.validate_item_uoms(self.order(free_item="Kit", free_item_uom="Pack"))

	def test_unrestricted_site_and_unknown_items_are_left_alone(self):
		item_uom.validate_item_uoms(self.order(item_code="Missing", uom="Pack"))
		frappe.get_meta.reset_mock()
		self.restricted.return_value = False
		item_uom.validate_item_uoms(self.order(uom="Pack"))
		frappe.get_meta.assert_not_called()

	def item(self, stock_uom_changed=False, **values):
		item = ItemDoc(
			stock_uom="Pc",
			variant_of=None,
			uoms=[frappe._dict(uom="Pc"), frappe._dict(uom="Box")],
			barcodes=[],
			meta=SimpleNamespace(get_label=lambda fieldname: fieldname),
			stock_uom_changed=stock_uom_changed,
		)
		item.update(values)
		return item

	def test_item_default_and_barcode_uoms_need_a_conversion_row(self):
		item_uom.validate_item_default_uoms(self.item(purchase_uom="Box", sales_uom="Pc"))
		item_uom.validate_item_default_uoms(self.item(variant_of="Kit Red", sales_uom="Pack"))
		with self.assertRaisesRegex(frappe.ValidationError, "purchase_uom Pack has no conversion factor"):
			item_uom.validate_item_default_uoms(self.item(purchase_uom="Pack"))
		with self.assertRaisesRegex(frappe.ValidationError, "Barcodes row #2: UOM Pack"):
			item_uom.validate_item_default_uoms(
				self.item(barcodes=[frappe._dict(idx=1, uom="Box"), frappe._dict(idx=2, uom="Pack")])
			)
		self.msgprint.assert_not_called()
		self.restricted.return_value = False
		item_uom.validate_item_default_uoms(self.item(purchase_uom="Pack"))

	def test_new_stock_uom_clears_defaults_it_does_not_convert(self):
		# Item.add_default_uom_in_conversion_factor_table has already reset the table to the new stock UOM.
		item = self.item(
			stock_uom_changed=True,
			stock_uom="Unit",
			uoms=[frappe._dict(uom="Unit")],
			purchase_uom="Box",
			sales_uom="Unit",
			barcodes=[frappe._dict(idx=1, uom="Box")],
		)
		item_uom.validate_item_default_uoms(item)
		self.assertEqual((item.purchase_uom, item.sales_uom, item.barcodes[0].uom), (None, "Unit", None))
		self.assertIn("Box", self.msgprint.call_args.args[0])

	def test_picker_lists_item_uoms_with_factor_and_search(self):
		patch.object(queries, "is_item_uom_restricted", return_value=True).start()
		get_list = patch.object(frappe, "get_list", return_value=["Kit Red"]).start()
		patch.object(frappe, "db", SimpleNamespace(exists=lambda *args: True)).start()

		def search(txt="", start=0, page_len=20, **filters):
			return queries.get_item_uom_query("UOM", txt, "name", start, page_len, filters)

		self.assertEqual(search(item_code="Kit Red"), [["Pc", 1.0], ["Box", 12.0], ["Pack", 5.0]])
		self.assertEqual(search("bo", item_code="Kit Red"), [["Box", 12.0]])
		self.assertEqual(search(start=1, page_len=1, item_code="Kit Red"), [["Box", 12.0]])
		self.assertEqual(search(item_code=""), [])
		get_list.return_value = []
		self.assertEqual(search(item_code="Kit Red"), [])


if __name__ == "__main__":
	unittest.main()
