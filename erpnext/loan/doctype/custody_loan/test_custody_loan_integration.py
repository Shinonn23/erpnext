# Copyright (c) 2026, ERPNext contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe.utils import add_days, date_diff, flt, get_last_day, today

from erpnext.assets.doctype.material_request import test_material_request as request_fixtures
from erpnext.setup.doctype.employee.test_employee import make_employee
from erpnext.stock.doctype.item.test_item import make_item
from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry
from erpnext.tests.utils import ERPNextTestSuite


class TestCustodyLoanIntegration(ERPNextTestSuite):
	"""Additional persisted-effect coverage; request workflow tests live with MR."""

	company = "_Test Company"
	source_warehouse = "_Test Warehouse - _TC"
	# Reuse native fixture builders without inheriting the request's test cases.
	ensure_location = request_fixtures.TestCustomerMaterialRequestIntegration.ensure_location
	ensure_address = request_fixtures.TestCustomerMaterialRequestIntegration.ensure_address
	make_request = request_fixtures.TestCustomerMaterialRequestIntegration.make_request
	make_custody_loan = request_fixtures.TestCustomerMaterialRequestIntegration.make_custody_loan
	complete_stock_loan_issue = (
		request_fixtures.TestCustomerMaterialRequestIntegration.complete_stock_loan_issue
	)
	complete_stock_loan_return = (
		request_fixtures.TestCustomerMaterialRequestIntegration.complete_stock_loan_return
	)

	def setUp(self):
		super().setUp()
		request_fixtures.ensure_company_tax_settings(self.company)
		frappe.db.set_value(
			"Company",
			self.company,
			{
				"capital_work_in_progress_account": "CWIP Account - _TC",
				"stock_adjustment_account": "Stock Adjustment - _TC",
			},
		)
		request_fixtures.create_internal_customer(
			customer_name=f"Loan Integration Internal Customer {frappe.generate_hash(length=10)}",
			represents_company=self.company,
		)
		self.loan_warehouse = request_fixtures.create_warehouse("_Test Loan Integration Warehouse")
		frappe.db.set_value("Company", self.company, "customer_loan_warehouse", self.loan_warehouse)
		# Both warehouses deliberately use the same inventory account: transfers
		# change physical custody without changing this company's GL balance.
		for warehouse in (self.source_warehouse, self.loan_warehouse):
			location = frappe.get_doc(
				{
					"doctype": "Storage Location",
					"warehouse": warehouse,
					"location_code": f"LOAN-{frappe.generate_hash(length=10)}",
				}
			).insert()
			warehouse_doc = frappe.get_doc("Warehouse", warehouse)
			warehouse_doc.account = "Stock In Hand - _TC"
			warehouse_doc.default_storage_location = location.name
			warehouse_doc.save()
		self.source_location = self.ensure_location("Test Location")
		self.customer_location = self.ensure_location("Customer Location")
		self.customer_address = self.ensure_address("_Test Customer", "Customer")
		self.customer_contact = (
			frappe.get_doc(
				{
					"doctype": "Contact",
					"first_name": f"Loan Contact {frappe.generate_hash(length=10)}",
					"links": [{"link_doctype": "Customer", "link_name": "_Test Customer"}],
				}
			)
			.insert()
			.name
		)
		frappe.db.set_value("Warehouse", self.source_warehouse, "asset_location", self.source_location)

	def make_asset(self, item_code):
		asset = request_fixtures.TestCustomerMaterialRequestIntegration.make_asset(self, item_code)
		# Native fixture creation updates demo fields directly after submission.
		# Clear its cached draft snapshot before downstream accounting reads it.
		frappe.clear_document_cache("Asset", asset.name)
		return asset

	def stock_loan(self, qty=3, serialized=False):
		properties = {"is_stock_item": 1, "stock_uom": "Nos", "has_serial_no": int(serialized)}
		item = make_item(properties=properties)
		serials = [f"LOAN-{frappe.generate_hash(length=12)}" for _ in range(qty)] if serialized else []
		make_stock_entry(
			item_code=item.name,
			qty=qty,
			to_warehouse=self.source_warehouse,
			rate=100,
			posting_date=today(),
			serial_no="\n".join(serials) or None,
			use_serial_batch_fields=int(serialized),
		)
		request = self.make_request("Customer Borrow / Loan", [{"item_code": item.name, "qty": qty}])
		loan = self.make_custody_loan(request, "Stock")
		if serialized:
			loan.items[0].serial_no = "\n".join(serials)
			loan.save()
		loan.submit()
		delivery_note, _ = self.complete_stock_loan_issue(loan)
		return loan, delivery_note, serials

	def asset_loan(self, recipient_type="Customer", recipient=None):
		asset = self.make_asset(f"Loan Integration Asset {frappe.generate_hash(length=10)}")
		request = self.make_request(
			"Customer Borrow / Loan",
			[{"asset": asset.name, "target_location": self.customer_location}],
			borrow_item_type="Asset",
			recipient_type=recipient_type,
			recipient=recipient,
		)
		loan = self.make_custody_loan(request, "Asset")
		loan.submit()
		return loan, asset

	def assert_stock(self, item_code, source_qty, loan_qty):
		for warehouse, qty in ((self.source_warehouse, source_qty), (self.loan_warehouse, loan_qty)):
			actual = frappe.db.get_value(
				"Bin", {"item_code": item_code, "warehouse": warehouse}, "actual_qty"
			)
			self.assertEqual(flt(actual), qty)

	def assert_sle(self, voucher_type, voucher_no, expected):
		rows = frappe.get_all(
			"Stock Ledger Entry",
			filters={
				"voucher_type": voucher_type,
				"voucher_no": voucher_no,
				"is_cancelled": 0,
			},
			fields=["warehouse", "item_code", "actual_qty", "stock_value_difference", "voucher_detail_no"],
		)
		self.assertEqual(len(rows), len(expected))
		actual = sorted(
			(r.warehouse, r.item_code, flt(r.actual_qty), flt(r.stock_value_difference, 2)) for r in rows
		)
		self.assertEqual(actual, sorted(expected))
		self.assertTrue(all(r.voucher_detail_no for r in rows))

	def gl_net(self, voucher_type, voucher_no, active_only=False):
		filters = {"voucher_type": voucher_type, "voucher_no": voucher_no}
		if active_only:
			filters["is_cancelled"] = 0
		result = {}
		for row in frappe.get_all(
			"GL Entry", filters=filters, fields=["account", "debit", "credit", "company"]
		):
			self.assertEqual(row.company, self.company)
			result[row.account] = flt(result.get(row.account, 0) + row.debit - row.credit, 2)
		return {account: value for account, value in result.items() if value}

	def assert_balance(self, loan, returned, adjusted, outstanding, status):
		loan.reload()
		row = loan.items[0]
		self.assertEqual(
			(row.returned_qty, row.adjusted_qty, row.outstanding_qty), (returned, adjusted, outstanding)
		)
		self.assertEqual(loan.status, status)

	def assert_request_rejected(self, operation, loan):
		"""Emulate the request transaction owner, not controller-local rollback."""
		loan.reload()
		before = (loan.as_dict(), self.gl_net("Delivery Note", loan.delivery_note))
		counts = {
			dt: frappe.db.count(dt)
			for dt in (
				"Delivery Note",
				"Stock Ledger Entry",
				"GL Entry",
				"Custody Loan Return",
				"Custody Loan Adjustment",
			)
		}
		savepoint = f"loan_rejection_{frappe.generate_hash(length=10)}"
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaises(frappe.ValidationError):
				operation()
		finally:
			frappe.db.rollback(save_point=savepoint)
		loan.reload()
		self.assertEqual(loan.as_dict(), before[0])
		self.assertEqual(self.gl_net("Delivery Note", loan.delivery_note), before[1])
		for dt, count in counts.items():
			self.assertEqual(frappe.db.count(dt), count)

	def test_converted_hold_value_asset_can_be_loaned_returned_and_reversed(self):
		category = frappe.get_doc(
			{
				"doctype": "Asset Category",
				"asset_category_name": f"Loan conversion {frappe.generate_hash(length=10)}",
				"finance_books": [
					{
						"depreciation_method": "Straight Line",
						"total_number_of_depreciations": 3,
						"frequency_of_depreciation": 1,
						"depreciation_start_date": get_last_day(today()),
						"salvage_value_percentage": 0,
					}
				],
				"accounts": [
					{
						"company_name": self.company,
						"fixed_asset_account": "_Test Fixed Asset - _TC",
						"accumulated_depreciation_account": "_Test Accumulated Depreciations - _TC",
						"depreciation_expense_account": "_Test Depreciations - _TC",
					}
				],
			}
		).insert()
		asset_item = request_fixtures.create_fixed_asset_item(
			item_code=f"Converted loan asset {frappe.generate_hash(length=10)}",
			asset_category=category.name,
		)
		stock_item = make_item(
			properties={
				"is_stock_item": 1,
				"stock_uom": "Nos",
				"demo_asset_item_code": asset_item.name,
			}
		)
		make_stock_entry(
			company=self.company,
			item_code=stock_item.name,
			qty=1,
			to_warehouse=self.source_warehouse,
			rate=100,
			posting_date=today(),
		)
		conversion = frappe.get_doc(
			{
				"doctype": "Stock to Asset Conversion",
				"company": self.company,
				"posting_date": today(),
				"asset_location": self.source_location,
				"stock_items": [{"item_code": stock_item.name, "warehouse": self.source_warehouse, "qty": 1}],
			}
		).insert()
		self.assertEqual(len(conversion.assets), 1)
		asset = frappe.get_doc("Asset", conversion.assets[0].asset)
		asset.stock_to_asset_accounting_treatment = "Hold Value"
		asset.save()
		conversion.save()
		conversion.submit()
		conversion.reload()
		asset.reload()
		self.assertEqual(
			(
				asset.docstatus,
				asset.stock_to_asset_conversion,
				asset.stock_to_asset_accounting_treatment,
				asset.value_after_depreciation,
			),
			(1, conversion.name, "Hold Value", 100),
		)
		self.assertFalse(asset.calculate_depreciation)
		self.assert_stock(stock_item.name, 0, 0)
		self.assert_sle(
			conversion.doctype, conversion.name, [(self.source_warehouse, stock_item.name, -1, -100)]
		)
		self.assertEqual(
			self.gl_net(conversion.doctype, conversion.name, active_only=True),
			{"Stock In Hand - _TC": -100, asset.get_fixed_asset_account(): 100},
		)
		request = self.make_request(
			"Customer Borrow / Loan",
			[{"asset": asset.name, "target_location": self.customer_location}],
			borrow_item_type="Asset",
		)
		loan = self.make_custody_loan(request, "Asset")
		loan.submit()
		asset.reload()
		self.assertEqual((asset.location, asset.value_after_depreciation), (self.customer_location, 100))
		# A direct test call emulates the request transaction owner's rollback.
		savepoint = f"loan_conversion_cancel_{frappe.generate_hash(length=10)}"
		frappe.db.savepoint(savepoint)
		try:
			with self.assertRaisesRegex(frappe.ValidationError, "active Asset Movement"):
				conversion.cancel()
		finally:
			frappe.db.rollback(save_point=savepoint)
		conversion.reload()
		asset.reload()
		self.assertEqual(
			(conversion.docstatus, asset.docstatus, asset.location), (1, 1, self.customer_location)
		)
		self.assert_stock(stock_item.name, 0, 0)
		self.assertEqual(
			self.gl_net(conversion.doctype, conversion.name, active_only=True),
			{"Stock In Hand - _TC": -100, asset.get_fixed_asset_account(): 100},
		)
		returned = loan.make_return()
		returned.submit()
		asset.reload()
		loan.reload()
		self.assertEqual(
			(loan.status, asset.location, asset.value_after_depreciation),
			("Returned", self.source_location, 100),
		)
		returned.cancel()
		loan.reload()
		loan.cancel()
		request.reload()
		request.cancel()
		conversion.reload()
		conversion.cancel()
		asset.reload()
		self.assertEqual(asset.docstatus, 2)
		self.assert_stock(stock_item.name, 1, 0)
		self.assertEqual(self.gl_net(conversion.doctype, conversion.name), {})
		self.assertFalse(
			frappe.db.exists(
				"Stock Ledger Entry",
				{
					"voucher_type": conversion.doctype,
					"voucher_no": conversion.name,
					"is_cancelled": 0,
				},
			)
		)

	def test_stock_partial_receipt_cancellation_restores_ledgers_and_balances(self):
		loan, issued, _ = self.stock_loan()
		item = loan.items[0].item_code
		self.assert_stock(item, 0, 3)
		self.assert_sle(
			"Delivery Note",
			issued.name,
			[
				(self.source_warehouse, item, -3, -300),
				(self.loan_warehouse, item, 3, 300),
			],
		)
		self.assertEqual(self.gl_net("Delivery Note", issued.name, active_only=True), {})
		returned = loan.make_return()
		returned.items[0].returned_qty = 1
		returned.submit()
		self.complete_stock_loan_return(returned)
		received = returned.delivery_note
		self.assert_stock(item, 1, 2)
		self.assert_sle(
			"Delivery Note",
			received,
			[
				(self.source_warehouse, item, 1, 100),
				(self.loan_warehouse, item, -1, -100),
			],
		)
		self.assertEqual(self.gl_net("Delivery Note", received, active_only=True), {})
		self.assert_balance(loan, 1, 0, 2, "Partially Returned")
		returned.cancel()
		self.assert_stock(item, 0, 3)
		self.assert_balance(loan, 0, 0, 3, "Active")
		self.assertEqual(frappe.db.get_value("Delivery Note", received, "docstatus"), 2)
		self.assertFalse(frappe.db.exists("Stock Ledger Entry", {"voucher_no": received, "is_cancelled": 0}))
		loan.reload()
		loan.cancel()
		self.assert_stock(item, 3, 0)
		self.assertFalse(
			frappe.db.exists("Stock Ledger Entry", {"voucher_no": issued.name, "is_cancelled": 0})
		)

	def test_stock_write_off_cancellation_reverses_exact_accounting(self):
		loan, _, _ = self.stock_loan(qty=2)
		item = loan.items[0].item_code
		adjustment = loan.make_adjustment()
		adjustment.items[0].reason = "Lost while in borrower custody"
		adjustment.submit()
		entry = adjustment.stock_entry
		self.assert_sle("Stock Entry", entry, [(self.loan_warehouse, item, -2, -200)])
		self.assertEqual(
			self.gl_net("Stock Entry", entry, active_only=True),
			{
				"Stock Adjustment - _TC": 200,
				"Stock In Hand - _TC": -200,
			},
		)
		self.assert_stock(item, 0, 0)
		self.assert_balance(loan, 0, 2, 0, "Closed")
		self.assert_request_rejected(loan.cancel, loan)
		adjustment.cancel()
		self.assertEqual(self.gl_net("Stock Entry", entry), {})
		self.assert_stock(item, 0, 2)
		self.assert_balance(loan, 0, 0, 2, "Active")
		loan.reload()
		loan.cancel()
		self.assert_stock(item, 2, 0)

	def test_serial_return_and_write_off_preserve_original_identities(self):
		loan, _, serials = self.stock_loan(qty=2, serialized=True)
		self.assertEqual(set(loan.items[0].serial_no.splitlines()), set(serials))
		for serial in serials:
			self.assertEqual(frappe.db.get_value("Serial No", serial, "warehouse"), self.loan_warehouse)
		returned = loan.make_return()
		returned.items[0].returned_qty = 1
		returned.items[0].serial_no = serials[0]
		returned.submit()
		self.complete_stock_loan_return(returned)
		self.assertEqual(frappe.db.get_value("Serial No", serials[0], "warehouse"), self.source_warehouse)
		self.assert_balance(loan, 1, 0, 1, "Partially Returned")
		duplicate = loan.make_return()
		duplicate.items[0].serial_no = serials[0]
		self.assert_request_rejected(duplicate.submit, loan)
		foreign = loan.make_return()
		foreign.items[0].serial_no = f"UNISSUED-{frappe.generate_hash(length=10)}"
		self.assert_request_rejected(foreign.submit, loan)
		adjustment = loan.make_adjustment()
		self.assertEqual(adjustment.items[0].serial_no, serials[1])
		adjustment.items[0].reason = "Missing original serial"
		adjustment.submit()
		self.assert_balance(loan, 1, 1, 0, "Closed")
		self.assertFalse(frappe.db.get_value("Serial No", serials[1], "warehouse"))
		adjustment.cancel()
		returned.cancel()
		loan.reload()
		loan.cancel()
		for serial in serials:
			self.assertEqual(frappe.db.get_value("Serial No", serial, "warehouse"), self.source_warehouse)

	def test_generated_delivery_note_rejects_forgery_tampering_and_direct_cancel(self):
		loan, issued, _ = self.stock_loan(qty=1)
		self.assert_request_rejected(issued.cancel, loan)
		for field, value in (("custody_loan_source_name", "unrelated-loan"), ("is_system_generated", 0)):
			tampered = frappe.get_doc("Delivery Note", issued.name)
			tampered.set(field, value)
			self.assert_request_rejected(tampered.save, loan)
		forged = frappe.copy_doc(issued)
		forged.docstatus = 0
		self.assert_request_rejected(forged.insert, loan)
		self.assert_stock(loan.items[0].item_code, 0, 1)
		self.assertEqual(frappe.db.count("Delivery Note", {"custody_loan": loan.name, "is_return": 0}), 1)

	def test_stock_user_can_submit_loan_and_return_but_cannot_cancel_or_sell(self):
		user = f"loan-stock-{frappe.generate_hash(length=10)}@example.com"
		frappe.get_doc(
			{
				"doctype": "User",
				"email": user,
				"first_name": "Loan Stock User",
				"send_welcome_email": 0,
				"user_type": "System User",
				"roles": [{"role": "Stock User"}],
			}
		).insert()
		self.addCleanup(frappe.clear_cache, user=user)
		frappe.clear_cache(user=user)
		asset = self.make_asset(f"Authorized Loan Asset {frappe.generate_hash(length=10)}")
		request = self.make_request(
			"Customer Borrow / Loan",
			[{"asset": asset.name, "target_location": self.customer_location}],
			borrow_item_type="Asset",
		)
		with self.set_user(user):
			for doctype in ("Custody Loan", "Custody Loan Return"):
				for permission in ("create", "read", "submit"):
					self.assertTrue(frappe.has_permission(doctype, permission))
				self.assertFalse(frappe.has_permission(doctype, "cancel"))
			self.assertFalse(frappe.has_permission("Sales Invoice", "create"))
			loan = self.make_custody_loan(request, "Asset")
			loan.check_permission("read")
			loan.submit()
			loan.reload()
			self.assertEqual((loan.docstatus, loan.status), (1, "Active"))
			before = (loan.as_dict(), frappe.db.get_value("Asset", asset.name, "location"))
			counts = {dt: frappe.db.count(dt) for dt in ("Sales Invoice", "Asset Movement", "GL Entry")}
			for action in ("cancel", "make_asset_sale_invoice"):
				savepoint = f"loan_permission_{frappe.generate_hash(length=10)}"
				frappe.db.savepoint(savepoint)
				try:
					with self.assertRaises(frappe.PermissionError):
						getattr(frappe.get_doc("Custody Loan", loan.name), action)()
				finally:
					frappe.db.rollback(save_point=savepoint)
				loan.reload()
				self.assertEqual(loan.as_dict(), before[0])
				self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), before[1])
				self.assertEqual(frappe.db.get_value("Asset Movement", loan.asset_movement, "docstatus"), 1)
				for dt, count in counts.items():
					self.assertEqual(frappe.db.count(dt), count)
			returned = loan.make_return()
			returned.submit()
			returned.reload()
			loan.reload()
			self.assertEqual((returned.docstatus, returned.status, loan.status), (1, "Received", "Returned"))
			self.assertEqual(frappe.db.get_value("Asset", asset.name, "location"), self.source_location)

	def test_customer_asset_sale_and_cancellation_restore_asset_and_loan(self):
		loan, asset = self.asset_loan()
		self.assertEqual(asset.value_after_depreciation, 100000)
		self.assertFalse(asset.calculate_depreciation)
		invoice = frappe.get_doc("Sales Invoice", loan.make_asset_sale_invoice()["name"])
		self.assertEqual(loan.make_asset_sale_invoice()["name"], invoice.name)
		invoice.set("taxes", [])
		invoice.items[0].rate = 120000
		invoice.save()
		invoice.submit()
		invoice.reload()
		asset.reload()
		loan.reload()
		self.assertEqual(asset.status, "Sold")
		self.assertEqual(
			(loan.items[0].sold_qty, loan.items[0].outstanding_qty, loan.status), (1, 0, "Closed")
		)
		self.assertEqual(invoice.outstanding_amount, 120000)
		self.assertEqual(
			self.gl_net("Sales Invoice", invoice.name, active_only=True),
			{
				invoice.debit_to: 120000,
				asset.get_fixed_asset_account(): -100000,
				invoice.items[0].income_account: -20000,
			},
		)
		self.assertFalse(frappe.db.exists("Stock Ledger Entry", {"voucher_no": invoice.name}))
		self.assert_request_rejected(invoice.cancel, loan)
		loan.cancel_asset_sale_invoice()
		self.assertEqual(self.gl_net("Sales Invoice", invoice.name), {})
		asset.reload()
		loan.reload()
		self.assertNotEqual(asset.status, "Sold")
		self.assertEqual(
			(loan.items[0].sold_qty, loan.items[0].outstanding_qty, loan.status), (0, 1, "Active")
		)
		loan.reload()
		loan.cancel()
		asset.reload()
		self.assertEqual(asset.location, self.source_location)

	def test_employee_asset_return_cancellation_restores_custodian_then_issue_baseline(self):
		employee = make_employee(f"loan-{frappe.generate_hash(length=10)}@example.com", company=self.company)
		loan, asset = self.asset_loan("Employee", employee)
		asset.reload()
		self.assertEqual(asset.custodian, employee)
		self.assertEqual(asset.location, self.customer_location)
		returned = loan.make_return()
		returned.submit()
		asset.reload()
		self.assertFalse(asset.custodian)
		self.assertEqual(asset.location, self.source_location)
		returned.cancel()
		asset.reload()
		self.assertEqual(asset.custodian, employee)
		self.assertEqual(asset.location, self.customer_location)
		loan.reload()
		loan.cancel()
		asset.reload()
		self.assertFalse(asset.custodian)
		self.assertEqual(asset.location, self.source_location)

	def report_rows(self, report, loan):
		execute = frappe.get_attr(f"erpnext.loan.report.{report}.{report}.execute")
		return [row for row in execute({"company": self.company})[1] if row.loan == loan.name]

	def test_register_stock_balance_and_overdue_show_fixture_then_exclude_cancelled(self):
		loan, _, _ = self.stock_loan(qty=3)
		returned = loan.make_return()
		returned.items[0].returned_qty = 1
		returned.submit()
		self.complete_stock_loan_return(returned)
		for report in ("custody_loan_register", "custody_loan_stock_balance"):
			rows = self.report_rows(report, loan)
			self.assertEqual(len(rows), 1)
			row = rows[0]
			self.assertEqual((row.returned_qty, row.adjusted_qty, row.outstanding_qty), (1, 0, 2))
			self.assertEqual(row.material_request, loan.material_request)
			self.assertEqual(row.borrower, "_Test Customer")
		self.assertEqual(self.report_rows("overdue_custody_loans", loan), [])
		returned.cancel()
		loan.reload()
		loan.cancel()
		for report in ("custody_loan_register", "custody_loan_stock_balance", "overdue_custody_loans"):
			self.assertEqual(self.report_rows(report, loan), [])

	def test_overdue_report_has_real_open_asset_then_excludes_received_and_cancelled(self):
		asset = self.make_asset(f"Overdue Loan Asset {frappe.generate_hash(length=10)}")
		request = frappe.new_doc("Material Request")
		request.material_request_type = "Customer Borrow / Loan"
		request.company = self.company
		request.borrow_item_type = "Asset"
		request.loan_recipient_type = "Customer"
		request.loan_recipient = "_Test Customer"
		request.customer = "_Test Customer"
		request.recipient_address = self.customer_address
		request.contact_person = self.customer_contact
		request.loan_recipient_contact = self.customer_contact
		request.loan_recipient_location = self.customer_location
		request.customer_location = self.customer_location
		request.transaction_date = add_days(today(), -3)
		request.schedule_date = add_days(today(), -2)
		request.expected_return_date = add_days(today(), -1)
		item = frappe.get_doc("Item", asset.item_code)
		request.append(
			"items",
			{
				"asset": asset.name,
				"item_code": item.name,
				"item_name": item.item_name,
				"qty": 1,
				"uom": item.stock_uom,
				"stock_uom": item.stock_uom,
				"conversion_factor": 1,
				"schedule_date": request.schedule_date,
				"target_location": self.customer_location,
			},
		)
		request_fixtures.prepare_request_prices(request)
		request.insert()
		if frappe.db.exists("Workflow", {"document_type": "Material Request", "is_active": 1}):
			request_fixtures.apply_workflow(request.as_dict(), "Request Approval")
			request.reload()
			transitions = [
				t for t in request_fixtures.get_transitions(request) if t.next_state == "Submitted"
			]
			self.assertEqual(len(transitions), 1)
			request_fixtures.apply_workflow(request.as_dict(), transitions[0].action)
			request.reload()
		else:
			request.submit()
		loan = self.make_custody_loan(request, "Asset")
		loan.submit()
		rows = self.report_rows("overdue_custody_loans", loan)
		self.assertEqual(len(rows), 1)
		self.assertEqual(
			(rows[0].asset, rows[0].outstanding_qty, rows[0].borrower), (asset.name, 1, "_Test Customer")
		)
		self.assertEqual(rows[0].overdue_days, date_diff(today(), request.expected_return_date))
		returned = loan.make_return()
		returned.submit()
		self.assertEqual(self.report_rows("overdue_custody_loans", loan), [])
		returned.cancel()
		self.assertEqual(len(self.report_rows("overdue_custody_loans", loan)), 1)
		loan.reload()
		loan.cancel()
		self.assertEqual(self.report_rows("overdue_custody_loans", loan), [])

	def test_asset_balance_and_movement_history_follow_return_and_cancellation(self):
		loan, asset = self.asset_loan()
		rows = self.report_rows("custody_loan_asset_balance", loan)
		self.assertEqual(len(rows), 1)
		self.assertEqual(
			(rows[0].asset, rows[0].outstanding_qty, rows[0].current_location),
			(asset.name, 1, self.customer_location),
		)
		returned = loan.make_return()
		returned.submit()
		self.assertEqual(self.report_rows("custody_loan_asset_balance", loan), [])
		rows = self.report_rows("custody_loan_movement_history", loan)
		self.assertEqual({r.action for r in rows}, {"Issue", "Return"})
		self.assertEqual({r.source_document for r in rows}, {loan.asset_movement, returned.asset_movement})
		execute_history = frappe.get_attr(
			"erpnext.loan.report.custody_loan_movement_history.custody_loan_movement_history.execute"
		)
		filters = {"company": self.company, "loan_type": "Asset", "from_date": today(), "to_date": today()}
		same_day = [row for row in execute_history(filters)[1] if row.loan == loan.name]
		self.assertEqual(
			{row.source_document for row in same_day}, {loan.asset_movement, returned.asset_movement}
		)
		self.assertEqual({row.action for row in same_day}, {"Issue", "Return"})
		filters.update(from_date=add_days(today(), 1), to_date=add_days(today(), 1))
		self.assertEqual([row for row in execute_history(filters)[1] if row.loan == loan.name], [])
		for row in rows:
			self.assertEqual((row.asset, row.qty), (asset.name, 1))
		returned.cancel()
		self.assertEqual(len(self.report_rows("custody_loan_movement_history", loan)), 1)
		loan.reload()
		loan.cancel()
		for report in ("custody_loan_asset_balance", "custody_loan_movement_history"):
			self.assertEqual(self.report_rows(report, loan), [])

	def test_stock_movement_history_includes_delivery_note_issue_and_receipt(self):
		loan, issued, _ = self.stock_loan(qty=2)
		returned = loan.make_return()
		returned.items[0].returned_qty = 1
		returned.submit()
		self.complete_stock_loan_return(returned)
		rows = self.report_rows("custody_loan_movement_history", loan)
		self.assertEqual(len(rows), 2)
		by_action = {row.action: row for row in rows}
		self.assertEqual(set(by_action), {"Issue", "Return"})
		for action, name, qty, source, target in (
			("Issue", issued.name, 2, self.source_warehouse, self.loan_warehouse),
			("Return", returned.delivery_note, 1, self.loan_warehouse, self.source_warehouse),
		):
			row = by_action[action]
			self.assertEqual(
				(
					row.source_doctype,
					row.source_document,
					row.item_code,
					row.qty,
					row.from_location,
					row.to_location,
				),
				("Delivery Note", name, loan.items[0].item_code, qty, source, target),
			)
		returned.cancel()
		self.assertEqual(len(self.report_rows("custody_loan_movement_history", loan)), 1)
		loan.reload()
		loan.cancel()
		self.assertEqual(self.report_rows("custody_loan_movement_history", loan), [])
