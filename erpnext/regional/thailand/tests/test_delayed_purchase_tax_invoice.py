"""Input-VAT delay survives the voucher popup and changes the report month, not the supplier invoice date."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.regional.doctype.purchase_tax_invoice.purchase_tax_invoice import PurchaseTaxInvoice
from erpnext.regional.thailand.custom import custom_api


class TaxInvoice(SimpleNamespace):
	def db_set(self, field, value):
		setattr(self, field, value)

	def save(self):
		PurchaseTaxInvoice.compute_report_date(self)


class TestDelayedPurchaseTaxInvoice(unittest.TestCase):
	def test_voucher_delay_sets_report_date_without_changing_tax_invoice_date(self):
		for date, delay, report_date in (
			("2026-11-17", "0", "2026-11-17"),
			("2026-11-17", "1", "2026-12-17"),
			("2026-12-31", "2", "2027-02-28"),
		):
			for voucher_type in ("Purchase Invoice", "Journal Entry"):
				with self.subTest(date=date, delay=delay, voucher_type=voucher_type):
					voucher = frappe._dict(
						tax_invoice_number="SUP-123",
						tax_invoice_date=date,
						months_delayed=delay,
						billing_address="ADDR",
						company_tax_address="ADDR",
						save=Mock(),
					)
					invoice = TaxInvoice(voucher_type=voucher_type, months_delayed=0)
					custom_api.update_voucher_tinv("Purchase Tax Invoice", voucher, invoice)
					self.assertEqual(invoice.months_delayed, int(delay))
					self.assertEqual(str(invoice.report_date), report_date)
					self.assertEqual(invoice.date, date)
					self.assertEqual(invoice.number, "SUP-123")

	def test_changing_only_delay_regenerates_journal_tax_details(self):
		old = frappe._dict(accounts=[frappe._dict(account="VAT", months_delayed="0")])
		doc = frappe._dict(
			docstatus=0,
			accounts=[frappe._dict(account="VAT", months_delayed="1")],
			get_doc_before_save=lambda: old,
		)
		self.assertTrue(custom_api.is_tax_reset(doc, ["VAT"]))
		doc.accounts[0].months_delayed = "0"
		self.assertFalse(custom_api.is_tax_reset(doc, ["VAT"]))

	def test_regenerated_journal_tax_detail_keeps_the_delay(self):
		line = frappe._dict(
			account="VAT",
			debit=70,
			credit=0,
			tax_base_amount=1000,
			supplier="SUP",
			customer=None,
			tax_invoice_number="SUP-123",
			tax_invoice_date="2026-11-17",
			months_delayed="1",
			save=Mock(),
		)
		doc = frappe._dict(
			name="JE-1",
			company="Company",
			company_tax_address="ADDR",
			docstatus=0,
			accounts=[line],
			tax_invoice_details=[],
			get_doc_before_save=lambda: None,
			reload=Mock(),
		)
		with (
			patch.object(
				custom_api,
				"get_thai_tax_settings",
				return_value=frappe._dict(purchase_tax_account="VAT", sales_tax_account="OUTPUT VAT"),
			),
			patch.object(custom_api, "get_field_precision", return_value=2),
			patch.object(frappe, "get_meta"),
			patch.object(frappe, "get_cached_value", return_value=7),
			patch.object(frappe, "get_doc") as get_doc,
		):
			custom_api.prepare_journal_entry_tax_invoice_detail(doc, "validate")
		values = next(call.args[0] for call in get_doc.call_args_list if isinstance(call.args[0], dict))
		self.assertEqual(values["months_delayed"], 1)
		self.assertEqual(values["tax_invoice_date"], "2026-11-17")

	def test_editing_submitted_tax_invoice_keeps_source_delay_in_sync(self):
		source = frappe._dict(meta=Mock(), save=Mock(), split_tax_invoice=0)
		source.meta.has_field.return_value = True
		invoice = TaxInvoice(
			voucher_type="Purchase Invoice",
			voucher_no="PI-1",
			date="2026-11-17",
			months_delayed="2",
			number="SUP-123",
			get_doc_before_save=Mock(return_value=True),
		)
		invoice.compute_report_date = lambda: PurchaseTaxInvoice.compute_report_date(invoice)
		with patch.object(frappe, "get_doc", return_value=source):
			PurchaseTaxInvoice.on_update_after_submit(invoice)
		self.assertEqual(source.months_delayed, "2")
		self.assertEqual(str(invoice.report_date), "2027-01-17")
		source.save.assert_called_once_with(ignore_permissions=True)

	def test_other_sources_still_save_without_delay_field(self):
		source = frappe._dict(meta=Mock(), save=Mock(), split_tax_invoice=0)
		source.meta.has_field.return_value = False
		invoice = TaxInvoice(
			voucher_type="Expense Claim",
			voucher_no="EXP-1",
			date="2026-11-17",
			months_delayed="0",
			number="SUP-123",
			get_doc_before_save=Mock(return_value=True),
			compute_report_date=Mock(),
		)
		with patch.object(frappe, "get_doc", return_value=source):
			PurchaseTaxInvoice.on_update_after_submit(invoice)
		source.save.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(source.tax_invoice_number, "SUP-123")

	def test_split_invoice_edits_only_selected_detail(self):
		source = frappe._dict(meta=Mock(), save=Mock(), split_tax_invoice=1)
		source.meta.has_field.return_value = True
		detail = frappe._dict(save=Mock())
		invoice = TaxInvoice(
			voucher_type="Purchase Invoice",
			voucher_no="PI-1",
			date="2026-11-17",
			months_delayed="2",
			number="SUP-123",
			party="SUP",
			splitted_tax_invoice="DETAIL-1",
			get_doc_before_save=Mock(return_value=True),
			compute_report_date=Mock(),
		)
		with patch.object(frappe, "get_doc", side_effect=[source, detail]):
			PurchaseTaxInvoice.on_update_after_submit(invoice)
		source.save.assert_not_called()
		detail.save.assert_called_once_with(ignore_permissions=True)
		self.assertEqual(detail.tax_invoice_number, "SUP-123")
