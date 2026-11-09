"""Order-date payment terms use actual orders without moving legacy invoice dates. Offline, no site."""

import json
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import frappe
from frappe.utils import getdate

from erpnext.accounts.doctype.payment_terms_template.payment_terms_template import PaymentTermsTemplate
from erpnext.controllers import accounts_controller as controller

ORDER_BASE = "Day(s) after order date"
INVOICE_BASE = "Day(s) after invoice date"


class Document(frappe._dict):
	def append(self, field, row):
		self.setdefault(field, []).append(frappe._dict(row))


def term(**values):
	row = Document(
		payment_term="T/T Before Delivery",
		due_date_based_on=ORDER_BASE,
		credit_days=0,
		credit_months=0,
		invoice_portion=100,
		discount=0,
	)
	row.update(values)
	row.precision = lambda field: 2
	return row


def invoice(**values):
	doc = Document(
		doctype="Purchase Invoice",
		docstatus=0,
		items=[Document(purchase_order="PO-1")],
		payment_schedule=[term()],
	)
	doc.update(values)
	return doc


class TestOrderDatePaymentTerms(unittest.TestCase):
	def setUp(self):
		for patcher in (
			patch.object(controller, "_", side_effect=lambda text: text),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(frappe, "get_system_settings", return_value="Banker's Rounding"),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_order_date_is_separate_from_invoice_and_supplier_bill_date(self):
		row = term(credit_days=7)
		date = controller.get_due_date(row, "2026-12-01", "2026-11-30", order_date="2026-11-09")
		self.assertEqual(getdate(date), getdate("2026-11-16"))

	def test_order_date_is_required_for_final_calculation(self):
		with self.assertRaises(frappe.ValidationError):
			controller.get_due_date(term(), "2026-12-01")

	def test_unsaved_term_preview_waits_for_an_order(self):
		preview = controller.get_payment_term_details(term(), "2026-12-01", 100, 100)
		self.assertIsNone(preview.due_date)
		self.assertEqual(preview.payment_amount, 100)

	def test_invoice_preview_does_not_clamp_an_order_due_date_to_invoice_date(self):
		preview = controller.get_payment_term_details(term(), "2026-12-01", 100, 100, order_date="2026-11-09")
		self.assertEqual(getdate(preview.due_date), getdate("2026-11-09"))

	def test_existing_invoice_credit_dates_ignore_the_new_anchor(self):
		preview = controller.get_payment_term_details(
			term(due_date_based_on=INVOICE_BASE, credit_days=30),
			"2026-12-01",
			100,
			100,
			order_date="2026-11-09",
		)
		self.assertEqual(getdate(preview.due_date), getdate("2026-12-31"))

	def test_template_api_forwards_the_order_anchor(self):
		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "get_doc", return_value=Document(terms=[term()])),
		):
			rows = controller.get_payment_terms("T/T", "2026-12-01", 100, 100, order_date="2026-11-09")
		self.assertEqual(getdate(rows[0].due_date), getdate("2026-11-09"))

	def test_orders_and_quotes_have_different_real_or_provisional_anchors(self):
		for doctype in ("Quotation", "Supplier Quotation", "Sales Order", "Purchase Order"):
			with self.subTest(doctype=doctype):
				doc = Document(doctype=doctype, transaction_date="2026-11-09")
				self.assertEqual(controller.AccountsController.get_payment_order_date(doc), "2026-11-09")

	def test_invoice_orders_must_have_one_common_date_and_be_readable(self):
		doc = invoice(items=[Document(purchase_order="PO-1"), Document(purchase_order="PO-2")])
		orders = {
			name: Document(transaction_date="2026-11-09", check_permission=Mock())
			for name in ("PO-1", "PO-2")
		}
		with patch.object(frappe, "get_doc", side_effect=lambda dt, name: orders[name]):
			self.assertEqual(controller.AccountsController.get_payment_order_date(doc), "2026-11-09")
			orders["PO-2"].transaction_date = "2026-11-10"
			self.assertIsNone(controller.AccountsController.get_payment_order_date(doc))
		for order in orders.values():
			order.check_permission.assert_called_with("read")

	def test_missing_invoice_order_is_not_replaced_by_posting_date(self):
		doc = invoice(items=[Document(purchase_order="PO-1"), Document()])
		with patch.object(frappe, "get_doc") as get_doc:
			self.assertIsNone(controller.AccountsController.get_payment_order_date(doc))
		get_doc.assert_not_called()

	def test_credit_invoice_does_not_add_a_linked_order_read_requirement(self):
		doc = invoice(payment_schedule=[term(due_date_based_on=INVOICE_BASE)])
		with patch.object(frappe, "get_doc") as get_doc:
			self.assertIsNone(controller.AccountsController.get_payment_order_date(doc))
		get_doc.assert_not_called()

	def test_new_order_recalculates_the_estimated_quotation_date(self):
		row = term(due_date="2026-11-01", paid_amount=0)
		doc = Document(
			doctype="Purchase Order",
			docstatus=0,
			transaction_date="2026-11-09",
			grand_total=100,
			base_grand_total=100,
			payment_schedule=[row],
			party_account_currency="THB",
			conversion_rate=1,
		)
		doc.get_payment_order_date = lambda: controller.AccountsController.get_payment_order_date(doc)
		with patch.object(frappe, "db", Mock(get_value=Mock(return_value=0))):
			controller.AccountsController.set_payment_schedule(doc)
		self.assertEqual(getdate(row.due_date), getdate("2026-11-09"))

	def test_submitted_order_does_not_silently_rewrite_an_approved_date(self):
		row = term(due_date="2026-11-01", paid_amount=0)
		doc = Document(
			doctype="Purchase Order",
			docstatus=1,
			transaction_date="2026-11-09",
			grand_total=100,
			base_grand_total=100,
			payment_schedule=[row],
			party_account_currency="THB",
			conversion_rate=1,
		)
		doc.get_payment_order_date = lambda: controller.AccountsController.get_payment_order_date(doc)
		with (
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=0))),
			self.assertRaises(frappe.ValidationError),
		):
			controller.AccountsController.set_payment_schedule(doc)
		self.assertEqual(row.due_date, "2026-11-01")

	def test_invoice_fetch_keeps_the_source_order_anchor(self):
		row = term(due_date="2026-11-09", paid_amount=0, mode_of_payment=None, description="T/T")
		source = Document(transaction_date="2026-11-09", payment_terms_template="T/T", payment_schedule=[row])
		doc = invoice(posting_date="2026-12-01", conversion_rate=1)
		with (
			patch.object(frappe, "get_cached_doc", return_value=source),
			patch.object(frappe, "get_cached_value", return_value=1),
		):
			controller.AccountsController.fetch_payment_terms_from_order(
				doc, "PO-1", "Purchase Order", 100, 100, True
			)
		self.assertEqual(getdate(doc.payment_schedule[0].due_date), getdate("2026-11-09"))

	def test_schema_options_and_credit_days_support_the_order_basis(self):
		root = Path(controller.__file__).parents[1] / "accounts" / "doctype"
		for name in ("payment_term", "payment_terms_template_detail", "payment_schedule"):
			with self.subTest(doctype=name):
				meta = json.loads((root / name / f"{name}.json").read_text())
				fields = {field["fieldname"]: field for field in meta["fields"]}
				self.assertIn(ORDER_BASE, fields["due_date_based_on"]["options"].splitlines())
				self.assertEqual(
					fields["due_date_based_on"]["options"].split("\n")[0],
					"" if name == "payment_schedule" else INVOICE_BASE,
				)
				self.assertIn(ORDER_BASE, fields["credit_days"]["depends_on"])

	def test_invoice_validation_updates_header_after_reanchoring_the_schedule(self):
		for ignore_default in (False, True):
			with self.subTest(ignore_default=ignore_default):
				row = term(due_date="2026-11-09", paid_amount=0)
				row.validate_from_to_dates = Mock()
				doc = invoice(
					posting_date="2026-12-01",
					bill_date="2026-12-01",
					due_date="2026-11-09",
					payment_schedule=[row],
					base_grand_total=100,
					grand_total=100,
					party_account_currency="THB",
					conversion_rate=1,
					is_return=0,
					is_paid=0,
					base_write_off_amount=0,
					write_off_amount=0,
					ignore_default_payment_terms_template=ignore_default,
				)
				for name in (
					"set_payment_schedule",
					"get_payment_order_date",
					"get_order_details",
					"set_due_date",
					"validate_payment_schedule_dates",
					"validate_due_date",
					"get_frozen_payment_terms",
				):
					method = getattr(controller.AccountsController, name)
					doc[name] = lambda method=method: method(doc)
				doc.validate_advance_entries = Mock()
				doc.validate_payment_schedule_amount = Mock()
				source = Document(transaction_date="2026-11-10", check_permission=Mock())
				with (
					patch.object(frappe, "db", Mock(get_value=Mock(return_value=0))),
					patch.object(frappe, "get_single_value", return_value=0),
					patch.object(frappe, "get_doc", return_value=source),
					patch.object(frappe, "flags", frappe._dict()),
					patch("erpnext.accounts.party.validate_due_date") as validate,
				):
					controller.AccountsController.validate_invoice_documents_schedule(doc)
				self.assertEqual(getdate(row.due_date), getdate("2026-11-10"))
				self.assertEqual(getdate(doc.due_date), getdate("2026-11-10"))
				if not ignore_default:
					self.assertEqual(getdate(validate.call_args.kwargs["due_date"]), getdate("2026-11-10"))

	def test_invoice_from_approved_old_order_can_keep_its_frozen_invoice_basis(self):
		old_terms = [term(due_date_based_on=INVOICE_BASE)]
		doc = invoice(payment_terms_template="T/T", payment_schedule=[term(due_date_based_on=INVOICE_BASE)])
		doc.get_doc_before_save = lambda: None
		source = Document(
			docstatus=1, payment_terms_template="T/T", payment_schedule=old_terms, check_permission=Mock()
		)
		with (
			patch.object(frappe, "get_cached_doc", return_value=Document(terms=[term()])),
			patch.object(frappe, "get_doc", return_value=source),
		):
			self.assertIs(controller.AccountsController.get_frozen_payment_terms(doc), old_terms)
			# An edited draft cannot use the old order to waive its current master's deadline.
			doc.payment_schedule[0].credit_days = 60
			self.assertIsNone(controller.AccountsController.get_frozen_payment_terms(doc))
		source.check_permission.assert_called_with("read")

	def test_submitted_invoice_retains_only_its_exact_previous_contract(self):
		old_terms = [term(due_date_based_on=INVOICE_BASE)]
		previous = invoice(docstatus=1, payment_terms_template="T/T", payment_schedule=old_terms)
		doc = invoice(
			docstatus=1, payment_terms_template="T/T", payment_schedule=[term(due_date_based_on=INVOICE_BASE)]
		)
		doc.get_doc_before_save = lambda: previous
		with (
			patch.object(frappe, "get_cached_doc", return_value=Document(terms=[term()])),
			patch.object(frappe, "get_doc") as get_doc,
		):
			self.assertIs(controller.AccountsController.get_frozen_payment_terms(doc), old_terms)
			get_doc.assert_not_called()
			doc.payment_schedule[0].credit_days = 60
			get_doc.return_value = Document(docstatus=0, check_permission=Mock())
			self.assertIsNone(controller.AccountsController.get_frozen_payment_terms(doc))

	def test_auto_fetch_disabled_invoice_validates_the_proven_old_order_contract(self):
		doc = invoice(
			payment_terms_template="T/T",
			posting_date="2026-12-01",
			bill_date="2026-12-01",
			due_date="2026-12-01",
			payment_schedule=[term(due_date_based_on=INVOICE_BASE)],
		)
		doc.get_doc_before_save = lambda: None
		doc.get_frozen_payment_terms = lambda: controller.AccountsController.get_frozen_payment_terms(doc)
		source = Document(
			docstatus=1,
			payment_terms_template="T/T",
			check_permission=Mock(),
			payment_schedule=[term(due_date_based_on=INVOICE_BASE)],
		)
		master = Document(terms=[term()])
		with (
			patch.object(frappe, "get_cached_doc", return_value=master),
			patch.object(
				frappe,
				"get_doc",
				side_effect=lambda doctype, name: master if doctype == "Payment Terms Template" else source,
			),
			patch.object(frappe, "flags", frappe._dict()),
		):
			controller.AccountsController.validate_due_date(doc)
			doc.payment_schedule[0].credit_days = 60
			with self.assertRaises(frappe.ValidationError):
				controller.AccountsController.validate_due_date(doc)

	def test_order_based_template_enables_native_term_allocation(self):
		doc = Document(terms=[term(idx=1)], allocate_payment_based_on_payment_terms=0)
		doc.validate_invoice_portion = lambda: PaymentTermsTemplate.validate_invoice_portion(doc)
		doc.validate_terms = lambda: PaymentTermsTemplate.validate_terms(doc)
		PaymentTermsTemplate.validate(doc)
		self.assertEqual(doc.allocate_payment_based_on_payment_terms, 1)
		doc.terms[0].payment_term = None
		with self.assertRaises(frappe.ValidationError):
			PaymentTermsTemplate.validate(doc)

	def test_invoice_based_template_keeps_its_existing_allocation_choice(self):
		doc = Document(
			terms=[term(idx=1, due_date_based_on=INVOICE_BASE)], allocate_payment_based_on_payment_terms=0
		)
		doc.validate_invoice_portion = lambda: PaymentTermsTemplate.validate_invoice_portion(doc)
		doc.validate_terms = lambda: PaymentTermsTemplate.validate_terms(doc)
		PaymentTermsTemplate.validate(doc)
		self.assertEqual(doc.allocate_payment_based_on_payment_terms, 0)


if __name__ == "__main__":
	unittest.main()
