from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.loan.doctype.custody_loan import custody_loan_core as core
from erpnext.loan.doctype.custody_loan import custody_loan_set as loan_set
from erpnext.patches.v16_0 import release_loans_waiting_for_shipment as shipment_patch


class Document(SimpleNamespace):
	def get(self, field, default=None):
		return getattr(self, field, default)


class TestExplicitDelivery(TestCase):
	def setUp(self):
		self.database = Mock()
		self.db_patch = patch.object(frappe, "db", self.database)
		self.db_patch.start()
		self.addCleanup(self.db_patch.stop)
		settings = patch.object(frappe, "get_system_settings", return_value="Banker's Rounding")
		settings.start()
		self.addCleanup(settings.stop)

	def loan(self, kind="Asset"):
		return Document(
			name=f"LOAN-{kind}",
			material_request="MR-1",
			loan_type=kind,
			docstatus=1,
			pick_list="PICK-1" if kind == "Stock" else None,
			asset_movement=None,
			reload=Mock(),
			check_permission=Mock(),
			validate_material_request=Mock(),
			validate_items=Mock(),
			run_method=Mock(),
			release_assets=Mock(),
			make_pick_list=Mock(),
			update_status=Mock(),
		)

	def test_submitting_a_loan_never_picks_or_moves_goods(self):
		for kind in ("Asset", "Stock"):
			loan = self.loan(kind)
			core.CustodyLoan.on_submit(loan)
			loan.make_pick_list.assert_not_called()
			loan.release_assets.assert_not_called()
			loan.update_status.assert_called_once()

	def test_picking_never_creates_or_submits_a_delivery_note(self):
		loan = self.loan("Stock")
		loan.delivery_note = None
		with (
			patch.object(frappe, "get_doc", return_value=loan),
			patch.object(core, "follow_picked_warehouses"),
		):
			core.update_custody_loan_pick_list_status(Document(custody_loan=loan.name, docstatus=1))
		loan.update_status.assert_called_once()
		loan.release_assets.assert_not_called()

	def test_shipment_patch_keeps_outgoing_delivery_explicit_but_processes_returns_and_status(self):
		loan = self.loan("Stock")
		loan.requires_shipment = 1
		loan.delivery_note = "DN-OUT"
		loan.confirm_delivery = Mock()
		outgoing = Document(docstatus=0, submit=Mock())
		returned = Document(docstatus=0)
		return_doc = Document(
			delivery_note="DN-BACK",
			update_status=Mock(),
			inspect_or_receive=Mock(side_effect=lambda note: setattr(note, "docstatus", 1)),
		)
		documents = {
			("Custody Loan", loan.name): loan,
			("Custody Loan Return", "RETURN-1"): return_doc,
			("Delivery Note", "DN-OUT"): outgoing,
			("Delivery Note", "DN-BACK"): returned,
		}
		self.database.table_exists.return_value = True
		self.database.has_column.return_value = True
		self.database.sql_list.return_value = [loan.name]
		self.database.get_value.side_effect = lambda doctype, name, field: (
			1 if doctype == "Pick List" else documents[(doctype, name)].docstatus
		)
		with (
			patch.object(frappe, "get_doc", side_effect=lambda doctype, name: documents[(doctype, name)]),
			patch.object(frappe, "get_all", return_value=["RETURN-1"]) as get_all,
			patch.object(frappe, "log_error") as log_error,
		):
			shipment_patch.execute()
			shipment_patch.execute()
		outgoing.submit.assert_not_called()
		self.assertEqual(outgoing.docstatus, 0)
		loan.confirm_delivery.assert_not_called()
		return_doc.inspect_or_receive.assert_called_once_with(returned)
		self.assertEqual(return_doc.update_status.call_count, 2)
		self.assertEqual(loan.update_status.call_count, 2)
		get_all.assert_called_with(
			"Custody Loan Return", filters={"custody_loan": loan.name, "docstatus": 1}, pluck="name"
		)
		self.assertEqual(self.database.savepoint.call_count, 2)
		self.database.rollback.assert_not_called()
		log_error.assert_not_called()

	def test_an_asset_loan_without_movement_waits_for_explicit_handover(self):
		self.assertEqual(core.CustodyLoan.get_submitted_status(self.loan()), "Pending Delivery")

	def test_confirm_delivery_checks_authority_before_starting(self):
		loan = self.loan()
		loan.check_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			core.CustodyLoan.confirm_delivery(loan)
		loan.release_assets.assert_not_called()

	def test_confirm_delivery_checks_then_moves_and_is_repeatable(self):
		loan = self.loan()
		with patch.object(core, "delivery_loans", return_value=[loan]):
			self.assertEqual(core.CustodyLoan.confirm_delivery(loan), {"loans": [loan.name]})
			loan.release_assets.assert_called_once()
			loan.run_method.assert_called_with("before_delivery")
			loan.asset_movement = "MOVE-1"
			core.CustodyLoan.confirm_delivery(loan)
			loan.release_assets.assert_called_once()

	def test_failed_movement_rolls_back_the_entire_delivery(self):
		loan = self.loan()
		loan.release_assets.side_effect = frappe.ValidationError
		with (
			patch.object(core, "delivery_loans", return_value=[loan]),
			self.assertRaises(frappe.ValidationError),
		):
			core.CustodyLoan.confirm_delivery(loan)
		self.database.rollback.assert_called_once_with(save_point="custody_loan_confirm_delivery")

	def test_mixed_request_without_stock_loan_cannot_deliver_asset(self):
		loan = self.loan()
		request = Document(check_permission=Mock())
		with (
			patch.object(frappe, "get_doc", side_effect=[request, loan]),
			patch.object(frappe, "get_all", return_value=[loan.name]),
			patch.object(core, "request_loan_types", return_value=["Asset", "Stock"]),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			core.delivery_loans(loan)

	def test_stock_must_be_picked_before_any_part_of_set_is_delivered(self):
		asset, stock = self.loan(), self.loan("Stock")
		self.database.get_value.return_value = 0
		with (
			patch.object(core, "delivery_loans", return_value=[asset, stock]),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			core.CustodyLoan.confirm_delivery(asset)
		asset.release_assets.assert_not_called()

	def test_asset_readiness_is_checked_again_when_handover_starts(self):
		asset = self.loan()
		asset.validate_items.side_effect = frappe.ValidationError("Asset is now Out of Order")
		with (
			patch.object(core, "delivery_loans", return_value=[asset]),
			self.assertRaises(frappe.ValidationError),
		):
			core.CustodyLoan.confirm_delivery(asset)
		asset.release_assets.assert_not_called()

	def test_native_loan_api_cannot_lend_stock_waiting_for_conversion(self):
		loan = self.loan("Stock")
		loan.company = "CD"
		request = Document(
			docstatus=1,
			material_request_type="Customer Demo",
			company="CD",
			items=[frappe._dict(demo_acquisition_method="Convert Stock", asset=None)],
		)
		with (
			patch.object(frappe, "get_doc", return_value=request),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			core.CustodyLoan.validate_material_request(loan)

	def test_pick_preparation_reuses_existing_draft(self):
		self.database.get_value.return_value = 0
		with patch.object(frappe, "get_doc", return_value="EXISTING-PICK"):
			self.assertEqual(core.CustodyLoan.make_pick_list(self.loan("Stock")), "EXISTING-PICK")

	def test_consumption_invoice_reuses_immutable_source_link(self):
		source = Document(
			name="ADJ-1",
			doctype="Custody Loan Adjustment",
			custody_loan="LOAN-Stock",
			docstatus=1,
			reason="Consumed by the borrower",
			check_permission=Mock(),
		)
		loan = self.loan("Stock")
		loan.borrower_type = "Customer"
		invoice = Document(check_permission=Mock(), as_dict=lambda: {"name": "SI-1"})
		self.database.get_value.side_effect = ["Customer Borrow / Loan", "SI-1"]
		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "get_doc", side_effect=[source, loan, invoice]),
		):
			self.assertEqual(loan_set.create_consumption_invoice("ADJ-1"), {"name": "SI-1"})
		self.assertEqual(self.database.get_value.call_args.args[1]["custody_loan_source_name"], "ADJ-1")

	def test_consumption_invoice_checks_create_permission(self):
		with (
			patch.object(frappe, "has_permission", side_effect=frappe.PermissionError),
			self.assertRaises(frappe.PermissionError),
		):
			loan_set.create_consumption_invoice("ADJ-1")
		self.database.sql.assert_not_called()

	def test_stock_submission_is_rolled_back_if_asset_handover_fails(self):
		stock, asset = self.loan("Stock"), self.loan()
		stock._make_delivery_note = Mock(return_value={"name": "DN-1"})
		note = Document(docstatus=0, meta=Mock(), flags=frappe._dict(), submit=Mock())
		asset.release_assets.side_effect = frappe.ValidationError
		self.database.get_value.return_value = 1
		with (
			patch.object(core, "delivery_loans", return_value=[stock, asset]),
			patch.object(frappe, "get_doc", return_value=note),
			self.assertRaises(frappe.ValidationError),
		):
			core.CustodyLoan.confirm_delivery(stock)
		note.submit.assert_called_once()
		self.database.rollback.assert_called_once_with(save_point="custody_loan_confirm_delivery")

	def consumption(self):
		source = Document(
			name="ADJ-1",
			docstatus=1,
			custody_loan="LOAN-Stock",
			reason="Consumed by the borrower",
			items=[Document(custody_loan_item="ROW-1", qty=2)],
		)
		loan = self.loan("Stock")
		loan.company, loan.borrower_type, loan.borrower = "CD", "Customer", "CUST-1"
		invoice = Document(
			name="SI-1",
			company="CD",
			customer="CUST-1",
			is_return=0,
			update_stock=0,
			items=[
				Document(
					custody_loan_item="ROW-1", item_code="ITEM-1", qty=2, uom="Unit", conversion_factor=1
				)
			],
		)
		return source, loan, invoice

	def test_consumption_invoice_cannot_increase_quantity_or_move_stock(self):
		source, loan, invoice = self.consumption()
		self.database.get_value.return_value = "Customer Borrow / Loan"
		self.database.exists.return_value = False
		with (
			patch.object(frappe, "get_doc", return_value=source),
			patch.object(frappe, "get_cached_value", return_value="Unit"),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
		):
			core.validate_consumption_invoice(invoice, loan, source.name)
			invoice.items[0].qty = 3
			with self.assertRaises(frappe.ValidationError):
				core.validate_consumption_invoice(invoice, loan, source.name)
			invoice.items[0].qty = 2
			invoice.update_stock = 1
			with self.assertRaises(frappe.ValidationError):
				core.validate_consumption_invoice(invoice, loan, source.name)

	def test_consumption_invoice_refuses_a_second_active_invoice(self):
		source, loan, invoice = self.consumption()
		self.database.get_value.return_value = "Customer Borrow / Loan"
		self.database.exists.return_value = True
		with (
			patch.object(frappe, "get_doc", return_value=source),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			core.validate_consumption_invoice(invoice, loan, source.name)

	def test_consumption_adjustment_cannot_cancel_before_its_invoice(self):
		self.database.exists.return_value = True
		with (
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			core.CustodyLoanAdjustment.before_cancel(Document(name="ADJ-1"))
