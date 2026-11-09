"""Allocated invoice schedules retain their contract and settle actual invoice allocations. No site."""

import unittest
from inspect import signature
from unittest.mock import MagicMock, Mock, patch

import frappe
from frappe.database.database import Database

from erpnext.accounts import utils as accounts_utils
from erpnext.accounts.doctype.journal_entry import journal_entry as journal_module
from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry
from erpnext.accounts.doctype.payment_entry import payment_entry as payment_module
from erpnext.accounts.doctype.payment_entry.payment_entry import (
	PaymentEntry,
	get_reference_as_per_payment_terms,
	get_split_invoice_rows,
)
from erpnext.accounts.report.accounts_receivable.accounts_receivable import ReceivablePayableReport
from erpnext.controllers.accounts_controller import AccountsController


class Row(frappe._dict):
	def db_set(self, values, **kwargs):
		self.update(values)

	def as_dict(self):
		return frappe._dict(self)

	def set(self, field, value):
		self[field] = value

	def precision(self, field):
		return 2


class Invoice(Row):
	def __getattr__(self, name):
		method = getattr(AccountsController, name, None)
		if callable(method):
			return method.__get__(self, Invoice)
		return super().__getattr__(name)


class TestAllocatedInvoiceSettlement(unittest.TestCase):
	def setUp(self):
		for patcher in (
			patch.object(frappe, "get_cached_value", return_value=1),
			patch.object(frappe, "get_system_settings", return_value="Banker's Rounding"),
			patch.object(frappe, "db", Mock()),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def invoice(self, doctype="Purchase Invoice", total=10700, rate=1):
		return Invoice(
			doctype=doctype,
			name="INV-1",
			docstatus=0,
			payment_terms_template="Half",
			grand_total=total,
			base_grand_total=total * rate,
			conversion_rate=rate,
			currency="USD" if rate != 1 else "THB",
			company_currency="THB",
			party_account_currency="THB",
			payment_schedule=[
				Row(
					name=f"ROW-{index}",
					payment_term=term,
					invoice_portion=50,
					payment_amount=total / 2,
					base_payment_amount=total * rate / 2,
					paid_amount=0,
					outstanding=total / 2,
					discounted_amount=0,
				)
				for index, term in enumerate(("Deposit", "Completion"))
			],
			advances=[
				Row(
					reference_type="Payment Entry",
					reference_name="PE-1",
					reference_row="REF-1",
					allocated_amount=5350,
				)
			],
		)

	def payment(self, amount=5350, term="Deposit", rate=1):
		return Row(
			docstatus=1,
			posting_date="2026-11-09",
			references=[Row(name="REF-1", payment_term=term)],
			get_allocated_amount_in_transaction_currency=lambda value, *args: value / rate,
		)

	def test_sales_and_purchase_half_advance_preserve_gross_contract(self):
		for doctype in ("Sales Invoice", "Purchase Invoice"):
			doc = self.invoice(doctype)
			with patch.object(frappe, "get_doc", return_value=self.payment()):
				doc.set_allocated_invoice_payment_schedule()
				self.assertEqual([row.payment_amount for row in doc.payment_schedule], [5350, 5350])
				self.assertEqual([row.paid_amount for row in doc.payment_schedule], [5350, 0])
				self.assertEqual([row.outstanding for row in doc.payment_schedule], [0, 5350])
				doc.set_allocated_invoice_payment_schedule()
				self.assertEqual(sum(row.paid_amount for row in doc.payment_schedule), 5350)

	def test_partial_invoice_caps_each_term_and_total(self):
		doc = self.invoice(total=5350)
		with patch.object(frappe, "get_doc", return_value=self.payment()):
			doc.set_allocated_invoice_payment_schedule()
		self.assertEqual([row.paid_amount for row in doc.payment_schedule], [2675, 2675])
		self.assertEqual(sum(row.outstanding for row in doc.payment_schedule), 0)

	def test_no_invoice_allocation_does_not_inherit_order_paid(self):
		doc = self.invoice()
		doc.advances = []
		doc.payment_schedule[0].paid_amount = 5350
		doc.set_allocated_invoice_payment_schedule()
		self.assertEqual([row.paid_amount for row in doc.payment_schedule], [0, 0])

	def test_unallocated_manual_advance_uses_fifo(self):
		doc = self.invoice()
		with patch.object(frappe, "get_doc", return_value=self.payment(term=None)):
			doc.set_allocated_invoice_payment_schedule()
		self.assertEqual([row.outstanding for row in doc.payment_schedule], [0, 5350])

	def test_foreign_invoice_settles_in_transaction_and_base_currencies(self):
		doc = self.invoice(total=200, rate=35)
		doc.advances[0].allocated_amount = 3500
		with patch.object(frappe, "get_doc", return_value=self.payment(rate=35)):
			doc.set_allocated_invoice_payment_schedule()
		self.assertEqual([row.outstanding for row in doc.payment_schedule], [0, 100])
		self.assertEqual([row.base_outstanding for row in doc.payment_schedule], [0, 3500])

	def test_reconcile_and_unreconcile_rebuild_once_from_current_references(self):
		doc = self.invoice()
		refs = [Row(parent="PE-1", allocated_amount=5350, payment_term="Deposit")]
		with (
			patch.object(frappe, "get_doc", return_value=self.payment()),
			patch.object(
				frappe.db,
				"get_values",
				side_effect=lambda dt, **kwargs: refs if dt == "Payment Entry Reference" else [],
			),
		):
			for _ in range(2):
				doc.set_allocated_invoice_payment_schedule(from_references=True)
				self.assertEqual(sum(row.outstanding for row in doc.payment_schedule), 5350)
			refs.clear()
			doc.set_allocated_invoice_payment_schedule(from_references=True)
			self.assertEqual(sum(row.outstanding for row in doc.payment_schedule), 10700)

	def test_cancelled_payment_is_not_counted(self):
		doc = self.invoice()
		payment = self.payment()
		payment.docstatus = 2
		with (
			patch.object(frappe, "get_doc", return_value=payment),
			patch.object(
				frappe.db,
				"get_values",
				return_value=[],
			),
		):
			doc.set_allocated_invoice_payment_schedule(from_references=True)
		self.assertEqual(sum(row.base_outstanding for row in doc.payment_schedule), 10700)

	def test_discount_keeps_paid_discount_outstanding_identity(self):
		doc = self.invoice()
		doc.payment_schedule[0].update(discount_date="2026-11-30", discount_type="Amount", discount=50)
		refs = [Row(parent="PE-1", allocated_amount=5350, payment_term="Deposit")]
		with (
			patch.object(frappe, "get_doc", return_value=self.payment()),
			patch.object(
				frappe.db,
				"get_values",
				side_effect=lambda dt, **kwargs: refs if dt == "Payment Entry Reference" else [],
			),
		):
			doc.set_allocated_invoice_payment_schedule(from_references=True)
		row = doc.payment_schedule[0]
		self.assertEqual((row.paid_amount, row.discounted_amount, row.outstanding), (5300, 50, 0))
		self.assertEqual(row.payment_amount, row.paid_amount + row.discounted_amount + row.outstanding)

	def test_reference_getter_offers_only_positive_outstanding_after_discount(self):
		doc = self.invoice()
		doc.payment_schedule[0].update(paid_amount=5300, discounted_amount=50, outstanding=0)
		refs = get_reference_as_per_payment_terms(
			doc.payment_schedule, doc.doctype, doc.name, doc, 10700, 5350, "THB"
		)
		self.assertEqual(
			[(ref["payment_term"], ref["allocated_amount"]) for ref in refs], [("Completion", 5350)]
		)

	def test_reconciled_partial_invoice_references_split_and_retain_source_term(self):
		doc = self.invoice(total=5350)

		class Payment(Row):
			def append(self, field, values=None):
				row = Row(values or {})
				self[field].append(row)
				return row

		payment = Payment(
			name="PE-1",
			company="Test",
			posting_date="2026-11-09",
			flags=Row(),
			references=[
				Row(
					name="REF-1",
					reference_doctype="Purchase Order",
					reference_name="PO-1",
					payment_term="Deposit",
					total_amount=10700,
					payment_term_discounted_amount=107,
					payment_term_discount_recorded=1,
					allocated_amount=5350,
				)
			],
			get_exchange_rate=lambda: 1,
			get_allocated_amount_in_transaction_currency=lambda value, *args: value,
		)
		for method in (
			"clear_unallocated_reference_document_rows",
			"setup_party_account_field",
			"set_missing_values",
			"set_missing_ref_details",
			"set_amounts",
			"make_exchange_gain_loss_journal",
		):
			payment[method] = Mock()
		detail = Row(
			against_voucher_type=doc.doctype,
			against_voucher=doc.name,
			grand_total=5350,
			outstanding_amount=5350,
			allocated_amount=5350,
			voucher_detail_no="REF-1",
		)
		with (
			patch.object(frappe, "get_doc", return_value=doc),
			patch.object(frappe.db, "get_values", return_value=[]),
			patch.object(accounts_utils, "get_standard_dimension_values", return_value={}),
			patch.object(
				accounts_utils, "get_advance_payment_doctypes", return_value=["Purchase Order", "Sales Order"]
			),
		):
			accounts_utils.update_reference_in_payment_entry(detail, payment, do_not_save=True)
		refs = payment.references[1:]
		self.assertEqual(
			[(row.payment_term, row.allocated_amount) for row in refs],
			[("Deposit", 2675), ("Completion", 2675)],
		)
		self.assertEqual([row.advance_payment_term for row in refs], ["Deposit", "Deposit"])
		self.assertEqual(payment.references[0].allocated_amount, 0)
		self.assertEqual([row.advance_payment_term_discounted_amount for row in refs], [53.5, 53.5])
		self.assertEqual([row.advance_payment_term_total_amount for row in refs], [10700, 10700])
		self.assertEqual(payment.references[0].payment_term_discounted_amount, 0)
		self.assertIs(payment.flags.reference_provenance_token, payment_module._REFERENCE_PROVENANCE_TOKEN)
		payment.get_doc_before_save = lambda: Row(docstatus=1, references=[])
		PaymentEntry.validate_reference_provenance(payment)
		self.assertIsNone(payment.flags.reference_provenance_token)
		self.assertEqual([row.advance_payment_term_discounted_amount for row in refs], [53.5, 53.5])

	def test_split_getter_and_direct_getter_agree_even_for_one_cent(self):
		doc = self.invoice()
		doc.payment_schedule[0].outstanding = 0
		doc.payment_schedule[1].outstanding = 0.01
		for term in doc.payment_schedule:
			term.parent = doc.name
		with (
			patch.object(frappe, "get_all", return_value=doc.payment_schedule),
			patch.object(frappe, "get_meta"),
			patch.object(payment_module, "get_field_precision", return_value=2),
		):
			frappe.db.get_value.return_value = 1
			rows = get_split_invoice_rows(
				Row(voucher_no=doc.name, invoice_amount=10700), "Half", {doc.name: doc}
			)
		self.assertEqual(
			[(row.payment_term, row.payment_term_outstanding) for row in rows], [("Completion", 0.01)]
		)

	def test_cancelling_transferred_advance_reverses_original_order_term_in_base_currency(self):
		ref = Row(
			reference_doctype="Purchase Invoice",
			reference_name="INV-1",
			allocated_amount=3500,
			payment_term="Completion",
			advance_payment_term="Deposit",
			advance_voucher_type="Purchase Order",
			advance_voucher_no="PO-1",
		)
		payment = Row(
			references=[ref],
			update_allocated_invoice_payment_schedules=lambda: {("Purchase Invoice", "INV-1")},
			get_allocated_amount_in_transaction_currency=lambda amount, *args: amount / 35,
		)
		with (
			patch.object(frappe, "get_all", return_value=[Row(payment_term="Deposit", outstanding=0)]),
			patch.object(frappe, "get_meta"),
			patch.object(payment_module, "get_field_precision", return_value=2),
		):
			frappe.db.get_value.return_value = 35
			PaymentEntry.update_payment_schedule(payment, cancel=1)
		sql, values = frappe.db.sql.call_args.args
		self.assertIn("base_outstanding = `base_outstanding` + %s", sql)
		self.assertEqual(values, (100, 3500, 0, 100, 3500, "PO-1", "Deposit"))

	def test_allocated_term_report_totals_match_ledger_including_advance(self):
		for account_type in ("Receivable", "Payable"):
			report = object.__new__(ReceivablePayableReport)
			report.filters = Row(company="Test", account_type=account_type)
			row = Row(
				voucher_type="Sales Invoice" if account_type == "Receivable" else "Purchase Invoice",
				voucher_no="INV-1",
				invoiced=10700,
				paid=5350,
			)
			doc = self.invoice()
			terms = doc.payment_schedule
			for index, term in enumerate(terms):
				term.update(
					total_advance=5350,
					payment_terms_template="Half",
					grand_total=10700,
					rounded_total=0,
					write_off_amount=0,
					currency="THB",
					party_account_currency="THB",
					conversion_rate=1,
					paid_amount=5350 if index == 0 else 0,
					base_paid_amount=5350 if index == 0 else 0,
					base_outstanding=0 if index == 0 else 5350,
				)
			with (
				patch.object(frappe, "get_value", return_value="THB"),
				patch(
					"erpnext.accounts.report.accounts_receivable.accounts_receivable.get_currency_precision",
					return_value=2,
				),
			):
				frappe.db.sql.return_value = terms
				report.get_payment_terms(row)
			self.assertEqual(sum(term.outstanding for term in row.payment_terms), 5350)
			self.assertEqual(row.paid, 0)

	def test_unreconcile_restores_all_order_references_without_clearing_their_allocations(self):
		row = Row(
			name="REF-1",
			parent="PE-1",
			payment_term="Deposit",
			total_amount=5350,
			advance_voucher_type="Purchase Order",
			advance_voucher_no="PO-1",
			advance_payment_term="Deposit",
			advance_payment_term_total_amount=10700,
			advance_payment_term_discount_recorded=1,
			advance_payment_term_discounted_amount=0,
		)
		per, pay = MagicMock(), MagicMock()
		query, update = MagicMock(), MagicMock()
		query.select.return_value = query
		query.where.return_value = query
		query.run.return_value = [row]
		update.set.return_value = update
		update.where.return_value = update
		builder = Mock()
		builder.DocType.side_effect = [per, pay]
		builder.from_.return_value = query
		builder.update.return_value = update
		payment = Mock(
			references=[row],
			total_allocated_amount=5350,
			base_total_allocated_amount=5350,
			unallocated_amount=0,
		)
		with (
			patch.object(accounts_utils, "qb", builder),
			patch.object(accounts_utils, "now", return_value="2026-11-09 10:00:00"),
			patch.object(frappe, "session", Row(user="accounting")),
			patch.object(frappe, "get_doc", return_value=payment),
			patch.object(frappe, "msgprint"),
			patch(
				"erpnext.accounts.doctype.payment_request.payment_request.update_payment_requests_as_per_pe_references"
			),
		):
			accounts_utils.remove_ref_doc_link_from_pe("Purchase Invoice", "INV-1", "PE-1")
		builder.update.assert_called_once_with(pay)
		values = frappe.db.set_value.call_args.args[2]
		self.assertEqual((values["reference_doctype"], values["reference_name"]), ("Purchase Order", "PO-1"))
		self.assertEqual(values["total_amount"], 10700)
		self.assertNotIn("allocated_amount", values)
		payment.make_advance_gl_entries.assert_called_once_with(row, cancel=1)

	def test_current_settlement_reads_lock_invoice_before_references_and_payment(self):
		doc = self.invoice()
		events = []
		frappe.db.get_value.side_effect = lambda *args, **kwargs: events.append(("invoice", kwargs))

		def current_rows(doctype, **kwargs):
			signature(Database.get_values).bind(frappe.db, doctype, **kwargs)
			events.append((doctype, kwargs))
			return (
				[Row(parent="PE-1", allocated_amount=5350, payment_term="Deposit")]
				if doctype == "Payment Entry Reference"
				else []
			)

		def current_payment(*args, **kwargs):
			events.append(("payment", kwargs))
			return self.payment()

		frappe.db.get_values.side_effect = current_rows
		with patch.object(frappe, "get_doc", side_effect=current_payment):
			doc.set_allocated_invoice_payment_schedule(from_references=True)
		self.assertEqual(events[0][0], "invoice")
		self.assertTrue(all(options["for_update"] for _, options in events))

	def test_invoice_lock_precedes_native_payment_parent_timestamp_lock(self):
		events = []
		doc = AccountsController.__new__(AccountsController)
		doc.doctype = "Payment Entry"
		doc.lock_allocated_invoice_payment_schedules = lambda: events.append("invoice")
		with patch.object(
			AccountsController.__mro__[1], "check_if_latest", side_effect=lambda: events.append("native")
		):
			doc.check_if_latest()
		self.assertEqual(events, ["invoice", "native"])

	def test_concurrent_overallocation_cannot_be_hidden_by_bounding(self):
		doc = self.invoice()
		frappe.db.get_values.side_effect = lambda doctype, **kwargs: (
			[
				Row(parent="PE-1", allocated_amount=6000, payment_term="Deposit"),
				Row(parent="PE-2", allocated_amount=6000, payment_term="Completion"),
			]
			if doctype == "Payment Entry Reference"
			else []
		)
		with (
			patch.object(frappe, "get_doc", return_value=self.payment()),
			patch.object(frappe, "throw", side_effect=ValueError),
		):
			with self.assertRaises(ValueError):
				doc.set_allocated_invoice_payment_schedule(from_references=True)

	def test_foreign_base_contract_closes_without_rounding_residue(self):
		doc = self.invoice(total=100.02, rate=1.333)
		doc.party_account_currency = "USD"
		doc.payment_schedule[0].base_payment_amount = 66.67
		doc.payment_schedule[1].base_payment_amount = 66.66
		doc.advances[0].allocated_amount = 100.02
		with patch.object(frappe, "get_doc", return_value=self.payment()):
			doc.set_allocated_invoice_payment_schedule()
		self.assertEqual([row.base_paid_amount for row in doc.payment_schedule], [66.67, 66.66])
		self.assertEqual([row.base_outstanding for row in doc.payment_schedule], [0, 0])

	def test_full_foreign_discount_closes_base_contract_and_matches_term_report(self):
		doc = self.invoice(total=100, rate=1.0105)
		doc.party_account_currency = "USD"
		doc.payment_schedule = [
			Row(
				name="ROW-1",
				payment_term="Deposit",
				payment_amount=100,
				base_payment_amount=101.05,
				paid_amount=0,
				discounted_amount=0,
				outstanding=100,
				discount_date="2026-11-30",
				discount_type="Amount",
				discount=10,
			)
		]
		frappe.db.get_values.side_effect = lambda doctype, **kwargs: (
			[Row(parent="PE-1", allocated_amount=100, payment_term="Deposit")]
			if doctype == "Payment Entry Reference"
			else []
		)
		with patch.object(frappe, "get_doc", return_value=self.payment()):
			doc.set_allocated_invoice_payment_schedule(from_references=True)
		term = doc.payment_schedule[0]
		self.assertEqual((term.paid_amount, term.discounted_amount, term.outstanding), (90, 10, 0))
		self.assertEqual((term.base_paid_amount, term.base_outstanding), (90.94, 0))
		base_discount = round(term.base_payment_amount - term.base_paid_amount - term.base_outstanding, 2)
		self.assertEqual(base_discount, 10.11)
		self.assertAlmostEqual(
			term.base_payment_amount, term.base_paid_amount + base_discount + term.base_outstanding
		)
		term.update(allocated_terms=True, currency="USD", party_account_currency="USD")
		for in_party_currency, expected_paid in ((False, 101.05), (True, 100)):
			report = object.__new__(ReceivablePayableReport)
			report.filters = Row(in_party_currency=in_party_currency)
			row = Row(invoiced=expected_paid, paid=expected_paid, payment_terms=[])
			with patch(
				"erpnext.accounts.report.accounts_receivable.accounts_receivable.get_currency_precision",
				return_value=2,
			):
				report.append_payment_term(row, term, Row(), "THB")
			self.assertEqual(row.payment_terms[0].outstanding, 0)
			self.assertEqual(row.payment_terms[0].paid, expected_paid)
			self.assertEqual(row.paid, 0)

	def test_order_discount_snapshot_is_recorded_and_reversed_after_partial_transfer(self):
		ref = Row(
			reference_doctype="Purchase Order",
			reference_name="PO-1",
			allocated_amount=5350,
			payment_term="Deposit",
			total_amount=10700,
		)
		payment = Row(
			references=[ref],
			update_allocated_invoice_payment_schedules=lambda: set(),
			get_allocated_amount_in_transaction_currency=lambda amount, *args: amount,
		)
		with (
			patch.object(
				frappe,
				"get_all",
				return_value=[
					Row(payment_term="Deposit", outstanding=5350, discount_type="Percentage", discount=1)
				],
			),
			patch.object(frappe, "get_meta"),
			patch.object(payment_module, "get_field_precision", return_value=2),
		):
			frappe.db.get_value.return_value = 1
			PaymentEntry.update_payment_schedule(payment)
		self.assertEqual(ref.payment_term_discounted_amount, 107)
		self.assertEqual(ref.payment_term_discount_recorded, 1)
		for remaining_order in (False, True):
			invoice_refs = [
				Row(
					reference_doctype="Purchase Invoice",
					reference_name="INV-1",
					payment_term=term,
					allocated_amount=2675,
					total_amount=5350,
					advance_voucher_type="Purchase Order",
					advance_voucher_no="PO-1",
					advance_payment_term="Deposit",
					advance_payment_term_total_amount=10700,
					advance_payment_term_discounted_amount=53.5,
					advance_payment_term_discount_recorded=1,
				)
				for term in ("Deposit", "Completion")
			]
			if remaining_order:
				ref.allocated_amount = 2675
				ref.payment_term_discounted_amount = 53.5
				invoice_refs[1] = ref
			payment.references = invoice_refs
			payment.update_allocated_invoice_payment_schedules = lambda: {("Purchase Invoice", "INV-1")}
			with (
				patch.object(
					frappe,
					"get_all",
					return_value=[
						Row(payment_term="Deposit", outstanding=0, discount_type="Percentage", discount=99)
					],
				),
				patch.object(frappe, "get_meta"),
				patch.object(payment_module, "get_field_precision", return_value=2),
			):
				PaymentEntry.update_payment_schedule(payment, cancel=1)
			self.assertEqual(
				frappe.db.sql.call_args.args[1], (5243, 5243, 107, 5350, 5350, "PO-1", "Deposit")
			)

	def test_journal_entry_submit_update_and_cancel_refresh_old_and_new_invoices(self):
		for event in ("on_submit", "on_update_after_submit", "on_cancel"):
			journal = JournalEntry.__new__(JournalEntry)
			journal.doctype = "Journal Entry"
			journal.name = "JE-1"
			journal.accounts = [Row(reference_type="Purchase Invoice", reference_name="INV-2")]
			journal._doc_before_save = Row(
				accounts=[Row(reference_type="Purchase Invoice", reference_name="INV-1")]
			)
			journal.flags = Row(ignore_reposting_on_reconciliation=True)
			for method in (
				"validate_cheque_info",
				"make_gl_entries",
				"check_credit_limit",
				"update_asset_value",
				"update_inter_company_jv",
				"update_invoice_discounting",
				"unlink_advance_entry_reference",
				"unlink_asset_reference",
				"unlink_inter_company_jv",
				"unlink_asset_adjustment_entry",
			):
				setattr(journal, method, Mock())
			invoice = Row(set_allocated_invoice_payment_schedule=Mock(return_value=True))
			with (
				patch.object(frappe, "get_doc", return_value=invoice) as get_doc,
				patch.object(AccountsController, "on_cancel"),
				patch.object(journal_module, "JournalTaxWithholding"),
			):
				getattr(journal, event)()
			self.assertEqual(
				[call.args for call in get_doc.call_args_list],
				[("Purchase Invoice", "INV-1"), ("Purchase Invoice", "INV-2")],
			)
			self.assertTrue(all(call.kwargs["for_update"] for call in get_doc.call_args_list))

	def test_cancelled_journal_advance_restores_full_invoice_outstanding(self):
		doc = self.invoice()
		doc.advances = [Row(reference_type="Journal Entry", allocated_amount=5350)]
		doc.set_allocated_invoice_payment_schedule()
		self.assertEqual(sum(row.paid_amount for row in doc.payment_schedule), 5350)
		journal_rows = [Row(debit_in_account_currency=5350, credit_in_account_currency=0)]
		frappe.db.get_values.side_effect = lambda doctype, **kwargs: (
			journal_rows if doctype == "Journal Entry Account" else []
		)
		doc.set_allocated_invoice_payment_schedule(from_references=True)
		self.assertEqual(sum(row.outstanding for row in doc.payment_schedule), 5350)
		journal_rows.clear()
		doc.set_allocated_invoice_payment_schedule(from_references=True)
		self.assertEqual(sum(row.outstanding for row in doc.payment_schedule), 10700)

	def reference_provenance_payment(self, rows, previous=None):
		payment = Row(
			doctype="Payment Entry",
			docstatus=1,
			references=rows,
			flags=Row(),
			get_doc_before_save=lambda: previous,
		)
		payment.validate_reference_provenance = lambda: PaymentEntry.validate_reference_provenance(payment)
		return payment

	def forged_provenance_reference(self, **values):
		row = Row(
			name="REF-1",
			reference_doctype="Purchase Invoice",
			reference_name="INV-1",
			payment_term="Completion",
			allocated_amount=5350,
			total_amount=10700,
			advance_voucher_type="Purchase Order",
			advance_voucher_no="PO-UNRELATED",
			advance_payment_term="Deposit",
			advance_payment_term_total_amount=99999,
			advance_payment_term_discounted_amount=5000,
			advance_payment_term_discount_recorded=1,
			payment_term_discounted_amount=5000,
			payment_term_discount_recorded=1,
		)
		row.update(values)
		return row

	def test_initial_draft_and_submit_clear_every_client_supplied_provenance_field(self):
		for action in ("save", "submit"):
			row = self.forged_provenance_reference()
			payment = self.reference_provenance_payment([row], Row(docstatus=0, references=[Row(row)]))
			payment.docstatus = 0 if action == "save" else 1
			payment._action = action
			payment.flags.reference_provenance_token = True
			payment.validate_reference_provenance()
			for field, default in payment_module._REFERENCE_PROVENANCE_DEFAULTS.items():
				self.assertEqual(row.get(field), default, (action, field))
			self.assertEqual(
				(row.reference_doctype, row.reference_name, row.allocated_amount),
				("Purchase Invoice", "INV-1", 5350),
			)

	def test_submitted_cancel_and_update_restore_locked_stored_provenance_not_payload(self):
		stored = self.forged_provenance_reference(
			advance_voucher_no="PO-REAL",
			advance_payment_term_total_amount=10700,
			advance_payment_term_discounted_amount=107,
			payment_term_discounted_amount=0,
		)
		for method in (PaymentEntry.before_cancel, PaymentEntry.before_update_after_submit):
			row = self.forged_provenance_reference()
			payment = self.reference_provenance_payment([row], Row(docstatus=1, references=[stored]))
			method(payment)
			for field in payment_module._REFERENCE_PROVENANCE_DEFAULTS:
				self.assertEqual(row.get(field), stored.get(field), field)

	def test_forged_cancel_payload_cannot_reverse_an_unrelated_order(self):
		stored = Row(
			name="REF-1",
			reference_doctype="Purchase Invoice",
			reference_name="INV-1",
			payment_term="Completion",
			allocated_amount=5350,
			total_amount=10700,
		)
		row = self.forged_provenance_reference()
		payment = self.reference_provenance_payment([row], Row(docstatus=1, references=[stored]))
		payment.flags.reference_provenance_token = True
		payment.update_allocated_invoice_payment_schedules = lambda: {("Purchase Invoice", "INV-1")}
		PaymentEntry.before_cancel(payment)
		PaymentEntry.update_payment_schedule(payment, cancel=1)
		self.assertIsNone(row.advance_voucher_type)
		self.assertIsNone(row.advance_voucher_no)
		frappe.db.sql.assert_not_called()

	def test_submitted_provenance_cannot_be_rebound_deleted_or_duplicated(self):
		stored = self.forged_provenance_reference(advance_voucher_no="PO-REAL")
		for field in (
			"reference_doctype",
			"reference_name",
			"payment_term",
			"allocated_amount",
			"total_amount",
			"missing",
			"duplicate",
		):
			row = Row(stored)
			rows = [row]
			if field == "missing":
				rows = []
			elif field == "duplicate":
				rows.append(Row(row))
			else:
				row[field] = "Changed" if field not in ("allocated_amount", "total_amount") else 1
			payment = self.reference_provenance_payment(rows, Row(docstatus=1, references=[stored]))
			with patch.object(frappe, "throw", side_effect=frappe.ValidationError):
				with self.assertRaises(frappe.ValidationError, msg=field):
					PaymentEntry.before_cancel(payment)

	def test_stored_legacy_attribution_is_preserved_and_new_submitted_rows_cannot_mint_it(self):
		stored = Row(
			name="REF-1",
			reference_doctype="Purchase Invoice",
			reference_name="INV-1",
			payment_term="Completion",
			allocated_amount=5350,
			total_amount=10700,
			advance_voucher_type="Purchase Order",
			advance_voucher_no="PO-LEGACY",
		)
		row = Row(stored)
		new_row = self.forged_provenance_reference(name="NEW-ROW")
		payment = self.reference_provenance_payment([row, new_row], Row(docstatus=1, references=[stored]))
		PaymentEntry.before_update_after_submit(payment)
		self.assertEqual((row.advance_voucher_type, row.advance_voucher_no), ("Purchase Order", "PO-LEGACY"))
		for field, default in payment_module._REFERENCE_PROVENANCE_DEFAULTS.items():
			self.assertEqual(new_row.get(field), default, field)

	def test_reconciliation_capability_is_one_use_and_not_a_client_flag(self):
		stored = self.forged_provenance_reference(advance_voucher_no="PO-REAL")
		row = Row(stored, allocated_amount=2675, advance_payment_term_discounted_amount=53.5)
		payment = self.reference_provenance_payment([row], Row(docstatus=1, references=[stored]))
		payment.flags.reference_provenance_token = payment_module._REFERENCE_PROVENANCE_TOKEN
		PaymentEntry.before_update_after_submit(payment)
		self.assertEqual(row.advance_payment_term_discounted_amount, 53.5)
		self.assertIsNone(payment.flags.reference_provenance_token)
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError):
			with self.assertRaises(frappe.ValidationError):
				PaymentEntry.before_cancel(payment)

	def test_normal_validate_checks_provenance_before_other_payment_logic(self):
		payment = Row(
			validate_reference_provenance=Mock(side_effect=RuntimeError("guard")),
			setup_party_account_field=Mock(),
		)
		with self.assertRaisesRegex(RuntimeError, "guard"):
			PaymentEntry.validate(payment)
		payment.setup_party_account_field.assert_not_called()

	def generated_fx_cancellation(self):
		source = Row(
			doctype="Payment Entry",
			name="PE-1",
			company="Test",
			docstatus=2,
			_action="cancel",
			check_permission=Mock(),
		)
		journal = Row(
			doctype="Journal Entry",
			name="FX-1",
			company="Test",
			docstatus=1,
			voucher_type="Exchange Gain Or Loss",
			is_system_generated=1,
			flags=Row(ignore_permissions=False),
			accounts=[
				Row(reference_type="Payment Entry", reference_name="PE-1"),
				Row(reference_type="Purchase Invoice", reference_name="INV-1"),
			],
		)

		def cancel():
			if not journal.flags.ignore_permissions:
				raise frappe.PermissionError
			journal.docstatus = 2

		journal.cancel = Mock(side_effect=cancel)
		return source, journal

	def run_generated_fx_cancellation(self, parent, source, journal, **kwargs):
		with (
			patch.object(
				accounts_utils, "get_linked_exchange_gain_loss_journal", return_value=[journal.name]
			),
			patch.object(
				frappe,
				"get_doc",
				side_effect=lambda dt, name, **options: journal if dt == "Journal Entry" else source,
			) as get_doc,
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
		):
			accounts_utils.cancel_exchange_gain_loss_journal(parent, **kwargs)
		return get_doc

	def test_authorized_source_cancellation_cancels_only_linked_generated_fx_journal(self):
		source, journal = self.generated_fx_cancellation()
		get_doc = self.run_generated_fx_cancellation(
			source, source, journal, _source_cancellation_token=accounts_utils._SOURCE_CANCELLATION_TOKEN
		)
		source.check_permission.assert_called_once_with("cancel")
		journal.cancel.assert_called_once_with()
		self.assertEqual(journal.docstatus, 2)
		self.assertFalse(journal.flags.ignore_permissions)
		self.assertTrue(all(call.kwargs["for_update"] for call in get_doc.call_args_list))

	def test_client_flags_and_forged_cancellation_token_cannot_delegate_fx_permissions(self):
		for token in (None, True, "cancel", object()):
			source, journal = self.generated_fx_cancellation()
			source.flags = Row(ignore_permissions=True, system_generation_token=True)
			with self.assertRaises(frappe.PermissionError):
				self.run_generated_fx_cancellation(source, source, journal, _source_cancellation_token=token)
			source.check_permission.assert_not_called()
			self.assertEqual(journal.docstatus, 1)
			self.assertFalse(journal.flags.ignore_permissions)

	def test_generated_fx_cancellation_requires_parent_cancel_permission_and_stored_cancelled_state(self):
		for failure in ("permission", "stored_state", "caller_state"):
			source, journal = self.generated_fx_cancellation()
			parent = Row(source)
			if failure == "permission":
				source.check_permission.side_effect = frappe.PermissionError
			elif failure == "stored_state":
				source.docstatus = 1
			else:
				parent._action = "submit"
			with self.assertRaises((frappe.PermissionError, frappe.ValidationError)):
				self.run_generated_fx_cancellation(
					parent,
					source,
					journal,
					_source_cancellation_token=accounts_utils._SOURCE_CANCELLATION_TOKEN,
				)
			journal.cancel.assert_not_called()
			self.assertFalse(journal.flags.ignore_permissions)

	def test_generated_fx_cancellation_rejects_manual_unrelated_or_cross_company_journals(self):
		for failure in ("manual", "type", "company", "status", "link", "rows"):
			source, journal = self.generated_fx_cancellation()
			if failure == "manual":
				journal.is_system_generated = 0
			elif failure == "type":
				journal.voucher_type = "Bank Entry"
			elif failure == "company":
				journal.company = "Other"
			elif failure == "status":
				journal.docstatus = 0
			elif failure == "link":
				journal.accounts[0].reference_name = "PE-OTHER"
			else:
				journal.accounts.append(Row(reference_type="Purchase Invoice", reference_name="INV-OTHER"))
			with self.assertRaises(frappe.ValidationError):
				self.run_generated_fx_cancellation(
					source,
					source,
					journal,
					_source_cancellation_token=accounts_utils._SOURCE_CANCELLATION_TOKEN,
				)
			journal.cancel.assert_not_called()
			self.assertFalse(journal.flags.ignore_permissions)

	def test_reconciliation_retains_normal_journal_permissions_and_counterpart_filter(self):
		source, journal = self.generated_fx_cancellation()
		source.docstatus = 1
		source._action = "update_after_submit"
		with self.assertRaises(frappe.PermissionError):
			self.run_generated_fx_cancellation(
				source, source, journal, referenced_dt="Purchase Invoice", referenced_dn="INV-1"
			)
		journal.cancel.reset_mock()
		self.run_generated_fx_cancellation(
			source, source, journal, referenced_dt="Purchase Invoice", referenced_dn="INV-OTHER"
		)
		journal.cancel.assert_not_called()
		self.assertFalse(journal.flags.ignore_permissions)

	def test_generated_fx_native_cancel_validation_failure_restores_permission_flag(self):
		source, journal = self.generated_fx_cancellation()
		journal.cancel.side_effect = frappe.ValidationError
		with self.assertRaises(frappe.ValidationError):
			self.run_generated_fx_cancellation(
				source, source, journal, _source_cancellation_token=accounts_utils._SOURCE_CANCELLATION_TOKEN
			)
		self.assertFalse(journal.flags.ignore_permissions)
		self.assertEqual(journal.docstatus, 1)

	def test_initial_submit_preserves_six_decimal_draft_schedule_residual(self):
		class PreciseInvoice(Invoice):
			def precision(self, field):
				return 6

		class PreciseRow(Row):
			def precision(self, field):
				return 6

		doc = PreciseInvoice(self.invoice(total=100.020001, rate=2.1))
		doc.base_grand_total = 210.042002
		doc.write_off_amount = doc.base_write_off_amount = 0
		doc.payment_schedule = [PreciseRow(row) for row in doc.payment_schedule]
		with patch.object(frappe, "get_single_value", return_value=0):
			doc.set_payment_schedule()
			draft_amounts = [(row.payment_amount, row.base_payment_amount) for row in doc.payment_schedule]
			doc.docstatus = 1
			doc._action = "submit"
			doc.set_payment_schedule()
		self.assertEqual(draft_amounts, [(50.01, 105.021001), (50.010001, 105.021001)])
		self.assertEqual(
			[(row.payment_amount, row.base_payment_amount) for row in doc.payment_schedule], draft_amounts
		)
		doc.validate_payment_schedule_amount()

	def test_submitted_net_contract_stays_legacy_before_schedule_rebuild(self):
		doc = self.invoice()
		doc.docstatus = 1
		for row in doc.payment_schedule:
			row.payment_amount = 2675
		self.assertFalse(doc.has_allocated_invoice_payment_terms())
		self.assertFalse(doc.set_allocated_invoice_payment_schedule(from_references=True))

	def test_allocated_schedule_validation_rejects_one_cent_mismatch(self):
		doc = self.invoice()
		doc.payment_schedule[0].payment_amount -= 0.01
		with patch.object(frappe, "throw", side_effect=ValueError):
			with self.assertRaises(ValueError):
				doc.validate_payment_schedule_amount()

	def test_legacy_template_and_posted_net_schedule_are_not_reinterpreted(self):
		doc = self.invoice()
		with patch.object(frappe, "get_cached_value", return_value=0):
			self.assertFalse(doc.set_allocated_invoice_payment_schedule())
		doc.payment_schedule[0].payment_amount = 2675
		self.assertFalse(doc.set_allocated_invoice_payment_schedule(from_references=True))


if __name__ == "__main__":
	unittest.main()
