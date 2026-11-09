"""Share reports use native readable-record queries without changing share ranges or cost calculations."""

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.accounts.report.share_balance import share_balance as balance
from erpnext.accounts.report.share_ledger import share_ledger as ledger


def transfer(
	name,
	*,
	company="Company A",
	kind="Issue",
	to="HOLDER",
	source=None,
	first=1,
	last=100,
	rate=10,
	date="2026-06-01",
	creation=None,
	docstatus=1,
):
	return frappe._dict(
		name=name,
		company=company,
		transfer_type=kind,
		from_shareholder=source,
		to_shareholder=to,
		share_type="Equity",
		from_no=first,
		to_no=last,
		no_of_shares=last - first + 1,
		rate=rate,
		amount=(last - first + 1) * rate,
		date=date,
		creation=creation or date + " 12:00:00",
		docstatus=docstatus,
	)


class TestShareReportPermissions(TestCase):
	def setUp(self):
		self.holder = frappe._dict(name="HOLDER", is_company=0, company="Company A", check_permission=Mock())
		self.records = [
			transfer("ISSUE-1"),
			transfer(
				"OUT", kind="Transfer", source="HOLDER", to="OTHER", last=40, rate=15, date="2026-06-05"
			),
			transfer("ISSUE-2", first=101, last=200, rate=20, date="2026-06-10"),
			transfer("HIDDEN", first=201, last=300, rate=999, date="2026-06-11"),
			transfer("FOREIGN", company="Company B", first=301, last=400),
			transfer("DRAFT", docstatus=0),
			transfer("FUTURE", first=401, last=500, date="2026-07-01"),
			transfer("UNRELATED", source="OTHER", to="ANOTHER"),
		]
		self.visible_companies = {"Company A"}
		self.hidden_records = {"HIDDEN"}
		self.calls = []
		for module in (ledger, balance):
			self.enterContext(patch.object(module, "_", side_effect=lambda text: text))
		self.enterContext(patch.object(frappe, "session", frappe._dict(user="manager@example.test")))
		self.enterContext(patch.object(frappe, "get_doc", return_value=self.holder))
		self.enterContext(
			patch.object(frappe, "db", Mock(sql=Mock(side_effect=AssertionError("Raw SQL bypass"))))
		)
		self.enterContext(patch.object(frappe, "get_all", side_effect=AssertionError("Unscoped query")))
		# Exercise frappe.get_list and its actual QB execute signature, with only storage/permission results simulated.
		self.query = self.enterContext(
			patch(
				"frappe.model.qb_query.DatabaseQuery.execute",
				autospec=True,
				side_effect=self.execute_query,
			)
		)

	def execute_query(self, query, *args, **kwargs):
		self.assertEqual(query.doctype, "Share Transfer")
		self.assertFalse(kwargs.get("ignore_permissions", False))
		self.assertFalse(kwargs.get("ignore_user_permissions", False))
		self.assertEqual(kwargs.get("limit_page_length"), 0)
		self.assertEqual(kwargs["order_by"], "date asc, creation asc, name asc")
		self.calls.append(kwargs)

		def matches(row, field, expected):
			value = row.get(field)
			if isinstance(expected, list):
				operator, target = expected
				if operator == "<=":
					return value <= target
				if operator == "in":
					return value in target
				raise AssertionError(operator)
			return value == expected

		rows = [
			row
			for row in self.records
			if row.company in self.visible_companies
			and row.name not in self.hidden_records
			and all(matches(row, field, expected) for field, expected in kwargs.get("filters", {}).items())
			and (
				not kwargs.get("or_filters")
				or any(matches(row, field, expected) for field, expected in kwargs["or_filters"].items())
			)
		]
		rows.sort(key=lambda row: (row.date, row.creation, row.name))
		return [frappe._dict({field: row.get(field) for field in kwargs["fields"]}) for row in rows]

	def test_ledger_keeps_date_and_holder_filters_with_record_and_company_permissions(self):
		rows = ledger.get_all_transfers("2026-06-30", "HOLDER")
		self.assertEqual([row.name for row in rows], ["ISSUE-1", "OUT", "ISSUE-2"])
		self.assertEqual(
			self.calls[0]["or_filters"], {"from_shareholder": "HOLDER", "to_shareholder": "HOLDER"}
		)
		self.holder.check_permission.assert_called_once_with("read")

	def test_ledger_report_rows_keep_transfer_direction_and_original_values(self):
		_, rows = ledger.execute({"date": "2026-06-30", "shareholder": "HOLDER"})
		self.assertEqual([row[2] for row in rows], ["Issue", "Transfer to OTHER", "Issue"])
		self.assertEqual(rows[1][4:8], [40, 15, 600, "Company A"])
		self.assertEqual(rows[0][-1], "ISSUE-1")

	def test_selected_holder_read_denial_precedes_both_report_queries(self):
		self.holder.check_permission.side_effect = frappe.PermissionError
		for method in (ledger.get_all_transfers, lambda date, holder: balance.get_all_shares(holder, date)):
			with self.subTest(method=method), self.assertRaises(frappe.PermissionError):
				method("2026-06-30", "HOLDER")
		self.query.assert_not_called()

	def test_balance_math_ignores_inaccessible_transfers_and_keeps_received_cost(self):
		_, rows = balance.execute({"date": "2026-06-30", "shareholder": "HOLDER"})
		self.assertEqual(rows, [["HOLDER", "Equity", 160, 16.25, 2600]])
		self.assertEqual(len(self.calls), 1)
		self.assertEqual(balance.get_all_shares("HOLDER", "2026-05-01"), [])

	def test_company_holder_issue_purchase_merge_deduplicates_and_sorts(self):
		self.holder.update(name="COMPANY-HOLDER", is_company=1)
		self.records = [
			transfer(
				"BUYBACK", kind="Purchase", source="HOLDER", to=None, last=40, rate=15, date="2026-06-05"
			),
			transfer(
				"DIRECT",
				kind="Transfer",
				source="OTHER",
				to=self.holder.name,
				first=101,
				last=110,
				rate=5,
				date="2026-06-03",
			),
			transfer("BOTH", to=self.holder.name, first=201, last=210, rate=7, date="2026-06-02"),
			transfer("ISSUE-1"),
			transfer("HIDDEN", rate=999),
			transfer("FOREIGN", company="Company B", rate=999),
		]
		transfers = balance.get_transfers(self.holder, "2026-06-30")
		self.assertEqual([row.name for row in transfers], ["ISSUE-1", "BOTH", "DIRECT", "BUYBACK"])
		self.assertEqual(len(self.calls), 2)
		self.assertEqual(self.calls[1]["filters"]["transfer_type"], ["in", ["Issue", "Purchase"]])
		_, rows = balance.execute({"date": "2026-06-30", "shareholder": self.holder.name})
		self.assertEqual(rows, [[self.holder.name, "Equity", 80, 9, 720]])

	def test_explicit_company_filter_cannot_override_permission_scope_or_holder_branch(self):
		self.holder.update(is_company=1)
		self.assertEqual(balance.get_all_shares("HOLDER", "2026-06-30", "Company B"), [])
		self.assertEqual(len(self.calls), 1)
		self.assertEqual(self.calls[0]["filters"]["company"], "Company B")

	def test_backdated_outflow_and_latest_receipt_rate_math_are_unchanged(self):
		self.records = [
			transfer("OLD", rate=10, date="2026-06-10", creation="2026-06-10 10:00:00"),
			transfer("LATEST", rate=20, date="2026-06-10", creation="2026-06-10 11:00:00"),
			transfer(
				"OUT", kind="Transfer", source="HOLDER", to="OTHER", last=100, rate=99, date="2026-06-01"
			),
		]
		_, rows = balance.execute({"date": "2026-06-30", "shareholder": "HOLDER"})
		self.assertEqual(rows, [["HOLDER", "Equity", 100, 20, 2000]])
