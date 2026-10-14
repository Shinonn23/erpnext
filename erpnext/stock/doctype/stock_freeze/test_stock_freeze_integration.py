# Copyright (c) 2026, ERPNext contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import flt, nowdate, nowtime

from erpnext.accounts.doctype.pos_invoice.test_pos_invoice import create_pos_invoice
from erpnext.accounts.doctype.pos_opening_entry.test_pos_opening_entry import create_opening_entry
from erpnext.accounts.doctype.pos_profile.test_pos_profile import make_pos_profile
from erpnext.accounts.doctype.sales_invoice.test_sales_invoice import create_sales_invoice
from erpnext.assets.doctype.asset.test_asset import create_asset
from erpnext.selling.doctype.customer.test_customer import create_internal_customer
from erpnext.stock import get_warehouse_account
from erpnext.stock.doctype.delivery_note.test_delivery_note import create_delivery_note
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.serial_and_batch_bundle.test_serial_and_batch_bundle import (
	get_batch_from_bundle,
	get_serial_nos_from_bundle,
)
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.stock.doctype.stock_reconciliation.test_stock_reconciliation import create_stock_reconciliation
from erpnext.stock.doctype.warehouse.test_warehouse import create_warehouse
from erpnext.stock.stock_control import OPERATIONS
from erpnext.tests.utils import ERPNextTestSuite


class TestStockFreezeIntegration(ERPNextTestSuite):
	def setUp(self):
		self.addCleanup(frappe.db.rollback)
		self.load_test_records("Company")
		self.company = "_Test Company"
		normal = frappe.get_doc(
			{
				"doctype": "Warehouse Type",
				"name": "_Test Controlled Stock",
				"handling_category": "Normal",
				**{"allow_" + operation: 1 for operation in OPERATIONS},
			}
		).insert()
		qa = frappe.get_doc(
			{
				"doctype": "Warehouse Type",
				"name": "_Test Controlled QA",
				"handling_category": "QA",
				"allow_internal_transfer": 1,
			}
		).insert()
		self.main = create_warehouse("_Test Control Main", {"warehouse_type": normal.name})
		self.qa = create_warehouse("_Test Control QA", {"warehouse_type": qa.name})
		for name in (self.main, self.qa):
			location = frappe.get_doc(
				{"doctype": "Storage Location", "warehouse": name, "location_code": "CONTROL"}
			).insert()
			warehouse = frappe.get_doc("Warehouse", name)
			warehouse.default_storage_location = location.name
			warehouse.save()
		# Existing native fixture warehouses also need explicit policy before activation.
		for name in frappe.get_all(
			"Warehouse", filters={"company": self.company, "is_group": 0, "disabled": 0}, pluck="name"
		):
			if name == self.qa:
				continue
			warehouse = frappe.get_doc("Warehouse", name)
			warehouse.warehouse_type = normal.name
			warehouse.stock_control_change_reason = "Prepare isolated stock control test"
			warehouse.save()
		location_type = frappe.get_doc(
			{
				"doctype": "Location Type",
				"name": "_Test Controlled Location",
				"handling_category": "Normal",
				**{"allow_" + operation: 1 for operation in OPERATIONS if operation != "reconciliation"},
			}
		).insert()
		for name in frappe.get_all(
			"Asset",
			filters={
				"company": self.company,
				"docstatus": 1,
				"status": ["not in", ["Sold", "Scrapped", "Capitalized"]],
			},
			pluck="location",
		):
			if name:
				location = frappe.get_doc("Location", name)
				location.location_type = location_type.name
				location.stock_control_change_reason = "Prepare isolated stock control test"
				location.save()
		company = frappe.get_doc("Company", self.company)
		company.enable_stock_control = 1
		company.save()
		self.item = make_item("_Test Controlled Stock Item").name
		self.receipt = make_stock_entry(
			item_code=self.item, company=self.company, target=self.main, qty=5, rate=100
		)

	def freeze(self, level="Item", target=None):
		field = {"Item": "item_code", "Batch": "batch_no", "Serial No": "serial_no"}[level]
		return frappe.get_doc(
			{
				"doctype": "Stock Freeze",
				"company": self.company,
				"freeze_level": level,
				field: target or self.item,
				"reason": "Investigate quality",
			}
		).insert()

	def quantity(self, warehouse):
		return flt(frappe.db.get_value("Bin", {"item_code": self.item, "warehouse": warehouse}, "actual_qty"))

	def rejected_submit(self, doc):
		before = tuple(
			frappe.db.count(dt, {"voucher_no": doc.name}) for dt in ("Stock Ledger Entry", "GL Entry")
		)
		frappe.db.savepoint("stock_control_rejection")
		try:
			with self.assertRaises(frappe.ValidationError):
				doc.submit()
		finally:
			frappe.db.rollback(save_point="stock_control_rejection")
		self.assertEqual(
			tuple(frappe.db.count(dt, {"voucher_no": doc.name}) for dt in ("Stock Ledger Entry", "GL Entry")),
			before,
		)
		self.assertEqual(frappe.get_doc(doc.doctype, doc.name).docstatus, 0)

	def test_freeze_release_issue_and_cancel_restore_stock(self):
		issue = make_stock_entry(item_code=self.item, source=self.main, qty=1, do_not_submit=True)
		hold = self.freeze()
		self.rejected_submit(issue)
		self.assertEqual(self.quantity(self.main), 5)
		hold.release("Inspection complete")
		issue = frappe.get_doc("Stock Entry", issue.name).submit()
		self.assertEqual(self.quantity(self.main), 4)
		if frappe.get_cached_value("Company", self.company, "enable_perpetual_inventory"):
			stock_account = get_warehouse_account(frappe.get_doc("Warehouse", self.main))
			expense_account = issue.items[0].expense_account
			gl = frappe.get_all(
				"GL Entry",
				filters={"voucher_no": issue.name, "is_cancelled": 0},
				fields=["account", "debit", "credit"],
			)
			self.assertEqual(
				sum(flt(row.debit) - flt(row.credit) for row in gl if row.account == stock_account), -100
			)
			self.assertEqual(
				sum(flt(row.debit) - flt(row.credit) for row in gl if row.account == expense_account), 100
			)
		self.assertEqual(
			sum(
				flt(row.actual_qty)
				for row in frappe.get_all(
					"Stock Ledger Entry", filters={"voucher_no": issue.name}, fields=["actual_qty"]
				)
			),
			-1,
		)
		self.freeze()
		issue.cancel()
		self.assertEqual(self.quantity(self.main), 5)

	def test_qa_transfer_retains_freeze_and_release_reason_is_required(self):
		hold = self.freeze()
		transfer = make_stock_entry(item_code=self.item, source=self.main, target=self.qa, qty=4)
		self.assertEqual((self.quantity(self.main), self.quantity(self.qa)), (1, 4))
		self.assertEqual(frappe.get_doc("Stock Freeze", hold.name).status, "Active")
		exit_doc = make_stock_entry(
			item_code=self.item, source=self.qa, target=self.main, qty=3, do_not_submit=True
		)
		exit_doc.stock_control_release_reason = "Release approved"
		exit_doc.save()
		self.rejected_submit(exit_doc)
		hold.release("Quality accepted")
		exit_doc = frappe.get_doc("Stock Entry", exit_doc.name)
		exit_doc.stock_control_release_reason = None
		exit_doc.save()
		self.rejected_submit(exit_doc)
		exit_doc = frappe.get_doc("Stock Entry", exit_doc.name)
		exit_doc.stock_control_release_reason = "Return approved stock to Main"
		exit_doc.save().submit()
		self.assertEqual((self.quantity(self.main), self.quantity(self.qa)), (4, 1))
		exit_doc.cancel()
		transfer.cancel()
		self.assertEqual((self.quantity(self.main), self.quantity(self.qa)), (5, 0))

	def test_frozen_sales_and_warehouse_wide_reconciliation_are_rejected(self):
		invoice = create_sales_invoice(
			item_code=self.item, warehouse=self.main, qty=1, rate=200, update_stock=1, do_not_submit=True
		)
		reconciliation = create_stock_reconciliation(
			item_code=self.item, warehouse=self.main, qty=2, rate=100, do_not_submit=True
		)
		self.freeze()
		self.rejected_submit(invoice)
		self.rejected_submit(reconciliation)
		self.assertEqual(self.quantity(self.main), 5)

	def test_standalone_return_cannot_bypass_qa_and_freeze(self):
		make_stock_entry(item_code=self.item, source=self.main, target=self.qa, qty=2)
		customer = create_internal_customer(represents_company=self.company)
		returned = create_delivery_note(
			company=self.company,
			customer=customer,
			item_code=self.item,
			warehouse=self.main,
			target_warehouse=self.qa,
			qty=-1,
			is_return=1,
			do_not_submit=True,
		)
		self.assertFalse(returned.return_against)
		hold = self.freeze()
		self.rejected_submit(returned)
		self.assertEqual((self.quantity(self.main), self.quantity(self.qa)), (3, 2))
		hold.release("Quality accepted")
		returned = frappe.get_doc("Delivery Note", returned.name)
		# Delivery Notes have no QA release field: use a Stock Entry for a QA exit.
		self.rejected_submit(returned)
		self.assertEqual((self.quantity(self.main), self.quantity(self.qa)), (3, 2))

	def test_quotation_api_rejects_qa_source(self):
		quotation = frappe.get_doc(
			{
				"doctype": "Quotation",
				"company": self.company,
				"quotation_to": "Customer",
				"party_name": "_Test Customer",
				"items": [{"item_code": self.item, "qty": 1, "rate": 100, "warehouse": self.qa}],
			}
		)
		with self.assertRaises(frappe.ValidationError):
			quotation.insert()
		self.assertFalse(frappe.db.exists("Quotation", quotation.name))

	def test_batch_freeze_blocks_only_selected_lot(self):
		with self.change_settings("Stock Settings", enable_serial_and_batch_no_for_item=1):
			self.item = make_item(
				"_Test Controlled Batch Item",
				properties={
					"has_batch_no": 1,
					"create_new_batch": 1,
					"batch_number_series": "CTRL-BATCH-.#####",
				},
			).name
			receipt = make_stock_entry(item_code=self.item, target=self.main, qty=3, rate=100)
			batch = get_batch_from_bundle(receipt.items[0].serial_and_batch_bundle)
			blocked = make_stock_entry(
				item_code=self.item, source=self.main, batch_no=batch, qty=1, do_not_submit=True
			)
			hold = self.freeze("Batch", batch)
			self.rejected_submit(blocked)
			self.assertEqual(self.quantity(self.main), 3)
			other = make_stock_entry(item_code=self.item, target=self.main, qty=2, rate=100)
			other_batch = get_batch_from_bundle(other.items[0].serial_and_batch_bundle)
			self.assertNotEqual(batch, other_batch)
			make_stock_entry(item_code=self.item, source=self.main, batch_no=other_batch, qty=1)
			self.assertEqual(self.quantity(self.main), 4)
			hold.release("Lot accepted")
			frappe.get_doc("Stock Entry", blocked.name).submit()
			self.assertEqual(self.quantity(self.main), 3)

	def test_serial_freeze_blocks_only_selected_unit(self):
		with self.change_settings("Stock Settings", enable_serial_and_batch_no_for_item=1):
			self.item = make_item(
				"_Test Controlled Serial Item",
				properties={"has_serial_no": 1, "serial_no_series": "CTRL-SERIAL-.#####"},
			).name
			receipt = make_stock_entry(item_code=self.item, target=self.main, qty=2, rate=100)
			serials = get_serial_nos_from_bundle(receipt.items[0].serial_and_batch_bundle)
			blocked = make_stock_entry(
				item_code=self.item, source=self.main, serial_no=[serials[0]], qty=1, do_not_submit=True
			)
			hold = self.freeze("Serial No", serials[0])
			self.rejected_submit(blocked)
			make_stock_entry(item_code=self.item, source=self.main, serial_no=[serials[1]], qty=1)
			self.assertEqual(self.quantity(self.main), 1)
			self.assertEqual(frappe.db.get_value("Serial No", serials[0], "warehouse"), self.main)
			hold.release("Unit accepted")
			frappe.get_doc("Stock Entry", blocked.name).submit()
			self.assertEqual(self.quantity(self.main), 0)

	def test_asset_freeze_qa_exit_and_receipt_cannot_bypass_release(self):
		normal_type = frappe.get_doc("Location Type", "_Test Controlled Location")
		qa_type = frappe.get_doc(
			{
				"doctype": "Location Type",
				"name": "_Test Controlled Asset QA",
				"handling_category": "QA",
				"allow_internal_transfer": 1,
			}
		).insert()
		main = frappe.get_doc(
			{
				"doctype": "Location",
				"location_name": "_Test Asset Control Main",
				"location_type": normal_type.name,
			}
		).insert()
		qa = frappe.get_doc(
			{"doctype": "Location", "location_name": "_Test Asset Control QA", "location_type": qa_type.name}
		).insert()
		item = make_item(
			"_Test Controlled Asset Item",
			properties={"is_stock_item": 0, "is_fixed_asset": 1, "asset_category": "Computers"},
		)
		asset = create_asset(item_code=item.name, location=main.name, submit=True)
		hold = frappe.get_doc(
			{
				"doctype": "Stock Freeze",
				"company": self.company,
				"freeze_level": "Asset",
				"asset": asset.name,
				"reason": "Asset investigation",
			}
		).insert()
		transfer = (
			frappe.get_doc(
				{
					"doctype": "Asset Movement",
					"company": self.company,
					"purpose": "Transfer",
					"assets": [
						{"asset": asset.name, "source_location": main.name, "target_location": qa.name}
					],
				}
			)
			.insert()
			.submit()
		)
		asset.reload()
		self.assertEqual(asset.location, qa.name)
		self.assertEqual(frappe.get_doc("Stock Freeze", hold.name).status, "Active")
		receipt = frappe.get_doc(
			{
				"doctype": "Asset Movement",
				"company": self.company,
				"purpose": "Receipt",
				"assets": [{"asset": asset.name, "target_location": main.name}],
			}
		).insert()
		self.rejected_submit(receipt)
		asset.reload()
		self.assertEqual(asset.location, qa.name)
		hold.release("Asset inspection complete")
		self.rejected_submit(frappe.get_doc("Asset Movement", receipt.name))
		receipt = frappe.get_doc("Asset Movement", receipt.name)
		receipt.stock_control_release_reason = "Return accepted Asset to Main"
		receipt.save().submit()
		asset.reload()
		self.assertEqual(asset.location, main.name)
		receipt.cancel()
		transfer.cancel()
		asset.reload()
		self.assertEqual(asset.location, main.name)

	def test_pos_tracked_bundle_consolidates_original_dispatch_after_freeze(self):
		with (
			self.change_settings("POS Settings", invoice_type="POS Invoice"),
			self.change_settings(
				"Stock Settings",
				enable_serial_and_batch_no_for_item=1,
				auto_create_serial_and_batch_bundle_for_outward=1,
			),
		):
			self.item = make_item(
				"_Test Controlled POS Serial",
				properties={"has_serial_no": 1, "serial_no_series": "CTRL-POS-.#####"},
			).name
			pos_receipt = make_stock_entry(item_code=self.item, target=self.main, qty=2, rate=100)
			bundle_item = make_item("_Test Controlled POS Bundle", properties={"is_stock_item": 0}).name
			frappe.get_doc(
				{
					"doctype": "Product Bundle",
					"new_item_code": bundle_item,
					"items": [{"item_code": self.item, "qty": 1}],
				}
			).insert()
			profile = make_pos_profile(warehouse=self.main)
			if not any(row.user == "Administrator" for row in profile.applicable_for_users):
				profile.append("applicable_for_users", {"user": "Administrator", "default": 1})
			profile.save()
			opening = create_opening_entry(profile, "Administrator", get_obj=True)
			pos = create_pos_invoice(
				item_code=bundle_item,
				warehouse=self.main,
				rate=200,
				qty=1,
				do_not_save=True,
				pos_profile=profile.name,
			)
			pos.run_method("calculate_taxes_and_totals")
			pos.set(
				"payments",
				[
					{
						"mode_of_payment": "Cash",
						"account": "Cash - _TC",
						"amount": pos.rounded_total or pos.grand_total,
					}
				],
			)
			pos.paid_amount = pos.base_paid_amount = pos.rounded_total or pos.grand_total
			pos.insert()
			pos.packed_items[0].use_serial_batch_fields = 1
			pos.packed_items[0].serial_no = get_serial_nos_from_bundle(
				pos_receipt.items[0].serial_and_batch_bundle
			)[0]
			pos.save().submit()
			pos.reload()
			original_bundle = pos.packed_items[0].serial_and_batch_bundle
			self.assertTrue(original_bundle)
			original_serials = get_serial_nos_from_bundle(original_bundle)
			self.freeze()
			merge = (
				frappe.get_doc(
					{
						"doctype": "POS Invoice Merge Log",
						"company": self.company,
						"customer": pos.customer,
						"posting_date": nowdate(),
						"posting_time": nowtime(),
						"pos_invoices": [{"pos_invoice": pos.name, "customer": pos.customer}],
					}
				)
				.insert()
				.submit()
			)
			invoice = frappe.get_doc("Sales Invoice", merge.consolidated_invoice)
			self.assertEqual(invoice.docstatus, 1)
			self.assertEqual(invoice.packed_items[0].serial_and_batch_bundle, original_bundle)
			self.assertEqual(
				get_serial_nos_from_bundle(invoice.packed_items[0].serial_and_batch_bundle), original_serials
			)
			self.assertEqual(invoice.packed_items[0].parent_detail_docname, invoice.items[0].name)
			self.assertEqual(self.quantity(self.main), 1)
			opening.cancel()
