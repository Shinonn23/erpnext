import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from jinja2 import Environment

import frappe

from erpnext.regional.thailand.utils import amount_to_text, finalize_address_dict, full_thai_date


class TestEnglishSystemOutput(unittest.TestCase):
	def test_currency_defaults_to_english_and_explicit_thai_still_works(self):
		self.assertEqual(
			amount_to_text(123.45, currency="THB"), "One Hundred And Twenty-Three Baht, Forty-Five Satang"
		)
		for amount, expected in [
			(450, "Four Hundred And Fifty Baht, Zero Satang"),
			(0, "Zero Baht, Zero Satang"),
			(-1.50, "Minus One Baht, Fifty Satang"),
			("1.005", "One Baht, One Satang"),
			("0.999", "One Baht, Zero Satang"),
		]:
			self.assertEqual(amount_to_text(amount, currency="THB"), expected)
		self.assertRegex(amount_to_text(123.45, currency="THB", lang="th"), "[\u0e00-\u0e7f]")
		self.assertEqual(full_thai_date("2026-10-17"), "17 October 2569")
		self.assertEqual(full_thai_date(None), "")

	def test_generated_address_labels_are_english_and_data_is_preserved(self):
		with patch("erpnext.regional.thailand.utils.get_prefix_for_address", return_value=("", "", "")):
			address = finalize_address_dict({
				"vBranchTitleName": "Company", "vBranchName": "Example", "vHouseNumber": "12",
				"vBuildingName": "อาคารทดสอบ", "vFloorNumber": "3", "vVillageName": "Example",
				"vRoomNumber": "A", "vMooNumber": "5", "vSoiName": "7", "vStreetName": "Example",
				"vThambol": "Test", "vAmphur": "Test", "vProvince": "กรุงเทพมหานคร", "vPostCode": "10000",
			})
		self.assertEqual(
			address["address_line1"],
			"12 Village Example Soi 7 Moo 5 Building อาคารทดสอบ Floor 3 Room A Road Example",
		)
		self.assertEqual(address["state"], "กรุงเทพมหานคร")

	def test_withholding_certificate_renders_all_fields_and_four_english_copies(self):
		regional = Path(__file__).resolve().parents[2]
		print_format = json.loads(
			(regional / "print_format/withholding_tax_cert/withholding_tax_cert.json").read_text()
		)
		environment = Environment()
		environment.filters["str"] = str
		template = environment.from_string(print_format["html"])
		types = [
			"1", "2", "3", "4", "4.1.1", "4.1.2", "4.1.3", "4.1.4",
			"4.2.1", "4.2.2", "4.2.3", "4.2.4", "4.2.5", "5", "6",
		]
		items = [
			frappe._dict(
				type_of_income=income_type, description=f"Income {income_type}",
				tax_base=1000, tax_rate=3, tax_amount=30,
			)
			for income_type in types
		]
		doc = frappe._dict(
			name="WHT-001", docstatus=2, amended_from="WHT-OLD", voucher_no="PAY-001", date="2026-10-17",
			company="Example <Company>", company_tax_id="1234567890123", company_address="Company Address",
			supplier="Supplier", supplier_address="Supplier Address", is_agent=1,
			withholding_agent_for_name="Represented Supplier", withholding_tax_items=items,
			income_tax_form="PND53", tax_payer="Withholding", owner="owner@example.com",
		)
		address = frappe._dict(
			address_line1="12 Test Road", address_line2=None, city="Test City",
			county=None, state="Test State", pincode="10000",
		)
		supplier = frappe._dict(supplier_name="Income Recipient", tax_id="9876543210123")
		mock_frappe = SimpleNamespace(
			get_doc=lambda doctype, name: supplier if doctype == "Supplier" else address,
			get_fullname=lambda owner: "Test Owner",
		)
		for form in ["PND1", "PND3", "PND3a", "PND53"]:
			for payer in ["Withholding", "Paid One Time", "Paid Continuously"]:
				with self.subTest(form=form, payer=payer):
					doc.income_tax_form, doc.tax_payer = form, payer
					html = template.render(doc=doc, frappe=mock_frappe, amount_to_text=amount_to_text)
					self.assertNotRegex(html, "[\u0e00-\u0e7f]")
					self.assertEqual(html.count('<section class="wht-copy">'), 4)
					self.assertEqual(html.count(f"[X] {form} &nbsp;"), 4)
					self.assertEqual(html.count(f"[X] {payer} &nbsp;"), 4)
					for value in [
						"WHT-001", "WHT-OLD", "PAY-001", "Cancelled", "17/10/2569 B.E.",
						"Example &lt;Company&gt;", "1234567890123", "Income Recipient",
						"Represented Supplier", "9876543210123", "12 Test Road", "Test Owner",
						"15,000.00", "450.00", "Four Hundred And Fifty Baht",
						*[f"Income {value}" for value in types],
					]:
						self.assertIn(value, html)
		self.assertNotIn("background-image", print_format["css"])

	def test_tax_report_templates_and_system_help_have_no_thai(self):
		regional = Path(__file__).resolve().parents[2]
		for name in ("purchase_tax_report", "sales_tax_report"):
			text = (regional / f"report/{name}/{name}.html").read_text()
			self.assertNotRegex(text, "[\u0e00-\u0e7f]")
			self.assertIn("Year (B.E.)", text)
			self.assertIn("Grand Total", text)
		metadata = json.loads((regional / "doctype/withholding_tax_items/withholding_tax_items.json").read_text())
		self.assertNotRegex(json.dumps(metadata, ensure_ascii=False), "[\u0e00-\u0e7f]")


if __name__ == "__main__":
	unittest.main()
