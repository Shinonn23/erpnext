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
from erpnext.regional.thailand.custom import payment_entry
from erpnext.regional.thailand.custom import withholding_cert as certificates


class Certificate(frappe._dict):
	def append(self, field, value):
		self.setdefault(field, []).append(frappe._dict(value))


class TestEmployeeWithholdingCertificate(unittest.TestCase):
	def setUp(self):
		for module in (certificates, controller, payment_entry):
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
			income_tax_form="PND3",
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

	def test_employee_payment_rejects_other_forms_before_native_validation(self):
		class BasePayment(frappe._dict):
			def validate(self):
				self.native_validation_called = True

		class EmployeePayment(payment_entry.PaymentEntry, BasePayment):
			pass

		for form in ("PND53", "PND1", "Unsupported"):
			for docstatus in (0, 1):
				with self.subTest(form=form, docstatus=docstatus):
					doc = EmployeePayment(
						party_type="Employee",
						apply_tds=1,
						tax_withholding_category="Services",
						docstatus=docstatus,
					)
					with (
						patch.object(frappe, "db", Mock(get_value=Mock(return_value=form))),
						patch.object(frappe, "throw", side_effect=frappe.ValidationError),
						self.assertRaises(frappe.ValidationError),
					):
						doc.validate()
					self.assertFalse(doc.native_validation_called)

		for form in ("PND3", "", None):
			with self.subTest(form=form):
				doc = EmployeePayment(
					party_type="Employee",
					apply_tds=1,
					tax_withholding_category="Services",
				)
				with (
					patch.object(frappe, "db", Mock(get_value=Mock(return_value=form))) as db,
					patch.object(payment_entry, "validate_receive_rows"),
					patch.object(payment_entry, "validate_advance_vat"),
				):
					doc.validate()
					db.get_value.assert_called_once_with(
						"Tax Withholding Category", "Services", "thai_income_tax_form"
					)
				self.assertTrue(doc.native_validation_called)

		for party_type, apply_tds in (("Supplier", 1), ("Employee", 0)):
			with self.subTest(party_type=party_type, apply_tds=apply_tds):
				doc = EmployeePayment(
					party_type=party_type,
					apply_tds=apply_tds,
					tax_withholding_category="Services",
				)
				with (
					patch.object(frappe, "db", Mock()) as db,
					patch.object(payment_entry, "validate_receive_rows"),
					patch.object(payment_entry, "validate_advance_vat"),
				):
					doc.validate()
					db.get_value.assert_not_called()
				self.assertTrue(doc.native_validation_called)

	def test_manual_employee_certificate_requires_pnd3_and_defaults_blank(self):
		for form in ("PND53", "PND1"):
			with (
				self.subTest(form=form),
				patch.object(frappe, "throw", side_effect=frappe.ValidationError),
				patch.object(controller, "employee_recipient") as employee,
				self.assertRaises(frappe.ValidationError),
			):
				self.document(income_tax_form=form).validate()
			employee.assert_not_called()
		for form in ("PND3", "", None):
			doc = self.document(income_tax_form=form, voucher_no=None)
			with patch.object(
				controller, "employee_recipient", return_value=frappe._dict(employee_name="Name")
			):
				doc.validate()
			self.assertEqual(doc.income_tax_form, "PND3")
		self.document(employee=None, supplier="SUP-1", income_tax_form="PND53").validate()

	def test_generated_employee_certificate_rejects_wrong_form_before_creation(self):
		with (
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(frappe, "new_doc") as new_doc,
			self.assertRaises(frappe.ValidationError),
		):
			certificates.new_certificate(self.voucher(), "EMP-1", "PND53", {}, party_type="Employee")
		new_doc.assert_not_called()

	def test_fallback_employee_certificate_rejects_form_from_request_or_category(self):
		for request_form, category_form in (("PND53", ""), ("", "PND53"), ("PND3", "PND3"), ("", "")):
			with self.subTest(request_form=request_form, category_form=category_form):
				cert = Certificate()
				with (
					patch.object(frappe, "parse_json", side_effect=lambda value: value),
					patch.object(frappe, "get_doc", return_value=self.voucher()),
					patch.object(frappe, "has_permission"),
					patch.object(frappe, "new_doc", return_value=cert),
					patch.object(payment_entry, "existing_certificates", return_value={}),
					patch.object(
						payment_entry, "employee_recipient", return_value=frappe._dict(employee_name="Name")
					),
					patch.object(
						frappe,
						"db",
						Mock(
							get_value=Mock(
								side_effect=lambda dt, name, fields, **kw: (
									"Services"
									if fields == "category_name"
									else frappe._dict(
										thai_type_of_income="5",
										thai_income_tax_form=category_form,
									)
								)
							)
						),
					),
					patch.object(frappe, "get_system_settings", return_value="Commercial Rounding"),
					patch.object(frappe, "throw", side_effect=frappe.ValidationError),
				):

					def make_cert():
						return payment_entry.make_withholding_tax_cert(
							{"income_tax_form": request_form, "company_address": "Address"},
							{"doctype": "Payment Entry", "name": "PE-EMP-1"},
						)

					if "PND53" in (request_form, category_form):
						with self.assertRaises(frappe.ValidationError):
							make_cert()
					else:
						self.assertEqual(make_cert().income_tax_form, "PND3")

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
