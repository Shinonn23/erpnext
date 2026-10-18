# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""Documents hand their standard dimensions to GL without every builder repeating them."""

import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpnext.accounts import general_ledger
from erpnext.accounts.doctype.accounting_dimension import accounting_dimension
from erpnext.controllers.accounts_controller import AccountsController

CONTROLLER = "erpnext.controllers.accounts_controller."


class TestStandardDimensionGLDict(unittest.TestCase):
	def get_gl_dict(self, doc, args=None, item=None):
		doc = frappe._dict(
			{"company": "Company", "name": "DOC-1", "posting_date": "2026-10-18", **doc},
			doctype="Journal Entry",  # skips currency handling that is irrelevant here
		)
		doc.get_voucher_subtype = lambda: None
		with (
			patch(CONTROLLER + "get_fiscal_years", return_value=[("2026",)]),
			patch(CONTROLLER + "temporary_flag", return_value=nullcontext()),
			patch(CONTROLLER + "update_gl_dict_with_regional_fields"),
			patch(CONTROLLER + "update_gl_dict_with_app_based_fields"),
			patch(CONTROLLER + "get_accounting_dimensions", return_value=[]),
			patch(CONTROLLER + "get_dimensions", return_value=([], {})),
			patch(CONTROLLER + "get_account_currency", return_value="USD"),
			patch.object(frappe, "get_hooks", return_value=[]),
		):
			return AccountsController.get_gl_dict(doc, args or {}, item=item)

	def test_header_values_reach_every_row(self):
		entry = self.get_gl_dict({"department": "Ops", "territory": "East", "sales_person": "Alice"})
		self.assertEqual((entry.department, entry.territory, entry.sales_person), ("Ops", "East", "Alice"))

	def test_item_overrides_header(self):
		entry = self.get_gl_dict({"department": "Ops"}, item=frappe._dict(department="Lab"))
		self.assertEqual(entry.department, "Lab")

	def test_caller_values_are_not_overwritten(self):
		entry = self.get_gl_dict(
			{"department": "Ops", "territory": "East"},
			args={"department": "Finance", "territory": None},
			item=frappe._dict(),
		)
		self.assertEqual(entry.department, "Finance")
		# an explicit empty value is "not set", so the stored document fills it
		self.assertEqual(entry.territory, "East")

	def test_documents_without_the_fields_stay_empty(self):
		entry = self.get_gl_dict({}, item=frappe._dict(project="P"))
		self.assertEqual((entry.department, entry.territory, entry.sales_person), (None, None, None))


class TestRoundOffDimensions(unittest.TestCase):
	def round_off_row(self, header_fields):
		row = frappe._dict(
			account="Round Off", company="Company", voucher_type="Sales Invoice", voucher_no="SI-1"
		)
		stored = frappe._dict(territory="East", sales_person="Alice", department=None)
		db = MagicMock()
		db.get_value.return_value = stored
		meta = SimpleNamespace(has_field=lambda field: field in header_fields)
		with (
			patch.object(general_ledger, "get_accounting_dimensions", return_value=[]),
			patch.object(general_ledger, "get_checks_for_pl_and_bs_accounts", return_value=[]),
			patch.object(frappe, "get_meta", return_value=meta),
			patch.object(frappe, "get_cached_value", return_value="Profit and Loss"),
			patch.object(frappe, "db", db),
		):
			general_ledger.update_accounting_dimensions(row)
		return row, db.get_value

	def test_round_off_copies_the_voucher_dimensions_it_has(self):
		row, get_value = self.round_off_row({"territory", "sales_person"})
		self.assertEqual((row.territory, row.sales_person), ("East", "Alice"))
		self.assertNotIn("department", row)
		self.assertEqual(get_value.call_args.args[2], ["territory", "sales_person"])

	def test_round_off_skips_vouchers_without_the_fields(self):
		row, get_value = self.round_off_row(set())
		get_value.assert_not_called()
		self.assertNotIn("territory", row)


class TestStandardDimensionValues(unittest.TestCase):
	def values(self, header_fields, stored):
		db = MagicMock()
		db.get_value.return_value = stored
		meta = SimpleNamespace(has_field=lambda field: field in header_fields)
		with patch.object(frappe, "get_meta", return_value=meta), patch.object(frappe, "db", db):
			return accounting_dimension.get_standard_dimension_values("Sales Invoice", "SI-1"), db.get_value

	def test_only_stored_values_of_fields_the_voucher_has_are_returned(self):
		result, get_value = self.values(
			{"territory", "sales_person"}, frappe._dict(territory="East", sales_person=None)
		)
		self.assertEqual(result, {"territory": "East"})
		self.assertEqual(get_value.call_args.args[2], ["territory", "sales_person"])

	def test_vouchers_without_the_fields_cost_no_query(self):
		result, get_value = self.values(set(), None)
		self.assertEqual(result, {})
		get_value.assert_not_called()

	def test_missing_voucher_reference_returns_nothing(self):
		self.assertEqual(accounting_dimension.get_standard_dimension_values(None, None), {})


class TestGainLossStamp(unittest.TestCase):
	def dimensions(self, entry, stored):
		from erpnext.accounts import utils

		with patch.object(utils, "get_standard_dimension_values", return_value=stored) as lookup:
			return utils._build_dimensions_dict_for_exc_gain_loss(entry, []), lookup

	def test_the_gain_or_loss_carries_the_stamp_of_the_voucher_it_settles(self):
		entry = frappe._dict(against_voucher_type="Sales Invoice", against_voucher="SI-1")
		dimensions, lookup = self.dimensions(entry, {"territory": "East", "sales_person": "Alice"})
		self.assertEqual(dimensions, {"territory": "East", "sales_person": "Alice"})
		lookup.assert_called_once_with("Sales Invoice", "SI-1")

	def test_no_entry_means_no_lookup(self):
		dimensions, lookup = self.dimensions(None, {"territory": "East"})
		self.assertEqual(dimensions, {})
		lookup.assert_not_called()
