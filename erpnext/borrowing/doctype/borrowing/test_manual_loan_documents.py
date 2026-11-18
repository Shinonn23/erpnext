"""Offline checks for explicit, linked custody movement documents."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.borrowing.doctype.borrowing import borrowing_core as core


class Doc(SimpleNamespace):
	def get(self, key, default=None):
		return getattr(self, key, default)


class TestManualLoanDocuments(TestCase):
	def setUp(self):
		for patcher in (
			patch.object(frappe, "db", Mock()),
			patch.object(frappe, "get_cached_value", return_value="Unit"),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(core, "_", lambda message: message),
			patch.object(core, "flt", lambda value, precision=None: float(value or 0)),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def source(self):
		return Doc(
			doctype="Borrowing",
			name="LOAN-1",
			borrowing="LOAN-1",
			company="CD",
			docstatus=1,
			loan_type="Stock",
			status="Active",
			reload=Mock(),
			check_permission=Mock(),
		)

	def reused_target_cases(self):
		asset_loan = self.source()
		asset_loan.loan_type = "Asset"
		asset_loan.asset_movement = "AM-1"
		adjustment = self.source()
		adjustment.stock_entry = "STE-1"
		return (
			(core.Borrowing.prepare_asset_movement, asset_loan),
			(core.BorrowingAdjustment.prepare_stock_entry, adjustment),
		)

	def test_reused_movement_documents_require_read_permission(self):
		frappe.db.get_value.return_value = 0
		for method, source in self.reused_target_cases():
			with self.subTest(method=method.__name__, loan_type=source.loan_type):
				target = Mock()
				target.check_permission.side_effect = frappe.PermissionError
				with (
					patch.object(frappe, "has_permission", return_value=True),
					patch.object(frappe, "get_doc", return_value=target),
					self.assertRaises(frappe.PermissionError),
				):
					method(source)
				target.check_permission.assert_called_once_with("read")
				target.as_dict.assert_not_called()

	def test_readable_existing_movement_documents_are_reused(self):
		frappe.db.get_value.return_value = 0
		for method, source in self.reused_target_cases():
			with self.subTest(method=method.__name__, loan_type=source.loan_type):
				target = Mock()
				target.as_dict.return_value = {"name": "EXISTING"}
				with (
					patch.object(frappe, "has_permission", return_value=True),
					patch.object(frappe, "get_doc", return_value=target),
				):
					self.assertEqual(method(source), {"name": "EXISTING"})
				target.check_permission.assert_called_once_with("read")

	def test_reused_pick_list_requires_read_permission(self):
		source = Doc(pick_list="PL-1")
		frappe.db.get_value.return_value = 0
		target = Mock()
		target.check_permission.side_effect = frappe.PermissionError
		with (
			patch.object(frappe, "get_doc", return_value=target),
			self.assertRaises(frappe.PermissionError),
		):
			core.Borrowing.make_pick_list(source)
		target.check_permission.assert_called_once_with("read")

	def test_reused_delivery_note_requires_read_permission(self):
		for linked in (False, True):
			with self.subTest(linked=linked):
				source = Doc(
					name="LOAN-1",
					doctype="Borrowing",
					docstatus=1,
					loan_type="Stock",
					delivery_note="DN-1" if linked else None,
					pick_list="PL-1",
					reload=Mock(),
					db_set=Mock(),
				)
				target = Mock()
				target.check_permission.side_effect = frappe.PermissionError
				frappe.db.get_value.side_effect = [0] if linked else [1, "DN-1"]
				with (
					patch.object(frappe, "get_doc", side_effect=[target] if linked else [Mock(), target]),
					patch.object(core, "lock_borrowing"),
					patch.object(core, "validate_borrowing_pick_list"),
					self.assertRaises(frappe.PermissionError),
				):
					core.Borrowing._make_delivery_note(source)
				target.check_permission.assert_called_once_with("read")
				target.as_dict.assert_not_called()
				source.db_set.assert_not_called()
		frappe.db.get_value.side_effect = None

	def test_opening_existing_delivery_note_does_not_write_source(self):
		for linked in (False, True):
			with self.subTest(linked=linked):
				source = Doc(
					name="LOAN-1",
					doctype="Borrowing",
					docstatus=1,
					loan_type="Stock",
					delivery_note="DN-1" if linked else None,
					pick_list="PL-1",
					reload=Mock(),
					check_permission=Mock(),
					db_set=Mock(),
				)
				source._make_delivery_note = lambda: core.Borrowing._make_delivery_note(source)
				target = Mock()
				target.as_dict.return_value = {"doctype": "Delivery Note", "name": "DN-1"}
				frappe.db.get_value.side_effect = [0] if linked else [1, "DN-1"]
				with (
					patch.object(frappe, "has_permission", return_value=True),
					patch.object(frappe, "get_doc", side_effect=[target] if linked else [Mock(), target]),
					patch.object(core, "lock_borrowing"),
					patch.object(core, "validate_borrowing_pick_list"),
				):
					self.assertEqual(core.Borrowing.make_delivery_note(source), target.as_dict.return_value)
				source.check_permission.assert_called_once_with("read")
				target.check_permission.assert_called_once_with("read")
				source.db_set.assert_not_called()
				target.insert.assert_not_called()
				target.save.assert_not_called()
		frappe.db.get_value.side_effect = None
		frappe.db.set_value.assert_not_called()

	def exchange(self):
		return Doc(
			name="STE-1",
			borrowing_source_name="LOAN-1",
			delivery_note_no="DN-RET-1",
			purpose="Repack",
			additional_costs=[],
			items=[
				Doc(
					item_code="ITEM",
					borrowing_item="ROW",
					batch_no="OLD",
					qty=2,
					uom="Unit",
					stock_uom="Unit",
					conversion_factor=1,
					transfer_qty=2,
					s_warehouse="LOAN-WH",
					t_warehouse=None,
					is_finished_item=0,
				),
				Doc(
					item_code="ITEM",
					borrowing_item="ROW",
					batch_no="NEW",
					qty=2,
					uom="Unit",
					stock_uom="Unit",
					conversion_factor=1,
					transfer_qty=2,
					s_warehouse=None,
					t_warehouse="LOAN-WH",
					is_finished_item=1,
				),
			],
		)

	def validate_exchange(self, document, return_note=None):
		"""The batch swap a return Delivery Note posts (UX WP4): checked against its return note and the loan."""
		frappe.db.get_value.return_value = return_note or frappe._dict(
			borrowing="LOAN-1", is_return=1, is_borrowing_transaction=1, docstatus=0
		)
		core.validate_batch_swap(document, Doc(name="LOAN-1", customer_loan_warehouse="LOAN-WH"))

	def test_exact_batch_exchange_is_allowed(self):
		self.validate_exchange(self.exchange())

	def test_changed_qty_warehouse_or_duplicate_exchange_is_refused(self):
		for field, value in (("qty", 3), ("s_warehouse", "FOREIGN"), ("item_code", "OTHER")):
			with self.subTest(field=field):
				document = self.exchange()
				setattr(document.items[0], field, value)
				with self.assertRaises(frappe.ValidationError):
					self.validate_exchange(document)
		for note in (
			frappe._dict(borrowing="OTHER-LOAN", is_return=1, is_borrowing_transaction=1, docstatus=0),
			frappe._dict(borrowing="LOAN-1", is_return=0, is_borrowing_transaction=1, docstatus=1),
			frappe._dict(borrowing="LOAN-1", is_return=1, is_borrowing_transaction=1, docstatus=2),
		):
			with self.subTest(note=note), self.assertRaises(frappe.ValidationError):
				self.validate_exchange(self.exchange(), return_note=note)
		document = self.exchange()
		document.delivery_note_no = None
		with self.assertRaises(frappe.ValidationError):
			self.validate_exchange(document)

	def test_own_pending_return_is_excluded_from_reservations(self):
		with (
			patch.object(core, "return_rows", return_value=[]) as rows,
			patch.object(frappe, "get_all", return_value=[]),
			patch.object(core, "loan_conversion_rows", return_value=[]),
		):
			self.assertEqual(
				core.get_loan_item_reserved_qty(
					"LOAN-1", "ROW", exclude_return=("Delivery Note", "DN-RET-1")
				),
				0,
			)
		rows.assert_called_once_with("LOAN-1", "ROW", docstatus=(0,), exclude=("Delivery Note", "DN-RET-1"))

	def test_stock_uom_conversion_transfer_qty_and_manual_rates_are_guarded(self):
		for field, value in (
			("uom", "Box"),
			("stock_uom", "Box"),
			("conversion_factor", 10),
			("transfer_qty", 20),
			("set_basic_rate_manually", 1),
			("allow_zero_valuation_rate", 1),
			("valuation_type", "Manual"),
			("secondary_item_type", "By-Product"),
		):
			with self.subTest(field=field):
				document = self.exchange()
				setattr(document.items[0], field, value)
				with self.assertRaises(frappe.ValidationError):
					self.validate_exchange(document)

	def test_pending_outgoing_and_receipt_states_refresh_stored_balances(self):
		for kind, movement, receipt in (
			("Asset", None, None),
			("Asset", "AM-1", None),
		):
			loan = Doc(
				loan_type=kind,
				asset_movement=movement,
				delivery_note=movement,
				pick_list="PL-1",
				name="LOAN-1",
				refresh_balances=Mock(),
			)
			frappe.db.get_value.return_value = 1 if kind == "Stock" else 2
			with patch.object(frappe, "get_all", return_value=[receipt] if receipt else []):
				self.assertEqual(core.Borrowing.get_submitted_status(loan), receipt or "Pending Delivery")
			loan.refresh_balances.assert_called_once()

	def test_adjustment_submit_records_pending_issue_without_posting_stock(self):
		adjustment = Doc(borrowing="LOAN-1", update_status=Mock())
		loan = Doc(update_status=Mock())
		with (
			patch.object(frappe, "get_doc", return_value=loan),
			patch.object(core, "new_stock_entry") as create,
		):
			core.BorrowingAdjustment.on_submit(adjustment)
		create.assert_not_called()
		adjustment.update_status.assert_called_once()

	def test_writeoff_builder_opens_an_unsaved_stock_uom_issue(self):
		adjustment = Doc(
			name="ADJ-1",
			doctype="Borrowing Adjustment",
			borrowing="LOAN-1",
			docstatus=1,
			company="CD",
			stock_entry=None,
			adjustment_account="EXPENSE",
			reload=Mock(),
			check_permission=Mock(),
			items=[Doc(borrowing_item="ROW-1", qty=2, batch_no=None, serial_no=None)],
		)
		loan = Doc(name="LOAN-1", customer_loan_warehouse="LOAN-WH")
		entry = Mock(flags=frappe._dict())
		entry.as_dict.return_value = {"doctype": "Stock Entry"}
		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "get_doc", side_effect=[loan, Doc(name="ROW-1", item_code="ITEM")]),
			patch.object(core, "conversion_factors", return_value={"ROW-1": 3}),
			patch.object(core, "new_stock_entry", return_value=entry),
			patch.object(core, "get_default_storage_location", return_value="LOC"),
		):
			self.assertEqual(
				core.BorrowingAdjustment.prepare_stock_entry(adjustment), {"doctype": "Stock Entry"}
			)
		self.assertEqual(entry.append.call_args.args[1]["qty"], 6)
		self.assertEqual(entry.append.call_args.args[1]["conversion_factor"], 1)
		entry.insert.assert_not_called()
		entry.submit.assert_not_called()

	def test_consumption_invoice_builder_opens_unsaved_and_keeps_invoice_approvals(self):
		from erpnext.borrowing.doctype.borrowing import borrowing_set

		loan = Doc(
			name="LOAN-1",
			company="CD",
			borrower="CUSTOMER",
			recipient_address=None,
			recipient_contact=None,
			items=[Doc(name="ROW-1", item_code="ITEM")],
		)
		source = Doc(name="ADJ-1", doctype="Borrowing Adjustment")
		invoice = Mock(flags=frappe._dict())
		with (
			patch.object(frappe, "new_doc", return_value=invoice),
			patch("erpnext.stock.get_item_details.get_item_details", return_value={}),
			patch.object(borrowing_set, "_", lambda message: message),
		):
			self.assertIs(borrowing_set._make_consumption_invoice(loan, {"ROW-1": 2}, source), invoice)
		invoice.insert.assert_not_called()
		invoice.submit.assert_not_called()
		self.assertIsNone(invoice.flags.custody_operation_token)

	def test_asset_sale_invoice_builder_does_not_insert_or_submit(self):
		loan = Doc(
			name="LOAN-1",
			doctype="Borrowing",
			company="CD",
			loan_type="Asset",
			borrower_type="Customer",
			borrower="CUSTOMER",
			docstatus=1,
			status="Active",
			recipient_address=None,
			recipient_contact=None,
			reload=Mock(),
			check_permission=Mock(),
			refresh_balances=Mock(),
			items=[Doc(name="ROW-1", item_code="ITEM", asset="AST-1", outstanding_qty=1)],
		)
		invoice = Mock(flags=frappe._dict())
		invoice.as_dict.return_value = {"doctype": "Sales Invoice"}
		frappe.db.get_value.return_value = None
		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "new_doc", return_value=invoice),
			patch.object(frappe, "get_cached_value", return_value=frappe._dict(stock_uom="Unit")),
			patch.object(core, "get_item_details", return_value={}),
		):
			self.assertEqual(core.Borrowing.make_asset_sale_invoice(loan), {"doctype": "Sales Invoice"})
		invoice.insert.assert_not_called()
		invoice.submit.assert_not_called()
		self.assertIsNone(invoice.flags.custody_operation_token)

	def test_writeoff_issue_validates_stock_quantity_account_and_own_reservation(self):
		source = Doc(
			docstatus=1,
			check_permission=Mock(),
			adjustment_account="EXPENSE",
			validate_items=Mock(),
			items=[Doc(borrowing_item="ROW-1", item_code="ITEM", qty=2, batch_no=None, serial_no=None)],
		)
		loan = Doc(name="LOAN-1", customer_loan_warehouse="LOAN-WH")
		row = Doc(
			item_code="ITEM",
			borrowing_item="ROW-1",
			qty=6,
			uom="Unit",
			stock_uom="Unit",
			conversion_factor=1,
			transfer_qty=6,
			s_warehouse="LOAN-WH",
			t_warehouse=None,
			expense_account="EXPENSE",
			batch_no=None,
			serial_no=None,
		)
		document = Doc(borrowing_source_name="ADJ-1", purpose="Material Issue", docstatus=1, items=[row])
		with (
			patch.object(frappe, "get_doc", return_value=source),
			patch.object(core, "conversion_factors", return_value={"ROW-1": 3}),
		):
			core.validate_manual_adjustment_issue(document, loan)
			source.validate_items.assert_called_once_with(loan)
			for field, value in (("qty", 2), ("expense_account", "OTHER"), ("s_warehouse", "OTHER")):
				original = getattr(row, field)
				setattr(row, field, value)
				with self.subTest(field=field), self.assertRaises(frappe.ValidationError):
					core.validate_manual_adjustment_issue(document, loan)
				setattr(row, field, original)

	def test_batch_exchange_refuses_bom_cost_allocation_even_without_from_bom(self):
		document = self.exchange()
		document.bom_no = "BOM-WITH-COST-ALLOCATION"
		document.from_bom = 0
		with self.assertRaises(frappe.ValidationError):
			self.validate_exchange(document)

	def test_one_writeoff_can_issue_multiple_batches_and_serial_groups_of_the_same_loan_row(self):
		source = Doc(
			docstatus=1,
			check_permission=Mock(),
			adjustment_account="EXPENSE",
			validate_items=Mock(),
			items=[
				Doc(borrowing_item="ROW-1", item_code="ITEM", qty=1, batch_no="B1", serial_no="S2\nS1"),
				Doc(borrowing_item="ROW-1", item_code="ITEM", qty=1, batch_no="B2", serial_no="S3\nS4"),
			],
		)
		loan = Doc(name="LOAN-1", customer_loan_warehouse="LOAN-WH")
		rows = [
			Doc(
				item_code="ITEM",
				borrowing_item="ROW-1",
				qty=2,
				uom="Unit",
				stock_uom="Unit",
				conversion_factor=1,
				transfer_qty=2,
				s_warehouse="LOAN-WH",
				t_warehouse=None,
				expense_account="EXPENSE",
				batch_no=batch,
				serial_no=serials,
			)
			for batch, serials in (("B1", "S1\nS2"), ("B2", "S3\nS4"))
		]
		document = Doc(borrowing_source_name="ADJ-1", purpose="Material Issue", docstatus=1, items=rows)
		with (
			patch.object(frappe, "get_doc", return_value=source),
			patch.object(core, "conversion_factors", return_value={"ROW-1": 2}),
		):
			core.validate_manual_adjustment_issue(document, loan)
			source.validate_items.assert_called_once_with(loan)
			document.items = [rows[0], rows[0]]
			with self.assertRaises(frappe.ValidationError):
				core.validate_manual_adjustment_issue(document, loan)

	def test_stock_issue_cancellation_checks_invoice_under_the_adjustment_lock(self):
		document = Doc(
			name="STE-1",
			doctype="Stock Entry",
			is_borrowing_transaction=1,
			borrowing_source_doctype="Borrowing Adjustment",
			borrowing_source_name="ADJ-1",
			borrowing="LOAN-1",
			flags=frappe._dict(),
			check_permission=Mock(),
		)
		events = []
		frappe.db.sql.side_effect = lambda *args, **kwargs: events.append("lock")
		frappe.db.exists.side_effect = lambda *args, **kwargs: events.append("invoice") or True
		with (
			patch.object(core, "check_borrowing_source", return_value=True),
			self.assertRaises(frappe.ValidationError),
		):
			core.validate_generated_transaction_cancellation(document)
		self.assertEqual(events, ["lock", "invoice"])
		frappe.db.sql.assert_called_once_with(
			"select name from `tabBorrowing Adjustment` where name=%s for update", "ADJ-1"
		)
		frappe.db.exists.assert_called_once_with(
			"Sales Invoice",
			{
				"borrowing_source_doctype": "Borrowing Adjustment",
				"borrowing_source_name": "ADJ-1",
				"docstatus": ["<", 2],
			},
		)
		document.check_permission.assert_called_once_with("cancel")

	def test_consumption_mapper_and_validator_use_the_same_non_unit_stock_factor(self):
		from erpnext.borrowing.doctype.borrowing import borrowing_set

		loan = Doc(
			name="LOAN-1",
			company="CD",
			borrower="CUSTOMER",
			borrower_type="Customer",
			loan_type="Stock",
			material_request="MR-1",
			recipient_address=None,
			recipient_contact=None,
			items=[Doc(name="ROW-1", item_code="ITEM")],
		)
		source = Doc(
			name="ADJ-1",
			doctype="Borrowing Adjustment",
			docstatus=1,
			borrowing="LOAN-1",
			stock_entry="STE-1",
			reason="Consumed by the borrower",
			items=[Doc(borrowing_item="ROW-1", qty=2)],
		)
		invoice = Mock(flags=frappe._dict(), items=[], name="SI-1", is_return=0)
		invoice.append.side_effect = lambda table, values: invoice.items.append(Doc(**values))
		frappe.db.get_value.side_effect = lambda doctype, *args, **kwargs: {
			"Item": "Unit",
			"Stock Entry": 1,
			"Material Request": "Customer Borrow",
		}[doctype]
		frappe.db.exists.return_value = False
		with (
			patch.object(frappe, "new_doc", return_value=invoice),
			patch("erpnext.stock.get_item_details.get_item_details", return_value={}),
			patch.object(borrowing_set, "_", lambda message: message),
			patch.object(borrowing_set, "conversion_factors", return_value={"ROW-1": 12}),
			patch.object(core, "conversion_factors", return_value={"ROW-1": 12}),
			patch.object(frappe, "get_doc", return_value=source),
		):
			borrowing_set._make_consumption_invoice(loan, {"ROW-1": 2}, source)
			self.assertEqual(invoice.items[0].qty, 24)
			self.assertEqual(invoice.items[0].uom, "Unit")
			core.validate_consumption_invoice(invoice, loan, source.name)
			invoice.items[0].qty = 2
			with self.assertRaises(frappe.ValidationError):
				core.validate_consumption_invoice(invoice, loan, source.name)
		invoice.insert.assert_not_called()

	def test_stock_issue_cancellation_proceeds_when_no_active_consumption_invoice_exists(self):
		document = Doc(
			name="STE-1",
			doctype="Stock Entry",
			is_borrowing_transaction=1,
			borrowing_source_doctype="Borrowing Adjustment",
			borrowing_source_name="ADJ-1",
			borrowing="LOAN-1",
			flags=frappe._dict(),
			check_permission=Mock(),
		)
		source = Doc(doctype="Borrowing Adjustment", borrowing="LOAN-1")
		loan = Doc(items=[], material_request="MR-1")
		frappe.db.exists.return_value = False
		frappe.db.table_exists.return_value = False
		frappe.db.has_column.return_value = False
		with (
			patch.object(core, "check_borrowing_source", return_value=True),
			patch.object(frappe, "get_doc", side_effect=[source, loan]),
		):
			core.validate_generated_transaction_cancellation(document)
		frappe.db.sql.assert_called_once_with(
			"select name from `tabBorrowing Adjustment` where name=%s for update", "ADJ-1"
		)
		self.assertTrue(core.is_trusted_custody_operation(document))
