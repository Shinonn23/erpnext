"""Employee service certificates use explicit tax-document data, never private HR fields."""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe
from jinja2 import Environment
from pypika import MySQLQuery, Table
from pypika.dialects import MySQLQueryBuilder

from erpnext.regional.doctype.withholding_tax_cert import withholding_tax_cert as controller
from erpnext.regional.report.pnd3 import pnd3
from erpnext.regional.thailand.custom import withholding_cert as certificates


class Certificate(frappe._dict):
	def append(self, field, value):
		self.setdefault(field, []).append(frappe._dict(value))


class TestEmployeeWithholdingCertificate(unittest.TestCase):
	def setUp(self):
		for module in (certificates, controller):
			translation = patch.object(module, "_", lambda text: text)
			translation.start()
			self.addCleanup(translation.stop)

	def voucher(self, **values):
		doc = frappe._dict(
			doctype="Payment Entry", name="PE-EMP-1", payment_type="Pay", party_type="Employee",
			party="EMP-1", company="Company", posting_date="2026-11-09", docstatus=1,
			check_permission=Mock(),
			tax_withholding_entries=[frappe._dict(
				party_type="Employee", party="EMP-1", withholding_amount=150, taxable_amount=5000,
				tax_rate=3, tax_withholding_category="Service",
			)],
		)
		doc.update(values)
		return doc

	def test_employee_recipient_rejects_mixed_tax_entry_identity(self):
		doc = self.voucher()
		self.assertEqual(certificates.withholding_recipient(doc), ("Employee", "EMP-1"))
		doc.tax_withholding_entries[0].party = "EMP-2"
		self.assertIsNone(certificates.withholding_recipient(doc))

	def test_new_certificate_queries_only_public_employee_identity(self):
		cert = Certificate(flags=frappe._dict(), withholding_tax_items=[])
		lines = {("5", 3): {"tax_base": 5000, "tax_amount": 150, "kinds": ["Services"]}}
		with (
			patch.object(frappe, "new_doc", return_value=cert),
			patch.object(frappe, "db", Mock(get_value=Mock(return_value=frappe._dict(
				employee_name="Employee Name", company="Company",
			)))) as db,
			patch.object(certificates, "company_address_of", return_value="Company Address"),
		):
			certificates.new_certificate(self.voucher(), "EMP-1", "PND3", lines, party_type="Employee")
			db.get_value.assert_called_once_with(
				"Employee", "EMP-1", ["employee_name", "company"], as_dict=True
			)
		self.assertEqual(cert.employee, "EMP-1")
		self.assertFalse(cert.supplier)
		self.assertEqual(cert.supplier_name, "Employee Name")
		self.assertFalse(cert.supplier_tax_id)
		self.assertFalse(cert.employee_address)
		self.assertEqual(cert.withholding_tax_items[0].tax_amount, 150)
		self.assertIn("Recipient Tax ID", certificates.missing_details(cert, lines))
		self.assertIn("Employee Address", certificates.missing_details(cert, lines))

	def document(self, **values):
		doc = object.__new__(controller.WithholdingTaxCert)
		doc.__dict__.update(dict(
			doctype="Withholding Tax Cert", employee="EMP-1", supplier=None, company="Company",
			supplier_address=None, voucher_type="Payment Entry", voucher_no="PE-EMP-1",
		))
		doc.__dict__.update(values)
		return doc

	def test_certificate_validates_saved_employee_and_company(self):
		doc = self.document()
		with (
			patch.object(controller, "employee_recipient", return_value=frappe._dict(employee_name="Name")),
			patch.object(frappe, "get_doc", return_value=self.voucher()) as get_doc,
		):
			doc.validate()
			get_doc.assert_called_once_with("Payment Entry", "PE-EMP-1")
		self.assertEqual(doc.supplier_name, "Name")
		with (
			patch.object(controller, "employee_recipient", return_value=frappe._dict(employee_name="Name")),
			patch.object(frappe, "get_doc", return_value=self.voucher(party="EMP-2")),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			doc.validate()

	def test_certificate_rejects_two_recipient_types(self):
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError):
			with self.assertRaises(frappe.ValidationError):
				self.document(supplier="SUP-1").validate()

	def test_certificate_requires_explicit_tax_details_and_submitted_payment(self):
		with patch.object(frappe, "throw", side_effect=frappe.ValidationError):
			with self.assertRaises(frappe.ValidationError):
				self.document().before_submit()
			doc = self.document(supplier_tax_id="1234567890123", employee_address="Certificate address")
			with patch.object(frappe, "db", Mock(get_value=Mock(return_value=0))):
				with self.assertRaises(frappe.ValidationError):
					doc.before_submit()
			with patch.object(frappe, "db", Mock(get_value=Mock(return_value=1))):
				doc.before_submit()

	def test_pnd3_keeps_supplier_addresses_for_empty_employee_links(self):
		sql = []
		def run(query, **kwargs):
			sql.append(str(query))
			return []
		with (
			patch.object(frappe, "qb", SimpleNamespace(DocType=lambda name: Table("tab" + name), from_=MySQLQuery.from_)),
			patch.object(MySQLQueryBuilder, "run", run, create=True),
		):
			self.assertEqual(pnd3.get_data(frappe._dict(month=11, year=2026)), [])
		self.assertIn("LEFT JOIN `tabSupplier`", sql[0])
		self.assertIn("NOT nullif(`tabWithholding Tax Cert`.`employee`,'') IS NULL", sql[0])
		self.assertIn("employee_address", sql[0])
		self.assertNotIn("tabEmployee", sql[0])
		self.assertNotIn("national_id_number", sql[0])

	def test_print_uses_employee_snapshots_without_supplier_or_hr_lookups(self):
		path = Path(__file__).parents[2] / "print_format/withholding_tax_cert/withholding_tax_cert.json"
		environment = Environment()
		environment.filters["str"] = str
		doc = Certificate(
			employee="EMP-1", supplier=None, supplier_name="Employee Name", supplier_tax_id="1234567890123",
			employee_address="Explicit <address>", company="Company", company_address="Company Address",
			name="WH-1", date="2026-11-09", voucher_no="PE-EMP-1", owner="Administrator",
			withholding_tax_items=[frappe._dict(type_of_income="5", tax_base=5000, tax_amount=150, tax_rate=3)],
		)
		def get_doc(doctype, name):
			self.assertEqual((doctype, name), ("Address", "Company Address"))
			return frappe._dict(address_line1="Company Address")
		html = environment.from_string(json.loads(path.read_text())["html"]).render(
			doc=doc, frappe=SimpleNamespace(get_doc=get_doc, get_fullname=lambda user: user),
			amount_to_text=lambda *args, **kwargs: "Amount in words",
		)
		self.assertIn("Employee Name", html)
		self.assertIn("Explicit &lt;address&gt;", html)
		self.assertIn("150.00", html)
		self.assertEqual(html.count('class="wht-copy"'), 4)
