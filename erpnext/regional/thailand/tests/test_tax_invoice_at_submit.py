"""The tax invoice number is a posting requirement (16-11-2026): a Purchase Invoice or Expense Claim with input VAT
saves as a Draft without it and is refused only on submit. Offline: the Thai Tax Settings are a stand-in."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe

from erpnext import hooks
from erpnext.regional.thailand.custom import custom_api

SETTINGS = SimpleNamespace(purchase_tax_account="1154-00")


def _doc(vat=True, number=None, split=0, splitted=None):
	taxes = [SimpleNamespace(account_head="1154-00" if vat else "5000-00")]
	return frappe._dict(
		company="CD",
		taxes=taxes,
		tax_invoice_number=number,
		split_tax_invoice=split,
		splitted_tax_invoices=splitted or [],
	)


def _throw(message, *args, **kwargs):
	raise frappe.ValidationError(message)


@patch.object(custom_api.frappe, "throw", side_effect=_throw)
@patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS)
class TestTaxInvoiceAtSubmit(unittest.TestCase):
	def test_hooks_run_on_submit_only(self, *_mocks):
		for doctype in ("Purchase Invoice", "Expense Claim"):
			events = hooks.doc_events[doctype]
			target = "erpnext.regional.thailand.custom.custom_api.validate_tax_invoice"
			self.assertEqual(events["before_submit"], target)
			self.assertNotIn("after_insert", events)
			self.assertNotIn("on_update", events)

	def test_vat_without_number_refused(self, *_mocks):
		with self.assertRaises(frappe.ValidationError):
			custom_api.validate_tax_invoice(_doc(), "before_submit")

	def test_vat_with_number_passes(self, *_mocks):
		custom_api.validate_tax_invoice(_doc(number="T-1"), "before_submit")

	def test_no_vat_with_number_refused(self, *_mocks):
		with self.assertRaises(frappe.ValidationError):
			custom_api.validate_tax_invoice(_doc(vat=False, number="T-1"), "before_submit")

	def test_no_vat_no_number_passes(self, *_mocks):
		custom_api.validate_tax_invoice(_doc(vat=False), "before_submit")

	def test_split_variants(self, *_mocks):
		with self.assertRaises(frappe.ValidationError):
			custom_api.validate_tax_invoice(_doc(split=1), "before_submit")
		with self.assertRaises(frappe.ValidationError):
			custom_api.validate_tax_invoice(_doc(vat=False, split=1, splitted=[1]), "before_submit")
		custom_api.validate_tax_invoice(_doc(split=1, splitted=[1]), "before_submit")
		custom_api.validate_tax_invoice(_doc(vat=False, split=1), "before_submit")


if __name__ == "__main__":
	unittest.main()
