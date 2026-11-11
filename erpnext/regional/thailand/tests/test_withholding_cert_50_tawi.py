"""The Thai Withholding Tax Certificate (50 tawi, Revenue Department notice no. 62) printed on the form image.
It is the default print format of the Withholding Tax Cert; the English four-copy format stays selectable.
Offline: the Jinja template is rendered with stand-ins, no site."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace

import frappe
from jinja2.sandbox import SandboxedEnvironment

from erpnext.regional.thailand.utils import amount_in_bahttext

REGIONAL = Path(__file__).resolve().parents[2]
FORMAT = REGIONAL / "print_format/withholding_tax_cert_50_tawi/withholding_tax_cert_50_tawi.json"
IMAGE = REGIONAL.parent / "public/images/thailand/wht/WithholdingTaxCert.png"


def render(**overrides):
	print_format = json.loads(FORMAT.read_text())
	environment = SandboxedEnvironment()
	environment.filters["str"] = str
	address = frappe._dict(
		address_line1="99/9 Test Road",
		address_line2=None,
		city="Test City",
		county=None,
		state="Bangkok",
		pincode="10400",
	)
	supplier = frappe._dict(supplier_name="Income Recipient", tax_id="0105551234567")
	items = [
		frappe._dict(type_of_income="5", description="Service", tax_base=12345678.5, tax_amount=370370.36),
		frappe._dict(type_of_income="2", description="", tax_base=1000, tax_amount=30),
		frappe._dict(type_of_income="6", description="Other income", tax_base=2000, tax_amount=60),
	]
	doc = frappe._dict(
		name="WHT-CERT-000123",
		docstatus=1,
		amended_from=None,
		voucher_no="PAY-1",
		date="2026-10-17",
		company="Company",
		company_tax_id="0105560012345",
		company_address="A",
		supplier="S",
		supplier_address="B",
		supplier_name=None,
		supplier_tax_id=None,
		employee=None,
		is_agent=0,
		withholding_agent_for_name=None,
		withholding_tax_items=items,
		income_tax_form="PND53",
		tax_payer="Withholding",
		owner="owner",
	)
	doc.update(overrides)
	mock_frappe = SimpleNamespace(
		get_doc=lambda doctype, name: supplier if doctype == "Supplier" else address,
		get_fullname=lambda owner: "Test Owner",
	)
	template = environment.from_string(print_format["html"])
	return template.render(doc=doc, frappe=mock_frappe, amount_in_bahttext=amount_in_bahttext)


class TestWithholdingCert50Tawi(unittest.TestCase):
	def test_format_is_standard_thai_jinja_on_the_form_image(self):
		print_format = json.loads(FORMAT.read_text())
		self.assertEqual(print_format["doc_type"], "Withholding Tax Cert")
		self.assertEqual(print_format["standard"], "Yes")
		self.assertEqual(print_format["print_format_type"], "Jinja")
		self.assertEqual(print_format["default_print_language"], "th")
		self.assertTrue(IMAGE.exists())
		self.assertIn("/assets/erpnext/images/thailand/wht/WithholdingTaxCert.png", print_format["html"])
		for margin in ("margin-top", "margin-right", "margin-bottom", "margin-left"):
			self.assertIn(f"{margin}: 0mm", print_format["css"])

	def test_it_is_the_default_and_the_old_format_stays(self):
		doctype = json.loads(
			(REGIONAL / "doctype/withholding_tax_cert/withholding_tax_cert.json").read_text()
		)
		self.assertEqual(doctype["default_print_format"], "Withholding Tax Cert 50 Tawi")
		self.assertTrue((REGIONAL / "print_format/withholding_tax_cert/withholding_tax_cert.json").exists())

	def test_three_copies_with_the_ticks_and_amounts(self):
		html = render()
		self.assertEqual(html.count('class="wht50-page"'), 3)
		for copy in ("ฉบับที่ 1", "ฉบับที่ 2", "ฉบับที่ 3"):
			self.assertIn(copy, html)
		self.assertNotIn("Cancelled", html)
		# PND53 and "withholding" ticked once on each copy, nothing else
		self.assertEqual(html.count(">X<"), 6)
		for value in ("12,345,678", "370,370", "12,348,678", "370,460", "17/10/2569", "ตุลาคม", "2569"):
			self.assertIn(value, html)
		self.assertIn(amount_in_bahttext(370460.36), html)

	def test_tax_id_goes_into_13_cells_each_and_a_wrong_length_is_left_blank(self):
		html = render()
		cells = html.count('class="f c" style="left:')
		html_bad = render(company_tax_id="12345")
		self.assertEqual(cells - html_bad.count('class="f c" style="left:'), 3 * 13)

	def test_each_choice_ticks_its_own_box(self):
		for form in ("PND3", "PND53"):
			for payer in ("Withholding", "Paid One Time", "Paid Continuously"):
				self.assertEqual(render(income_tax_form=form, tax_payer=payer).count(">X<"), 6)
		self.assertEqual(render(income_tax_form="", tax_payer="").count(">X<"), 0)

	def test_cancelled_certificate_is_marked(self):
		self.assertEqual(render(docstatus=2).count("ยกเลิก (Cancelled)"), 3)
