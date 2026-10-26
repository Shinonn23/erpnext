"""Offline tests of the per-branch helpers of the Thai tax reports (no site, no database)."""

import logging
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import frappe

from erpnext.regional.report.pnd3 import pnd3
from erpnext.regional.report.purchase_tax_report import purchase_tax_report
from erpnext.regional.report.sales_tax_report import sales_tax_report
from erpnext.regional.thailand import tax_report_branch as trb

HO = "CD-Head Office"
B3 = "CD-Branch 3"
B4 = "CD-Branch 4"


def row(address, code, base=100, tax=7, name="INV"):
	return {
		"company_tax_address": address,
		"company_branch_code": code,
		"tax_base": base,
		"tax_amount": tax,
		"name": name,
	}


def fake_frappe():
	"""A stand-in for the frappe module inside tax_report_branch (the company currency is THB)."""
	fake = mock.MagicMock()
	fake.db.get_value.return_value = "THB"
	return fake


class TestBranchHelpers(unittest.TestCase):
	def test_branch_label(self):
		for code in ("", None, "-", "00000", " 00000 ", "0"):
			self.assertEqual(trb.branch_label(code), "Head Office")
		self.assertEqual(trb.branch_label("00003"), "Branch 00003")
		self.assertEqual(trb.branch_label("3"), "Branch 00003")

	def test_normalise_branch_code(self):
		self.assertEqual(trb.normalise_branch_code("3"), "00003")
		self.assertEqual(trb.normalise_branch_code(" 00004 "), "00004")
		self.assertEqual(trb.normalise_branch_code("-"), "-")
		self.assertEqual(trb.normalise_branch_code(None), "")

	def test_mixed_branch_detection(self):
		self.assertFalse(trb.is_mixed_branches([]))
		self.assertFalse(trb.is_mixed_branches([row(HO, "00000"), row(HO, "00000")]))
		self.assertTrue(trb.is_mixed_branches([row(HO, "00000"), row(B3, "00003")]))
		# a document without a tax address is the head office: with the head-office rows it is not mixed
		self.assertFalse(trb.is_mixed_branches([row(None, "00000"), row(HO, "00000")]))
		self.assertFalse(trb.is_mixed_branches([row("", ""), row(None, "00000")]))
		# but with the rows of a branch it is
		self.assertTrue(trb.is_mixed_branches([row(None, "00000"), row(B3, "00003")]))
		self.assertTrue(trb.is_mixed_branches([row(None, "00000"), row(HO, "00000"), row(B3, "00003")]))

	def test_needs_branch_choice(self):
		one = [row(HO, "00000")]
		self.assertFalse(trb.needs_branch_choice(one, None, 1))
		self.assertFalse(trb.needs_branch_choice(one, HO, 3))
		self.assertTrue(trb.needs_branch_choice(one, None, 3))
		self.assertTrue(trb.needs_branch_choice([row(HO, "00000"), row(B3, "00003")], HO, 3))
		# nothing to print and nothing selected: only the branch count decides
		self.assertFalse(trb.needs_branch_choice([], None, 0))

	def test_summary_has_one_card_per_branch_and_a_total(self):
		rows = [
			row(B3, "00003", 200, 14),
			row(HO, "00000", 100, 7),
			row(HO, "00000", 50, 3.5),
			row(B3, "00003", 0, 0),
		]
		cards = trb.branch_summary(rows, "THB")
		self.assertEqual(len(cards), 3)
		self.assertIn("Head Office", cards[0]["label"])
		self.assertEqual(cards[0]["value"], 10.5)
		self.assertIn("150.00", cards[0]["label"])
		self.assertIn("Branch 00003", cards[1]["label"])
		self.assertEqual(cards[1]["value"], 14)
		self.assertEqual(cards[2]["value"], 24.5)
		self.assertIn("350.00", cards[2]["label"])
		self.assertTrue(all(card["currency"] == "THB" for card in cards))

	def test_summary_puts_blank_address_rows_with_the_head_office(self):
		rows = [row(None, "00000", 40, 2.8), row(HO, "00000", 100, 7), row(B3, "00003", 10, 0.7)]
		cards = trb.branch_summary(rows, "THB", "WHT")
		self.assertEqual(len(cards), 3)
		self.assertEqual(cards[0]["value"], 9.8)
		self.assertIn("WHT (Tax Base 140.00)", cards[0]["label"])
		self.assertIn("Total WHT", cards[2]["label"])

	def test_summary_labels_escape_the_branch_code(self):
		cards = trb.branch_summary([row(B3, "<b>1")], None)
		self.assertNotIn("<b>", cards[0]["label"])

	def test_finish_normalises_branch_codes(self):
		rows, _summary = trb.finish_tax_report(
			[{"company_tax_address": B3, "company_branch_code": "3", "company_branch": None, "branch": "4"}],
			{"company_tax_address": B3},
		)
		self.assertEqual(rows[0]["company_branch_code"], "00003")
		self.assertEqual(rows[0]["company_branch"], "00000")
		self.assertEqual(rows[0]["branch"], "00004")

	def test_finish_flags_rows_and_summary_only_for_all_branches(self):
		rows = [row(HO, "00000"), row(B3, "00003")]
		with mock.patch.object(trb, "get_company_branch_count", return_value=2), mock.patch.object(
			trb, "frappe", fake_frappe()
		):
			flagged, summary = trb.finish_tax_report([dict(r) for r in rows], {"company": "CD"}, summary=True)
			self.assertTrue(all(r["filing_notice"] == 1 for r in flagged))
			self.assertEqual(len(summary), 3)

			single, summary = trb.finish_tax_report(
				[row(B3, "00003")], {"company": "CD", "company_tax_address": B3}, summary=True
			)
			self.assertEqual(single[0]["filing_notice"], 0)
			self.assertIsNone(summary)

		# no Company filter (an old caller): no branch count, no notice for one branch
		old, summary = trb.finish_tax_report([row(HO, "00000")], {}, summary=False)
		self.assertEqual(old[0]["filing_notice"], 0)
		self.assertIsNone(summary)


class TestReportExecute(unittest.TestCase):
	def run_execute(self, module, rows, filters, branch_count=2):
		with mock.patch.object(module, "get_data", return_value=rows) as get_data, mock.patch.object(
			trb, "get_company_branch_count", return_value=branch_count
		), mock.patch.object(trb, "frappe", fake_frappe()):
			result = module.execute(filters)
		return result, get_data

	def test_sales_all_branches_returns_summary_and_notice(self):
		rows = [row(HO, "00000"), row(B3, "00003")]
		(columns, data, message, chart, summary), _get_data = self.run_execute(
			sales_tax_report, rows, {"company": "CD"}
		)
		self.assertEqual(len(summary), 3)
		self.assertEqual(data[0]["filing_notice"], 1)
		self.assertEqual(len(data), 2)  # no subtotal rows added

	def test_sales_selected_branch_has_no_summary(self):
		(_c, data, _m, _ch, summary), _get_data = self.run_execute(
			sales_tax_report, [row(B3, "00003")], {"company": "CD", "company_tax_address": B3}
		)
		self.assertIsNone(summary)
		self.assertEqual(data[0]["filing_notice"], 0)

	def test_old_caller_without_company(self):
		(_c, data, _m, _ch, _s), get_data = self.run_execute(
			purchase_tax_report, [row(HO, "00000")], None, branch_count=0
		)
		self.assertEqual(data[0]["filing_notice"], 0)
		self.assertEqual(get_data.call_args.args[0].get("company"), None)

	def test_pnd_uses_company_address_filter_as_the_branch(self):
		rows = [row(B3, "00003")]
		(_c, data, _m, _ch, summary), _get_data = self.run_execute(
			pnd3, rows, {"company": "CD", "company_address": B3}
		)
		self.assertEqual(data[0]["filing_notice"], 0)
		self.assertIsNone(summary)

		rows = [row(HO, "00000"), row(B4, "00004")]
		(_c, data, _m, _ch, summary), _get_data = self.run_execute(pnd3, rows, {"company": "CD"})
		self.assertEqual(data[0]["filing_notice"], 1)
		self.assertEqual(len(summary), 3)

	def test_pnd_has_our_branch_column_and_wht_summary(self):
		for module in (pnd3,):
			self.assertIn("company_branch_code", [c.get("fieldname") for c in module.get_columns()])
		rows = [row(HO, "00000", 100, 3), row(B3, "00003", 200, 6)]
		(_c, _d, _m, _ch, summary), _get_data = self.run_execute(pnd3, rows, {"company": "CD"})
		self.assertEqual(len(summary), 3)
		self.assertIn("WHT", summary[2]["label"])

	def test_blank_address_row_next_to_head_office_does_not_block_the_print(self):
		rows = [row(None, "", name="OLD"), row(HO, "00000")]
		(_c, data, _m, _ch, summary), _get_data = self.run_execute(
			sales_tax_report, rows, {"company": "CD", "company_tax_address": HO}, branch_count=1
		)
		self.assertEqual([r["filing_notice"] for r in data], [0, 0])
		self.assertEqual(data[0]["company_branch_code"], "00000")
		(_c, data, _m, _ch, summary), _get_data = self.run_execute(
			sales_tax_report, rows, {"company": "CD"}, branch_count=1
		)
		self.assertEqual(data[0]["filing_notice"], 0)
		self.assertEqual(len(summary), 2)  # one head-office card and the total

	def test_columns_keep_names_and_add_our_branch(self):
		for module in (sales_tax_report, purchase_tax_report):
			fieldnames = [c["fieldname"] for c in module.get_columns()]
			self.assertIn("company_tax_address", fieldnames)
			self.assertIn("company_branch_code", fieldnames)
			self.assertGreater(fieldnames.index("company_branch_code"), fieldnames.index("tax_id"))
			hidden = next(c for c in module.get_columns() if c["fieldname"] == "company_tax_address")
			self.assertEqual(hidden["hidden"], 1)


if __name__ == "__main__":
	unittest.main()


class TestReportSql(unittest.TestCase):
	"""get_data() against a stubbed database: the SQL it sends for each filter combination."""

	@classmethod
	def setUpClass(cls):
		cls.sites = tempfile.TemporaryDirectory()
		sites = Path(cls.sites.name)
		(sites / "apps.txt").write_text("frappe\n")
		(sites / "common_site_config.json").write_text("{}")
		(sites / "fake").mkdir()
		(sites / "fake" / "site_config.json").write_text("{}")
		cls.previous = getattr(frappe.local, "site", None)
		frappe.init("fake", sites_path=str(sites))

	@classmethod
	def tearDownClass(cls):
		frappe.destroy()
		cls.sites.cleanup()

	def sql(self, module, filters, branch_code="00000"):
		db = mock.MagicMock()
		db.sql.return_value = []
		db.get_value.return_value = branch_code
		# frappe opens its log files relative to the working directory: use no file handler
		with (
			mock.patch.object(frappe.local, "db", db, create=True),
			mock.patch("frappe.utils.logger.create_handler", return_value=[logging.NullHandler()]),
		):
			module.get_data(frappe._dict(filters))
		return " ".join(str(db.sql.call_args.args[0]).split())

	def test_sales_snapshot_expressions_and_all_branches_order(self):
		sql = self.sql(sales_tax_report, {"company": "CD", "filter_based_on": "Fiscal Year"})
		self.assertIn("coalesce(nullif(`tabSales Tax Invoice`.`company_branch_code`,", sql)
		self.assertIn("nullif(`tabSales Tax Invoice`.`company_tax_id`,", sql)
		self.assertIn("`tabSales Tax Invoice`.`company`=", sql)
		self.assertIn(
			"ORDER BY `tabSales Tax Invoice`.`company_tax_address`,`tabSales Tax Invoice`.`name`", sql
		)

	def test_sales_selected_branch_order_and_head_office_includes_blank(self):
		sql = self.sql(sales_tax_report, {"company": "CD", "company_tax_address": HO})
		self.assertIn("`company_tax_address` IS NULL", sql)
		self.assertTrue(sql.rstrip().endswith("ORDER BY `tabSales Tax Invoice`.`name`"), sql[-120:])
		sql = self.sql(sales_tax_report, {"company": "CD", "company_tax_address": B3}, "00003")
		self.assertNotIn("IS NULL", sql)

	def test_old_caller_has_no_company_condition(self):
		sql = self.sql(sales_tax_report, {})
		self.assertNotIn("`tabSales Tax Invoice`.`company`=", sql)

	def test_purchase_expressions_and_order(self):
		sql = self.sql(purchase_tax_report, {"company": "CD"})
		self.assertIn("coalesce(nullif(`tabPurchase Tax Invoice`.`company_branch_code`,", sql)
		self.assertIn("`tabPurchase Tax Invoice`.`company`=", sql)
		self.assertIn("ORDER BY `tabPurchase Tax Invoice`.`company_tax_address`,", sql)

	def test_pnd_expressions_and_filters(self):
		sql = self.sql(pnd3, {"company": "CD", "month": "10", "year": "2026"})
		self.assertIn("coalesce(nullif(`tabWithholding Tax Cert`.`supplier_tax_id`,", sql)
		self.assertIn("coalesce(nullif(`tabAddress`.`branch_code`,", sql)
		self.assertIn("`tabWithholding Tax Cert`.`company`=", sql)
		self.assertIn("ORDER BY `tabWithholding Tax Cert`.`company_address`,", sql)
		sql = self.sql(pnd3, {"company": "CD", "company_address": HO})
		self.assertIn("`company_address` IS NULL", sql)
