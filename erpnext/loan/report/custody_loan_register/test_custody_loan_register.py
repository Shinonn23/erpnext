"""Loan tracking SQL and summaries, executed against an isolated in-memory database."""

import re
import sqlite3
from unittest import TestCase
from unittest.mock import patch

import frappe

from erpnext.loan.report.custody_loan_register import custody_loan_register as report


class TestCustodyLoanRegister(TestCase):
	def setUp(self):
		self.db = sqlite3.connect(":memory:")
		self.db.row_factory = sqlite3.Row
		self.addCleanup(self.db.close)
		for name, fields in {
			"Custody Loan": "name,material_request,loan_type,borrower_type,borrower,requested_by,requested_by_name,requested_by_user,expected_return_date,company,status,delivery_note,docstatus",
			"Custody Loan Item": "name,parent,qty,issued_qty,returned_qty,adjusted_qty,sold_qty,outstanding_qty,asset,valuation_rate,material_request_item",
			"Material Request": "name,material_request_type,owner",
			"Material Request Item": "name,rate,conversion_factor",
			"Company": "name,default_currency",
			"Delivery Note": "name,custody_loan_shipment",
			"Shipment": "name,custody_loan,docstatus,modified",
			"Employee": "name,user_id,company,status,employee_name",
		}.items():
			self.db.execute(f"CREATE TABLE `tab{name}` ({fields})")
		self.insert("Company", name="CD", default_currency="THB")
		self.insert("Company", name="OTHER", default_currency="USD")
		self.patch = patch.object(report.frappe, "db", frappe._dict(sql=self.sql))
		self.patch.start()
		self.addCleanup(self.patch.stop)
		self.matches = patch.object(report, "get_match_cond", return_value="")
		self.matches.start()
		self.addCleanup(self.matches.stop)
		self.hooks = patch.object(report.frappe, "get_hooks", return_value={})
		self.hooks.start()
		self.addCleanup(self.hooks.stop)

	def insert(self, table, **values):
		columns = ",".join(values)
		placeholders = ",".join("?" for _ in values)
		self.db.execute(
			f"INSERT INTO `tab{table}` ({columns}) VALUES ({placeholders})", list(values.values())
		)

	def sql(self, query, values, as_dict=False):
		query = re.sub(r"%\((\w+)\)s", r":\1", query)
		return [frappe._dict(dict(row)) for row in self.db.execute(query, values).fetchall()]

	def loan(
		self,
		name="LOAN-1",
		*,
		qty=5,
		issued=5,
		returned=1,
		adjusted=1,
		sold=0,
		value=100,
		asset=None,
		purpose="Customer Demo",
		customer="HOSPITAL",
		company="CD",
	):
		self.insert(
			"Material Request", name=name, material_request_type=purpose, owner="requester@example.com"
		)
		self.insert("Material Request Item", name=name, rate=value, conversion_factor=1)
		self.insert(
			"Custody Loan",
			name=name,
			material_request=name,
			loan_type="Asset" if asset else "Stock",
			borrower_type="Customer",
			borrower=customer,
			requested_by="EMP-1",
			requested_by_name="Requester",
			requested_by_user="requester@example.com",
			expected_return_date="2026-11-30",
			company=company,
			status="Active" if issued else "Pending Pick",
			delivery_note=None,
			docstatus=1,
		)
		self.insert(
			"Custody Loan Item",
			name=name,
			parent=name,
			qty=qty,
			issued_qty=issued,
			returned_qty=returned,
			adjusted_qty=adjusted,
			sold_qty=sold,
			outstanding_qty=issued - returned - adjusted - sold,
			asset=asset,
			valuation_rate=0 if asset else value,
			material_request_item=name,
		)

	def test_prepared_loan_has_no_issued_quantity_or_value(self):
		self.loan(issued=0, returned=0, adjusted=0)
		columns, rows = report.execute()
		self.assertEqual((rows[0].prepared_qty, rows[0].issued_qty, rows[0].loan_value), (5, 0, 0))
		self.assertEqual(rows[0].outstanding_value, 0)
		self.assertEqual(next(c for c in columns if c["fieldname"] == "loan_value")["options"], "currency")

	def test_partial_return_and_consumption_keep_recorded_exposure(self):
		self.loan(sold=1)
		_, rows = report.execute()
		self.assertEqual(
			(rows[0].issued_qty, rows[0].returned_qty, rows[0].adjusted_qty, rows[0].sold_qty), (5, 1, 1, 1)
		)
		self.assertEqual((rows[0].loan_value, rows[0].outstanding_value), (500, 200))

	def test_asset_zero_recorded_value_stays_zero(self):
		self.loan(qty=1, issued=1, returned=0, adjusted=0, asset="FULLY-DEPRECIATED", value=0)
		_, rows = report.execute()
		self.assertEqual((rows[0].loan_value, rows[0].outstanding_value), (0, 0))

	def test_asset_value_uses_its_recorded_request_not_later_depreciation(self):
		self.loan(qty=1, issued=1, returned=0, adjusted=0, asset="ASSET", value=12000)
		_, rows = report.execute()
		self.assertEqual(rows[0].loan_value, 12000)

	def test_summary_separates_customer_purpose_company_currency_and_loan_type(self):
		self.loan("FIRST")
		self.loan("SAME")
		self.loan("CUSTOMER", customer="OTHER HOSPITAL")
		self.loan("PURPOSE", purpose="Customer Borrow / Loan")
		self.loan("COMPANY", company="OTHER")
		self.loan("ASSET", qty=1, issued=1, returned=0, adjusted=0, asset="ASSET")
		_, rows = report.execute({"summary_by_requester": 1})
		self.assertEqual(len(rows), 5)
		combined = next(r for r in rows if r.loan_count == 2)
		self.assertEqual((combined.issued_qty, combined.adjusted_qty, combined.loan_value), (10, 2, 1000))
		self.assertEqual({(r.company, r.currency) for r in rows}, {("CD", "THB"), ("OTHER", "USD")})

	def test_purpose_and_requester_filters_are_bound_and_cannot_inject_sql(self):
		self.loan()
		self.assertEqual(report.execute({"purpose": "Customer Borrow / Loan"})[1], [])
		self.assertEqual(report.execute({"borrower": "HOSPITAL' OR 1=1 --"})[1], [])
		self.assertEqual(len(report.execute({"requested_by": "EMP-1", "purpose": "Customer Demo"})[1]), 1)

	def test_document_permission_condition_applies_before_summary(self):
		self.loan()
		with patch.object(
			report, "get_match_cond", return_value=" and (`tabCustody Loan`.company = 'OTHER')"
		):
			self.assertEqual(report.execute({"summary_by_requester": 1})[1], [])

	def test_query_guard_cannot_be_widened_by_a_shared_loan(self):
		self.loan()
		with (
			patch.object(report.frappe, "get_hooks", return_value={"Custody Loan": ["loan.guard"]}),
			patch.object(report.frappe, "get_attr", return_value=lambda user: "`tabCustody Loan`.company = 'OTHER'"),
			patch.object(report.frappe, "session", frappe._dict(user="requester@example.com")),
		):
			self.assertEqual(report.execute({"summary_by_requester": 1})[1], [])
