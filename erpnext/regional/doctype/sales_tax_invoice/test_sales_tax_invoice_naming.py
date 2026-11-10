"""Sales and Purchase Tax Invoice numbers (Siwat, 10 Nov 2026): ST6910001 (October 2569), PT6905001, ... the
Buddhist-era year, the month, a running number restarting every month per type; no company or branch part. The two modes of Thai Tax Settings that name a Sales Tax Invoice otherwise stay as
they were. Offline: Frappe's own naming (make_autoname) runs; the Series table is an in-memory counter."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
import frappe.model.naming

from erpnext.regional.doctype.purchase_tax_invoice.purchase_tax_invoice import PurchaseTaxInvoice
from erpnext.regional.doctype.sales_tax_invoice import sales_tax_invoice
from erpnext.regional.doctype.sales_tax_invoice.sales_tax_invoice import SalesTaxInvoice

CD, UB = "บริษัท คอนเนค ไดแอกโนสติกส์ จำกัด", "บริษัท ยูบีคอินเทลลิซีส จำกัด"


class TestTaxInvoiceNaming(unittest.TestCase):
	def setUp(self):
		self.series = {}
		self.settings = SimpleNamespace(
			use_doc_name_for_sales_taxinv=0,
			create_sales_taxinv_on_zero_tax=0,
			manual_keyin_sales_taxinv_on_zero_tax=0,
		)
		self.settings.get = lambda field: getattr(self.settings, field)
		for target in (
			patch.object(frappe.model.naming, "getseries", side_effect=self.next_number),
			patch.object(frappe, "get_hooks", return_value={}),
			# make_autoname reads the time zone (now_datetime) from System Settings
			patch.object(frappe, "get_system_settings", side_effect=lambda key: "Asia/Bangkok", create=True),
			patch.object(
				sales_tax_invoice, "get_thai_tax_settings", side_effect=lambda company: self.settings
			),
		):
			target.start()
			self.addCleanup(target.stop)

	def next_number(self, prefix, digits):
		self.series[prefix] = self.series.get(prefix, 0) + 1
		return str(self.series[prefix]).zfill(digits)

	def named(self, company, date, address="CD-HO", cls=SalesTaxInvoice, **fields):
		doc = cls.__new__(cls)
		doctype = "Sales Tax Invoice" if cls is SalesTaxInvoice else "Purchase Tax Invoice"
		doc.__dict__.update(
			doctype=doctype, name=None, company=company, date=date, gl_entry=None, company_tax_address=address
		)
		doc.__dict__.update(fields)
		doc.autoname()
		return doc.name

	def test_one_series_for_every_branch_and_company(self):
		self.assertEqual(self.named(CD, "2026-10-22"), "ST6910001")
		self.assertEqual(self.named(CD, "2026-10-22", "CD-B3"), "ST6910002")
		self.assertEqual(self.named(CD, "2026-10-22", None), "ST6910003")
		self.assertEqual(self.named(UB, "2026-10-22", "UB-HO"), "ST6910004")

	def test_the_buddhist_era_year(self):
		self.assertEqual(self.named(CD, "2026-12-31"), "ST6912001")
		self.assertEqual(self.named(CD, "2027-01-02"), "ST7001001")
		self.assertEqual(self.named(CD, "2057-01-02"), "ST0001001")  # 2600 BE

	def test_the_month_is_padded_and_restarts_the_number(self):
		self.assertEqual(self.named(CD, "2026-05-09"), "ST6905001")
		self.assertEqual(self.named(CD, "2026-05-31"), "ST6905002")
		self.assertEqual(self.named(CD, "2026-06-01"), "ST6906001")
		self.assertEqual(self.named(CD, "2026-05-31", "CD-B3"), "ST6905003")
		self.assertEqual(self.named(CD, "2026-05-02"), "ST6905004")

	def test_the_date_of_the_gl_entry_when_the_date_is_not_fetched_yet(self):
		with patch.object(
			sales_tax_invoice.frappe, "db", SimpleNamespace(get_value=lambda *args: "2025-12-31")
		):
			self.assertEqual(self.named(CD, None, gl_entry="GLE-1"), "ST6812001")

	def test_a_purchase_tax_invoice_month_is_our_posting_date_on_every_path(self):
		"""The normal path has no date yet at insert, a split row carries the supplier's invoice date (here
		another month and year): both are named from the GL Entry's posting date."""
		cls = PurchaseTaxInvoice
		with patch.object(
			sales_tax_invoice.frappe, "db", SimpleNamespace(get_value=lambda *args: "2026-10-31")
		):
			self.assertEqual(self.named(CD, None, cls=cls, gl_entry="GLE-1"), "PT6910001")
			self.assertEqual(self.named(CD, "2026-09-15", cls=cls, gl_entry="GLE-1"), "PT6910002")
			self.assertEqual(self.named(CD, "2025-01-02", cls=cls, gl_entry="GLE-1"), "PT6910003")
		# a hand-made one without a GL Entry has only its date
		self.assertEqual(self.named(CD, "2026-09-15", cls=cls), "PT6909001")

	def test_purchase_tax_invoices_have_their_own_pt_series(self):
		cls = PurchaseTaxInvoice
		self.assertEqual(self.named(CD, "2026-05-09", cls=cls), "PT6905001")
		self.assertEqual(self.named(CD, "2026-05-09", "CD-B3", cls=cls), "PT6905002")
		self.assertEqual(self.named(CD, "2026-05-09", "CD-B3"), "ST6905001")

	def test_an_amended_copy_under_default_naming_gets_a_fresh_number(self):
		doc = SalesTaxInvoice.__new__(SalesTaxInvoice)
		doc.__dict__.update(
			doctype="Sales Tax Invoice", name="ST6910001", company=CD, date="2026-10-22",
			gl_entry=None, company_tax_address="CD-HO", amended_from="ST6910001",
		)  # fmt: skip
		meta = SimpleNamespace(autoname="", issingle=0)
		doc.meta = meta
		with (
			patch.object(frappe, "get_meta", return_value=meta),
			patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args, **kwargs: None)),
			patch.object(frappe.model.naming, "is_autoincremented", return_value=False),
			patch.object(frappe.model.naming, "set_naming_from_document_naming_rule"),
			patch.object(frappe.model.naming, "validate_name", side_effect=lambda doctype, name: name),
			patch.object(frappe, "get_single_value", return_value="Default Naming"),
			patch.object(frappe, "flags", SimpleNamespace(in_import=False)),
			patch.object(
				doc, "run_method", side_effect=lambda m: doc.autoname() if m == "autoname" else None
			),
		):
			frappe.model.naming.set_new_name(doc)
		# Default Naming (a site setting, not the real default): the copy is named by autoname like a new one
		self.assertEqual(doc.name, "ST6910001")
		self.assertEqual(self.series, {"ST6910": 1})

	def test_an_amended_copy_under_amend_counter_is_the_original_name_with_1(self):
		"""The real default of Frappe: the amended copy is `<original>-1`, autoname is not called."""
		doc = SalesTaxInvoice.__new__(SalesTaxInvoice)
		doc.__dict__.update(
			doctype="Sales Tax Invoice", name="ST6910001", company=CD, date="2026-11-02",
			gl_entry=None, company_tax_address="CD-HO", amended_from="ST6910001",
		)  # fmt: skip
		meta = SimpleNamespace(autoname="", issingle=0)
		doc.meta = meta
		with (
			patch.object(frappe, "get_meta", return_value=meta),
			patch.object(frappe, "db", SimpleNamespace(get_value=lambda *args, **kwargs: None)),
			patch.object(frappe.model.naming, "is_autoincremented", return_value=False),
			patch.object(frappe.model.naming, "validate_name", side_effect=lambda doctype, name: name),
			patch.object(frappe, "get_single_value", return_value="Amend Counter"),
			patch.object(frappe, "flags", SimpleNamespace(in_import=False)),
			patch.object(
				doc, "run_method", side_effect=lambda m: doc.autoname() if m == "autoname" else None
			),
		):
			frappe.model.naming.set_new_name(doc)
		self.assertEqual(doc.name, "ST6910001-1")
		self.assertEqual(self.series, {})

	def test_the_voucher_name_mode_is_kept(self):
		self.settings.use_doc_name_for_sales_taxinv = 1
		self.assertEqual(
			self.named(CD, "2026-10-22", voucher_no="ACC-SINV-2026-00001"), "ACC-SINV-2026-00001"
		)
		self.assertEqual(self.series, {})

	def test_the_zero_tax_manual_key_in_mode_is_kept(self):
		self.settings.create_sales_taxinv_on_zero_tax = 1
		self.settings.manual_keyin_sales_taxinv_on_zero_tax = 1
		voucher = SimpleNamespace(tax_invoice_number="TAX-0001")
		with patch.object(sales_tax_invoice.frappe, "get_doc", return_value=voucher):
			name = self.named(
				CD, "2026-10-22", tax_amount=0, voucher_type="Sales Invoice", voucher_no="ACC-SINV-2026-00001"
			)
		self.assertEqual(name, "TAX-0001")

	def test_the_zero_tax_manual_mode_without_a_number_uses_the_series(self):
		self.settings.create_sales_taxinv_on_zero_tax = 1
		self.settings.manual_keyin_sales_taxinv_on_zero_tax = 1
		voucher = SimpleNamespace(tax_invoice_number=None)
		with patch.object(sales_tax_invoice.frappe, "get_doc", return_value=voucher):
			name = self.named(CD, "2026-10-22", tax_amount=0, voucher_type="Sales Invoice", voucher_no="X")
		self.assertEqual(name, "ST6910001")



if __name__ == "__main__":
	unittest.main()
