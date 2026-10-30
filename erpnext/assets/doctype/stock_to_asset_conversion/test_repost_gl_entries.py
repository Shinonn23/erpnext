"""Offline tests: Repost Item Valuation restates a submitted conversion's journal without writing its Assets."""

import inspect
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import frappe


def module():
	from erpnext.assets.doctype.stock_to_asset_conversion import stock_to_asset_conversion as conversion

	return conversion


def make_doc(sle_value, asset_value=100, submitting=False, docstatus=1):
	output = Mock(
		source_item_row="R1",
		asset_value=asset_value,
		accounting_treatment="Hold Value",
		asset_item_code="A",
		asset="ASSET-1",
	)
	output.flags = frappe._dict()
	doc = Mock()
	doc.doctype = "Stock to Asset Conversion"
	doc.stock_items = [frappe._dict(name="R1", qty=1, amount=asset_value, warehouse="W")]
	doc.assets = [output]
	doc.flags = frappe._dict(submitting=submitting)
	doc.docstatus = docstatus
	doc.use_item_inventory_account = 0
	doc.company = "CD"
	doc.name = "CONV-1"
	doc.get_stock_ledger_details.return_value = {"R1": [frappe._dict(stock_value_difference=-sle_value)]}
	doc.get_debit_field_precision.return_value = 2
	doc.precision.return_value = 2
	doc.get_inventory_account_dict.return_value = {"account": "Stock - CD"}
	doc.get_gl_dict.side_effect = lambda args, *a, **k: frappe._dict(args)
	return doc, output


class TestRepostGlEntries(unittest.TestCase):
	def run_gl(self, doc, *args, adjustment_account="Stock Adjustment - CD", posted=None):
		conversion = module()
		with ExitStack() as stack:
			stack.enter_context(
				patch.object(conversion.erpnext, "is_perpetual_inventory_enabled", return_value=True)
			)
			stack.enter_context(
				patch.object(conversion.erpnext, "get_default_cost_center", return_value="CC")
			)
			stack.enter_context(
				patch.object(conversion, "flt", side_effect=lambda v, p=None: round(float(v or 0), 2))
			)
			stack.enter_context(patch.object(conversion, "_", side_effect=lambda x: x))
			stack.enter_context(patch.object(conversion.frappe, "db", Mock()))
			stack.enter_context(
				patch.object(conversion.frappe, "get_cached_value", return_value=adjustment_account)
			)
			self.get_all = stack.enter_context(
				patch.object(conversion.frappe, "get_all", return_value=posted or [])
			)
			self.log_error = stack.enter_context(patch.object(conversion.frappe, "log_error"))
			self.get_doc = stack.enter_context(patch.object(conversion.frappe, "get_doc"))
			stack.enter_context(
				patch.object(conversion, "get_asset_category_account", return_value="Fixed Asset - CD")
			)
			return conversion.StocktoAssetConversion.get_gl_entries(doc, *args)

	def assert_balanced(self, entries):
		self.assertEqual(sum(e.get("debit", 0) for e in entries), sum(e.get("credit", 0) for e in entries))

	def test_repost_call_signatures_are_accepted(self):
		conversion = module()
		get_params = inspect.signature(conversion.StocktoAssetConversion.get_gl_entries).parameters
		make_params = inspect.signature(conversion.StocktoAssetConversion.make_gl_entries).parameters
		self.assertIn("inventory_account_map", get_params)
		self.assertIn("gl_entries", make_params)
		self.assertIn("from_repost", make_params)
		# exactly how accounts.utils.repost_gle_for_stock_vouchers calls it
		doc, _output = make_doc(100)
		entries = self.run_gl(doc, {"W": {"account": "Stock - CD"}})
		self.assert_balanced(entries)

	def test_make_gl_entries_posts_the_reposted_entries_as_given(self):
		conversion = module()
		doc = Mock(docstatus=1)
		given = [frappe._dict(account="X", debit=1), frappe._dict(account="Y", credit=1)]
		with patch("erpnext.accounts.general_ledger.make_gl_entries") as post:
			conversion.StocktoAssetConversion.make_gl_entries(doc, gl_entries=given, from_repost=True)
		post.assert_called_once_with(given, merge_entries=False, from_repost=True)
		doc.get_gl_entries.assert_not_called()

	def test_unchanged_value_restates_exactly_what_submit_posted(self):
		doc, output = make_doc(100)
		reposted = self.run_gl(doc, {})
		self.get_doc.assert_not_called()
		output.db_set.assert_not_called()
		submit_doc, _submit_output = make_doc(100, submitting=True)
		submitted = self.run_gl(submit_doc)
		key = lambda e: (e.account, e.get("debit", 0), e.get("credit", 0))  # noqa: E731
		self.assertEqual(sorted(map(key, reposted)), sorted(map(key, submitted)))
		self.assertNotIn("Stock Adjustment - CD", [e.account for e in reposted])

	def test_higher_stock_value_on_repost_never_writes_the_asset(self):
		doc, output = make_doc(120)
		entries = self.run_gl(doc, {})
		self.get_doc.assert_not_called()
		output.db_set.assert_not_called()
		self.assertEqual(output.asset_value, 100)
		by_account = {e.account: e for e in entries}
		self.assertEqual(by_account["Stock - CD"].credit, 120)
		self.assertEqual(by_account["Fixed Asset - CD"].debit, 100)
		self.assertEqual(by_account["Stock Adjustment - CD"].debit, 20)
		self.assert_balanced(entries)

	def test_lower_stock_value_on_repost_credits_stock_adjustment(self):
		doc, output = make_doc(90)
		entries = self.run_gl(doc, {})
		output.db_set.assert_not_called()
		by_account = {e.account: e for e in entries}
		self.assertEqual(by_account["Stock Adjustment - CD"].credit, 10)
		self.assertEqual(by_account["Stock Adjustment - CD"].debit, 0)
		self.assert_balanced(entries)

	def test_no_stock_adjustment_account_leaves_the_posted_ledger_and_logs(self):
		posted = [
			frappe._dict(account="Stock - CD", debit=0, credit=100, cost_center="CC"),
			frappe._dict(account="Fixed Asset - CD", debit=100, credit=0, cost_center="CC"),
		]
		doc, output = make_doc(120)
		entries = self.run_gl(doc, {}, adjustment_account=None, posted=posted)
		self.log_error.assert_called_once()
		self.get_doc.assert_not_called()
		output.db_set.assert_not_called()
		self.assertEqual(
			[(e.account, e.debit, e.credit) for e in entries],
			[(e.account, e.debit, e.credit) for e in posted],
		)

	def test_submit_still_balances_and_saves_the_last_asset(self):
		doc, output = make_doc(120, submitting=True)
		entries = self.run_gl(doc)
		output.db_set.assert_called_once_with("asset_value", 120)
		self.get_doc.assert_called_once_with("Asset", "ASSET-1")
		self.get_doc.return_value.save.assert_called_once_with(ignore_permissions=True)
		self.assertNotIn("Stock Adjustment - CD", [e.account for e in entries])
		self.assert_balanced(entries)

	def test_on_submit_marks_submitting_only_around_its_own_gl(self):
		conversion = module()
		doc = Mock(pick_list=None)
		doc.flags = frappe._dict()
		seen = []
		doc.make_gl_entries.side_effect = lambda: seen.append(doc.flags.submitting)
		conversion.StocktoAssetConversion.on_submit(doc)
		self.assertEqual(seen, [True])
		self.assertFalse(doc.flags.submitting)
		doc.submit_generated_assets.assert_called_once()


if __name__ == "__main__":
	unittest.main()
