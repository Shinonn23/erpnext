"""Sales Tax Invoice numbers per company (owner, 22-10-2026): CD-CI2600001, CD-CI2600002, ... and UB-CI2600001, ...,
the year from the tax invoice's own date. The two modes of Thai Tax Settings that name it otherwise stay as they were.
Offline: Frappe's own naming (make_autoname) runs; the Series table is an in-memory counter."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
import frappe.model.naming

from erpnext.regional.doctype.sales_tax_invoice import sales_tax_invoice
from erpnext.regional.doctype.sales_tax_invoice.sales_tax_invoice import SalesTaxInvoice

CD, UB = "บริษัท คอนเนค ไดแอกโนสติกส์ จำกัด", "บริษัท ยูบีคอินเทลลิซีส จำกัด"
ABBR = {CD: "CD", UB: "UB"}


class TestSalesTaxInvoiceNaming(unittest.TestCase):
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
			patch.object(frappe, "get_cached_value", side_effect=lambda doctype, name, field: ABBR[name]),
			patch.object(
				sales_tax_invoice, "get_thai_tax_settings", side_effect=lambda company: self.settings
			),
		):
			target.start()
			self.addCleanup(target.stop)

	def next_number(self, prefix, digits):
		self.series[prefix] = self.series.get(prefix, 0) + 1
		return str(self.series[prefix]).zfill(digits)

	def named(self, company, date, **fields):
		doc = SalesTaxInvoice.__new__(SalesTaxInvoice)
		doc.__dict__.update(doctype="Sales Tax Invoice", name=None, company=company, date=date, gl_entry=None)
		doc.__dict__.update(fields)
		doc.autoname()
		return doc.name

	def test_each_company_numbers_its_own_tax_invoices(self):
		self.assertEqual(self.named(CD, "2026-10-22"), "CD-CI2600001")
		self.assertEqual(self.named(CD, "2026-10-22"), "CD-CI2600002")
		self.assertEqual(self.named(UB, "2026-10-22"), "UB-CI2600001")
		self.assertEqual(self.named(CD, "2026-10-23"), "CD-CI2600003")

	def test_the_year_is_the_tax_invoice_date_s(self):
		self.assertEqual(self.named(CD, "2026-12-31"), "CD-CI2600001")
		self.assertEqual(self.named(CD, "2027-01-02"), "CD-CI2700001")

	def test_the_date_of_the_gl_entry_when_the_date_is_not_fetched_yet(self):
		with patch.object(
			sales_tax_invoice.frappe, "db", SimpleNamespace(get_value=lambda *args: "2025-12-31")
		):
			self.assertEqual(self.named(CD, None, gl_entry="GLE-1"), "CD-CI2500001")

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


if __name__ == "__main__":
	unittest.main()
