"""Territory stamps are server-derived and retain their historical attribution."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry


class TestTerritorySalesPerson(TestCase):
	def setUp(self):
		self.enterContext(
			patch("erpnext.accounts.doctype.journal_entry.journal_entry._", side_effect=lambda text: text)
		)
		self.enterContext(patch.object(frappe, "throw", side_effect=ValueError))
		self.lookup = self.enterContext(
			patch.object(frappe, "get_cached_value", return_value="Current Seller")
		)

	def row(self, **values):
		row = frappe._dict(
			name="ROW",
			account="Expense",
			territory="East",
			sales_person="Historical Seller",
			account_currency="THB",
			exchange_rate=1,
			debit=10,
			credit=0,
			debit_in_account_currency=10,
			credit_in_account_currency=0,
		)
		row.update(values)
		return row

	def stamp(self, rows, previous=None, reversal_of=None, **flags):
		doc = SimpleNamespace(
			accounts=rows,
			company="CD",
			reversal_of=reversal_of,
			flags=frappe._dict(flags),
			get_doc_before_save=lambda: previous,
		)
		JournalEntry.set_territory_sales_person(doc)

	def reversed_row(self, row):
		result = frappe._dict(row)
		for debit, credit in (
			("debit", "credit"),
			("debit_in_account_currency", "credit_in_account_currency"),
		):
			result[debit], result[credit] = row[credit], row[debit]
		return result

	def source(self, rows):
		return SimpleNamespace(accounts=rows, company="CD", docstatus=1, check_permission=Mock())

	def test_new_row_overwrites_client_stamp_and_blank_territory_clears_it(self):
		row, blank = (
			self.row(sales_person="Forged"),
			self.row(name="BLANK", territory=None, sales_person="Forged"),
		)
		self.stamp([row, blank])
		self.assertEqual(row.sales_person, "Current Seller")
		self.assertIsNone(blank.sales_person)
		self.lookup.assert_called_once_with("Territory", "East", "territory_manager")

	def test_unassigned_territory_remains_optional(self):
		self.lookup.return_value = None
		row = self.row(territory="Cash")
		self.stamp([row])
		self.assertIsNone(row.sales_person)

	def test_unchanged_territory_preserves_persisted_stamp_including_blank(self):
		for saved in ("Historical Seller", None):
			with self.subTest(saved=saved):
				original = self.row(sales_person=saved)
				current = self.row(sales_person="Forged or refreshed from master")
				self.stamp([current], previous=SimpleNamespace(accounts=[original]))
				self.assertEqual(current.sales_person, saved)
		self.lookup.assert_not_called()

	def test_changed_territory_fetches_new_manager_and_unrelated_row_ids_cannot_borrow_stamps(self):
		previous = SimpleNamespace(accounts=[self.row()])
		changed = self.row(territory="West")
		unrelated = self.row(name="OTHER-DOCUMENT-ROW")
		self.stamp([changed, unrelated], previous=previous)
		self.assertEqual(changed.sales_person, "Current Seller")
		self.assertEqual(unrelated.sales_person, "Current Seller")
		self.assertEqual(self.lookup.call_count, 2)

	def test_partial_reordered_reversal_retains_each_original_stamp(self):
		rows = [self.row(sales_person=seller) for seller in ("Seller A", "Seller B", None)]
		source = self.source(rows)
		reversal = [self.reversed_row(rows[2]), self.reversed_row(rows[1])]
		with patch.object(frappe, "get_doc", return_value=source):
			self.stamp(reversal, reversal_of="SOURCE")
		self.assertEqual([row.sales_person for row in reversal], [None, "Seller B"])
		source.check_permission.assert_called_once_with("read")
		self.lookup.assert_not_called()

	def test_reversal_rejects_forged_stamp_and_reusing_a_source_row(self):
		original = self.row()
		with patch.object(frappe, "get_doc", return_value=self.source([original])):
			for rows in (
				[self.reversed_row(original) | {"sales_person": "Forged"}],
				[self.reversed_row(original), self.reversed_row(original)],
				[self.reversed_row(original) | {"debit_in_account_currency": 1}],
			):
				with self.subTest(rows=rows), self.assertRaises(ValueError):
					self.stamp(rows, reversal_of="SOURCE")

	def test_reversal_checks_source_status_company_and_read_permission(self):
		original = self.row()
		for field, value in (("docstatus", 0), ("company", "OTHER")):
			with self.subTest(field=field):
				source = self.source([original])
				setattr(source, field, value)
				with patch.object(frappe, "get_doc", return_value=source), self.assertRaises(ValueError):
					self.stamp([self.reversed_row(original)], reversal_of="SOURCE")
		source = self.source([original])
		source.check_permission.side_effect = frappe.PermissionError
		with patch.object(frappe, "get_doc", return_value=source), self.assertRaises(frappe.PermissionError):
			self.stamp([self.reversed_row(original)], reversal_of="SOURCE")

	def test_server_built_entry_keeps_the_stamp_of_its_source(self):
		row = self.row(name="NEW", sales_person="Historical Seller")
		self.stamp([row], previous=SimpleNamespace(accounts=[]), preserve_dimension_stamps=True)
		self.assertEqual(row.sales_person, "Historical Seller")
		self.lookup.assert_not_called()

	def test_server_built_entry_without_a_stamp_stays_unassigned(self):
		row = self.row(name="NEW", sales_person=None)
		self.stamp([row], previous=SimpleNamespace(accounts=[]), preserve_dimension_stamps=True)
		self.assertIsNone(row.sales_person)

	def test_preserve_flag_never_keeps_a_stamp_without_a_territory(self):
		row = self.row(name="NEW", territory=None)
		self.stamp([row], previous=SimpleNamespace(accounts=[]), preserve_dimension_stamps=True)
		self.assertIsNone(row.sales_person)

	def test_partial_reconciliation_of_a_reversal_keeps_the_split_rows_and_their_stamps(self):
		# reconciliation splits a row, so the rows no longer mirror the entry that was reversed
		first = self.row(name="A", debit=6, debit_in_account_currency=6)
		split = self.row(name="B", debit=4, debit_in_account_currency=4, sales_person="Historical Seller")
		previous = SimpleNamespace(accounts=[self.row(name="A")])
		self.stamp([first, split], previous=previous, reversal_of="JV-1", preserve_dimension_stamps=True)
		self.assertEqual((first.sales_person, split.sales_person), ("Historical Seller", "Historical Seller"))
