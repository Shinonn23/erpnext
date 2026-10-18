# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpnext.accounts import general_ledger, utils
from erpnext.accounts.doctype.account_closing_balance.account_closing_balance import (
	aggregate_with_last_account_closing_balance,
	generate_key,
)
from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry
from erpnext.accounts.doctype.period_closing_voucher.period_closing_voucher import (
	PeriodClosingVoucher,
)


class TestStandardDimensions(unittest.TestCase):
	def setUp(self):
		patcher = patch.object(frappe, "get_system_settings", return_value="Banker's Rounding")
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_native_fields_are_optional_and_visible_without_custom_dimensions(self):
		for doctype in (
			"journal_entry_account",
			"gl_entry",
			"payment_ledger_entry",
			"account_closing_balance",
		):
			with self.subTest(doctype=doctype):
				definition = json.loads(
					(Path(__file__).parent / "doctype" / doctype / f"{doctype}.json").read_text()
				)
				fields = {field["fieldname"]: field for field in definition["fields"]}
				for fieldname, target in (("department", "Department"), ("territory", "Territory")):
					self.assertEqual(fields[fieldname]["options"], target)
					self.assertEqual(fields[fieldname]["fieldtype"], "Link")
					self.assertEqual(fields[fieldname]["reqd"], 0)
					self.assertFalse(fields[fieldname].get("hidden"))
				self.assertEqual(fields["sales_person"]["options"], "Sales Person")
				self.assertEqual(fields["sales_person"]["fieldtype"], "Link")
				self.assertEqual(fields["sales_person"]["reqd"], 0)
				self.assertEqual(fields["sales_person"]["read_only"], 1)
				if doctype == "journal_entry_account":
					order = definition["field_order"]
					self.assertLess(
						order.index("accounting_dimensions_section"),
						order.index("standard_dimensions_section"),
					)
					for fieldname in ("project", "department", "territory", "sales_person"):
						self.assertLess(order.index("standard_dimensions_section"), order.index(fieldname))
						self.assertLess(order.index(fieldname), order.index("currency_section"))

	def test_journal_rows_reach_gl_map_with_empty_or_populated_dimensions(self):
		for department, territory in ((None, None), ("Operations", "East")):
			with self.subTest(department=department, territory=territory):
				row = frappe._dict(
					account="Receivable",
					debit=10,
					credit=0,
					debit_in_account_currency=10,
					credit_in_account_currency=0,
					account_currency="USD",
					project="Project",
					department=department,
					territory=territory,
				)
				row.precision = lambda field: 2
				doc = SimpleNamespace(
					company="Company",
					multi_currency=0,
					voucher_type="Journal Entry",
					due_date=None,
					remark=None,
					finance_book=None,
					get=lambda field: [row],
					get_gl_dict=lambda args, item: frappe._dict(args),
				)
				with (
					patch("erpnext.get_company_currency", return_value="USD"),
					patch(
						"erpnext.accounts.doctype.journal_entry.journal_entry.get_advance_payment_doctypes",
						return_value=[],
					),
					patch.object(frappe, "get_cached_value", return_value="Bank"),
				):
					entry = JournalEntry.build_gl_map(doc)[0]
				self.assertEqual((entry.department, entry.territory), (department, territory))
				self.assertEqual(entry.debit, 10)

	def test_gl_merge_keeps_each_standard_dimension_separate(self):
		base = frappe._dict(account="Account", company="Company", debit=10, credit=0)
		entries = [
			frappe._dict(base, department=department, territory=territory, sales_person=sales_person)
			for department, territory, sales_person in (
				(None, None, None),
				(None, None, None),
				("Operations", None, None),
				("Operations", "East", "Alice"),
				("Operations", "East", "Bob"),
				("Operations", "West", "Alice"),
			)
		]
		with (
			patch.object(general_ledger, "get_accounting_dimensions", return_value=[]),
			patch("erpnext.get_company_currency", return_value="USD"),
		):
			merged = general_ledger.merge_similar_entries(entries, precision=2)
		self.assertEqual(len(merged), 5)
		self.assertEqual(merged[0].debit, 20)

	def test_gl_dimensions_reach_payment_ledger_on_submit_and_cancel(self):
		query = MagicMock()
		query.select.return_value = query
		query.where.return_value = query
		query.run.return_value = [frappe._dict(name="Receivable", account_type="Receivable")]
		for department, territory, sales_person in ((None, None, None), ("Operations", "East", "Alice")):
			for cancel in (0, 1):
				with self.subTest(department=department, territory=territory, cancel=cancel):
					gle = frappe._dict(
						company="Company",
						account="Receivable",
						debit=10,
						credit=0,
						debit_in_account_currency=10,
						credit_in_account_currency=0,
						department=department,
						territory=territory,
						sales_person=sales_person,
					)
					with (
						patch.object(utils, "qb", MagicMock()) as qb,
						patch.object(utils, "get_dimensions", return_value=([], {})),
					):
						qb.from_.return_value = query
						entry = utils.get_payment_ledger_entries([gle], cancel=cancel)[0]
					self.assertEqual((entry.department, entry.territory), (department, territory))
					self.assertEqual(entry.sales_person, sales_person)
					self.assertEqual(entry.amount, -10 if cancel else 10)

	def test_closing_balances_keep_standard_dimension_keys_and_values(self):
		entries = [
			frappe._dict(
				account="Account",
				company="Company",
				debit=10,
				credit=0,
				debit_in_account_currency=10,
				credit_in_account_currency=0,
				department=department,
				territory=territory,
				sales_person=sales_person,
			)
			for department, territory, sales_person in (
				(None, None, None),
				("Operations", None, None),
				("Operations", "East", "Alice"),
				("Operations", "East", "Bob"),
			)
		]
		merged = aggregate_with_last_account_closing_balance(entries, [])
		self.assertEqual(len(merged), 4)
		for entry in entries:
			key, dimensions = generate_key(entry, [])
			self.assertEqual(dimensions["department"], entry.department or "")
			self.assertEqual(dimensions["territory"], entry.territory or "")
			self.assertEqual(dimensions["sales_person"], entry.sales_person or "")
			self.assertEqual(merged[key]["dimensions"], dimensions)
		doc = SimpleNamespace()
		with patch(
			"erpnext.accounts.doctype.period_closing_voucher.period_closing_voucher.get_accounting_dimensions",
			return_value=[],
		):
			PeriodClosingVoucher.get_accounting_dimension_fields(doc)
		self.assertIn("department", doc.accounting_dimension_fields)
		self.assertIn("territory", doc.accounting_dimension_fields)
		self.assertIn("sales_person", doc.accounting_dimension_fields)
