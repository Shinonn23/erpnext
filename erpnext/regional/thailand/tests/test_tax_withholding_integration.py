import json
import unittest
from unittest.mock import MagicMock, patch

import frappe
from frappe.model.base_document import get_controller

from erpnext.accounts.doctype.tax_withholding_entry.tax_withholding_entry import PaymentTaxWithholding
from erpnext.regional.thailand.constants import ERP_CUSTOM_FIELDS
from erpnext.regional.thailand.custom.payment_entry import make_withholding_tax_cert


class TestThaiTaxWithholdingIntegration(unittest.TestCase):
	def test_core_withholding_entry_builds_thai_certificate_line(self):
		cert = MagicMock()
		supplier = MagicMock(supplier_name="Supplier Co", supplier_primary_address="Supplier Address")
		category = {
			"thai_type_of_income": "5",
			"thai_income_tax_form": "PND53",
		}
		payment_entry = {
			"party_type": "Supplier",
			"party": "SUP-001",
			"company": "Example Co",
			"name": "ACC-PAY-2026-00001",
			"tax_withholding_entries": [
				{
					"tax_withholding_category": "Service WHT",
					"taxable_amount": 10000,
					"tax_rate": 3,
					"withholding_amount": 300,
				}
			],
		}

		with (
			patch("erpnext.regional.thailand.custom.payment_entry.frappe.new_doc", return_value=cert),
			patch("erpnext.regional.thailand.custom.payment_entry.frappe.get_doc", return_value=supplier),
			patch(
				"erpnext.regional.thailand.custom.payment_entry.frappe.db.get_value",
				return_value=category,
			),
		):
			result = make_withholding_tax_cert(
				json.dumps({"date": "2026-10-05", "company_address": "Company Address"}),
				json.dumps(payment_entry),
			)

		self.assertIs(result, cert)
		cert.append.assert_called_once_with(
			"withholding_tax_items",
			{
				"type_of_income": "5",
				"tax_base": 10000,
				"tax_rate": 3,
				"tax_amount": 300,
			},
		)
		self.assertEqual(cert.income_tax_form, "PND53")

	def test_setup_uses_core_category_as_the_only_wht_master(self):
		category_fields = {field["fieldname"] for field in ERP_CUSTOM_FIELDS["Tax Withholding Category"]}
		self.assertEqual(category_fields, {"thai_income_tax_form", "thai_type_of_income"})
		self.assertNotIn("Payment Entry Deduction", ERP_CUSTOM_FIELDS)
		self.assertNotIn("Withholding Tax Type", ERP_CUSTOM_FIELDS)

	def test_payment_entry_extension_composes_with_installed_controller(self):
		controller_bases = get_controller("Payment Entry").__mro__
		self.assertIn(
			"erpnext.regional.thailand.custom.payment_entry.PaymentEntry",
			{f"{cls.__module__}.{cls.__name__}" for cls in controller_bases},
		)

	def test_core_payment_withholding_calculates_multiple_categories(self):
		payment = frappe._dict(
			{
				"tax_withholding_categories": [
					frappe._dict(tax_withholding_category="Services", taxable_amount=10000),
					frappe._dict(tax_withholding_category="Rent", taxable_amount=5000),
				],
				"unallocated_amount": 0,
				"references": [],
				"payment_type": "Pay",
				"target_exchange_rate": 1,
				"precision": MagicMock(return_value=2),
				"company": "Example Co",
				"posting_date": "2026-10-05",
				"tax_withholding_group": None,
			}
		)
		controller = PaymentTaxWithholding(payment)
		controller.category_details = {
			"Services": frappe._dict(taxable_amount=0),
			"Rent": frappe._dict(taxable_amount=0),
		}

		self.assertEqual(set(controller._get_category_names()), {"Services", "Rent"})
		controller._update_taxable_amounts()
		self.assertEqual(controller.category_details["Services"].taxable_amount, 10000)
		self.assertEqual(controller.category_details["Rent"].taxable_amount, 5000)


def run_unit_tests():
	result = unittest.TextTestRunner(verbosity=2).run(
		unittest.defaultTestLoader.loadTestsFromTestCase(TestThaiTaxWithholdingIntegration)
	)
	if not result.wasSuccessful():
		raise AssertionError("Thai tax withholding integration tests failed")
