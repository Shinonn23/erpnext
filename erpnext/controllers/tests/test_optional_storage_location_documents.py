# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

import frappe
from frappe.utils import add_days, flt, today

import erpnext
from erpnext.accounts.doctype.purchase_invoice.test_purchase_invoice import make_purchase_invoice
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.accounts.utils import is_immutable_ledger_enabled
from erpnext.buying.doctype.purchase_order.test_purchase_order import create_purchase_order
from erpnext.selling.doctype.quotation.test_quotation import make_quotation
from erpnext.selling.doctype.sales_order.test_sales_order import make_sales_order
from erpnext.stock.doctype.material_request.test_material_request import make_material_request
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.tests.utils import ERPNextTestSuite


class TestOptionalStorageLocationDocuments(ERPNextTestSuite):
	"""Real document lifecycles: location is required at stock posting, not planning."""

	def setUp(self):
		super().setUp()
		self.company = "_Test Company"
		self.posting_date = today()
		self.warehouse = create_warehouse(
			f"Optional Location {frappe.generate_hash(length=10)}",
			properties={"default_storage_location": None},
			company=self.company,
		)
		self.item = (
			frappe.get_doc(
				{
					"doctype": "Item",
					"item_code": f"Optional Location {frappe.generate_hash(length=10)}",
					"item_group": "All Item Groups",
					"stock_uom": "Nos",
					"is_stock_item": 1,
					"valuation_rate": 50,
				}
			)
			.insert()
			.name
		)
		self.assertFalse(frappe.get_cached_value("Warehouse", self.warehouse, "default_storage_location"))

	def assert_blank_locations(self, doc):
		for row in [doc, *doc.items, *(doc.get("packed_items") or [])]:
			for field in row.meta.fields:
				if field.fieldtype == "Link" and field.options == "Storage Location":
					self.assertFalse(row.get(field.fieldname), f"{row.doctype}.{field.fieldname}")

	def assert_no_stock_ledger(self, doc):
		self.assertFalse(
			frappe.db.exists("Stock Ledger Entry", {"voucher_type": doc.doctype, "voucher_no": doc.name})
		)

	def assert_without_posting(self, doc, docstatus):
		doc.reload()
		self.assertEqual(doc.docstatus, docstatus)
		self.assert_blank_locations(doc)
		self.assert_no_stock_ledger(doc)
		if docstatus == 0:
			self.assertFalse(
				frappe.db.exists("GL Entry", {"voucher_type": doc.doctype, "voucher_no": doc.name})
			)

	def planning_lifecycle(self, doc):
		if doc.is_new():
			doc.insert()
		self.assert_without_posting(doc, 0)
		doc.save()
		self.assert_without_posting(doc, 0)
		doc.submit()
		self.assert_without_posting(doc, 1)
		doc.cancel()
		self.assert_without_posting(doc, 2)
		self.assertFalse(frappe.db.exists("GL Entry", {"voucher_type": doc.doctype, "voucher_no": doc.name}))

	def test_quotation_without_location(self):
		self.planning_lifecycle(
			make_quotation(
				item_code=self.item,
				warehouse=self.warehouse,
				qty=2,
				rate=100,
				transaction_date=self.posting_date,
				do_not_save=True,
			)
		)

	def test_sales_order_without_location(self):
		self.planning_lifecycle(
			make_sales_order(
				item_code=self.item,
				warehouse=self.warehouse,
				qty=2,
				rate=100,
				transaction_date=self.posting_date,
				do_not_save=True,
			)
		)

	def test_purchase_order_without_location(self):
		doc = create_purchase_order(
			item_code=self.item,
			warehouse=self.warehouse,
			qty=2,
			rate=100,
			transaction_date=self.posting_date,
			do_not_save=True,
		)
		doc.schedule_date = add_days(self.posting_date, 1)
		doc.items[0].schedule_date = doc.schedule_date
		self.planning_lifecycle(doc)

	def test_supplier_quotation_without_location(self):
		doc = frappe.new_doc("Supplier Quotation")
		doc.update(
			{
				"company": self.company,
				"supplier": "_Test Supplier",
				"transaction_date": self.posting_date,
				"currency": "INR",
				"conversion_rate": 1,
			}
		)
		doc.append("items", {"item_code": self.item, "warehouse": self.warehouse, "qty": 2, "rate": 100})
		self.planning_lifecycle(doc)

	def test_material_request_without_location(self):
		doc = make_material_request(
			item_code=self.item,
			warehouse=self.warehouse,
			qty=2,
			uom="Nos",
			schedule_date=add_days(self.posting_date, 1),
			do_not_submit=True,
		)
		doc.transaction_date = self.posting_date
		self.planning_lifecycle(doc)

	def invoice(self, doctype, update_stock=0):
		builder = create_sales_invoice if doctype == "Sales Invoice" else make_purchase_invoice
		doc = builder(
			item_code=self.item,
			warehouse=self.warehouse,
			qty=2,
			rate=100,
			uom="Nos",
			posting_date=self.posting_date,
			update_stock=update_stock,
			do_not_save=True,
		)
		doc.set_posting_time = 1
		doc.posting_time = "10:00:00"
		doc.disable_rounded_total = 1
		if doctype == "Purchase Invoice":
			doc.credit_to = "Creditors - _TC"
		return doc

	def invoice_without_stock_lifecycle(self, doc, debit_account, credit_account):
		doc.insert()
		self.assert_without_posting(doc, 0)
		doc.save()
		self.assert_without_posting(doc, 0)
		doc.submit()
		self.assert_without_posting(doc, 1)
		self.assertEqual(doc.grand_total, 200)
		self.assertEqual(doc.base_grand_total, 200)
		entries = frappe.get_all(
			"GL Entry",
			filters={"voucher_type": doc.doctype, "voucher_no": doc.name, "is_cancelled": 0},
			fields=["account", "debit", "credit"],
		)
		self.assertEqual(
			sorted((entry.account, flt(entry.debit), flt(entry.credit)) for entry in entries),
			sorted([(debit_account, 200, 0), (credit_account, 0, 200)]),
		)
		doc.cancel()
		self.assert_without_posting(doc, 2)
		entries = frappe.get_all(
			"GL Entry",
			filters={"voucher_type": doc.doctype, "voucher_no": doc.name},
			fields=["account", "debit", "credit", "is_cancelled"],
		)
		for account in (debit_account, credit_account):
			self.assertEqual(sum(flt(e.debit) - flt(e.credit) for e in entries if e.account == account), 0)
		self.assertEqual({bool(entry.is_cancelled) for entry in entries}, {not is_immutable_ledger_enabled()})

	def test_sales_invoice_without_stock_update_without_location(self):
		self.invoice_without_stock_lifecycle(self.invoice("Sales Invoice"), "Debtors - _TC", "Sales - _TC")

	def test_purchase_invoice_without_stock_update_without_location(self):
		debit_account = (
			frappe.get_cached_value("Company", self.company, "stock_received_but_not_billed")
			if erpnext.is_perpetual_inventory_enabled(self.company)
			else "_Test Account Cost for Goods Sold - _TC"
		)
		self.invoice_without_stock_lifecycle(
			self.invoice("Purchase Invoice"), debit_account, "Creditors - _TC"
		)

	def stock_invoice_requires_location(self, doctype):
		location = (
			frappe.get_doc(
				{"doctype": "Storage Location", "location_code": "A-01", "warehouse": self.warehouse}
			)
			.insert()
			.name
		)
		if doctype == "Sales Invoice":
			receipt = make_stock_entry(
				item_code=self.item,
				qty=5,
				to_warehouse=self.warehouse,
				rate=50,
				posting_date=self.posting_date,
				posting_time="09:00:00",
				do_not_save=True,
			)
			receipt.items[0].to_storage_location = location
			receipt.insert()
			receipt.submit()
			receipt.reload()
			self.assertEqual(receipt.docstatus, 1)
			self.assertEqual(
				frappe.get_all(
					"Stock Ledger Entry",
					filters={"voucher_type": receipt.doctype, "voucher_no": receipt.name, "is_cancelled": 0},
					pluck="actual_qty",
				),
				[5],
			)
		doc = self.invoice(doctype, update_stock=1)
		doc.insert()
		self.assert_without_posting(doc, 0)
		doc.save()
		self.assert_without_posting(doc, 0)
		baseline = frappe.db.get_value(
			"Bin", {"item_code": self.item, "warehouse": self.warehouse}, "actual_qty"
		)
		savepoint = "missing_location_posting"
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaisesRegex(frappe.ValidationError, "Storage Location is required"):
				doc.submit()
		finally:
			# Match the transaction owner in a failed HTTP request, not controller-level rollback.
			frappe.db.rollback(save_point=savepoint)
		self.assert_without_posting(doc, 0)
		self.assertFalse(frappe.db.exists("GL Entry", {"voucher_type": doc.doctype, "voucher_no": doc.name}))
		self.assertEqual(
			frappe.db.get_value("Bin", {"item_code": self.item, "warehouse": self.warehouse}, "actual_qty"),
			baseline,
		)
		doc.items[0].storage_location = location
		doc.save()
		doc.reload()
		self.assertEqual(doc.items[0].storage_location, location)
		doc.submit()
		doc.reload()
		self.assertEqual(doc.docstatus, 1)
		entries = frappe.get_all(
			"Stock Ledger Entry",
			filters={"voucher_type": doc.doctype, "voucher_no": doc.name, "is_cancelled": 0},
			fields=["item_code", "warehouse", "storage_location", "actual_qty"],
		)
		quantity = -2 if doctype == "Sales Invoice" else 2
		self.assertEqual(
			[(e.item_code, e.warehouse, e.storage_location, e.actual_qty) for e in entries],
			[(self.item, self.warehouse, location, quantity)],
		)
		self.assertEqual(
			flt(
				frappe.db.get_value(
					"Bin", {"item_code": self.item, "warehouse": self.warehouse}, "actual_qty"
				)
			),
			flt(baseline) + quantity,
		)
		doc.cancel()
		doc.reload()
		self.assertEqual(doc.docstatus, 2)
		self.assertEqual(
			sum(
				frappe.get_all(
					"Stock Ledger Entry",
					filters={"voucher_type": doc.doctype, "voucher_no": doc.name},
					pluck="actual_qty",
				)
			),
			0,
		)
		self.assertEqual(
			flt(
				frappe.db.get_value(
					"Bin", {"item_code": self.item, "warehouse": self.warehouse}, "actual_qty"
				)
			),
			flt(baseline),
		)

	def test_stock_sales_invoice_requires_location_only_when_posting(self):
		self.stock_invoice_requires_location("Sales Invoice")

	def test_stock_purchase_invoice_requires_location_only_when_posting(self):
		self.stock_invoice_requires_location("Purchase Invoice")
