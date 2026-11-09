# Copyright (c) 2023, Kitti U. and Contributors
# See license.txt

"""Output VAT cannot be delayed (Revenue Department notice on VAT no. 4: only input VAT may be reported
in a later month). Offline: only validate_months_delayed runs, no database."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from erpnext.regional.doctype.sales_tax_invoice import sales_tax_invoice
from erpnext.regional.doctype.sales_tax_invoice.sales_tax_invoice import SalesTaxInvoice


class TestSalesTaxInvoice(unittest.TestCase):
	def check(self, months):
		# no bound site offline: translate nothing, raise what frappe.throw raises
		def throw(message, *args, **kwargs):
			raise frappe.ValidationError(message)

		with (
			patch.object(sales_tax_invoice, "_", lambda text: text),
			patch.object(sales_tax_invoice.frappe, "throw", throw),
		):
			SalesTaxInvoice.validate_months_delayed(SimpleNamespace(months_delayed=months))

	def test_zero_passes(self):
		for months in ("0", 0, None, ""):
			self.check(months)

	def test_one_to_six_months_are_refused(self):
		for months in ("1", "2", "3", "4", "5", "6", 3):
			with self.assertRaises(frappe.ValidationError):
				self.check(months)
