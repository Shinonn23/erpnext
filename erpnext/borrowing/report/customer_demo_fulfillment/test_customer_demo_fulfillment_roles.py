"""Customer Demo Fulfillment reads stock data, so only stock roles and System Manager may open it."""

import json
import unittest
from pathlib import Path

REPORT = Path(__file__).with_name("customer_demo_fulfillment.json")


class TestCustomerDemoFulfillmentRoles(unittest.TestCase):
	def test_roles_are_exactly_the_stock_roles_and_system_manager(self):
		roles = [row["role"] for row in json.loads(REPORT.read_text())["roles"]]
		self.assertEqual(sorted(roles), ["Stock Manager", "Stock User", "System Manager"])

	def test_report_is_owned_and_listed_by_borrowing(self):
		root = REPORT.parents[3]
		name = "Customer Demo Fulfillment"
		self.assertEqual(json.loads(REPORT.read_text())["module"], "Borrowing")
		stock = json.loads((root / "stock/sidebar/stock/stock.json").read_text())
		self.assertNotIn(name, [row.get("link_to") for row in stock["items"]])
		for path, field, heading_type in (
			("borrowing/sidebar/borrowings/borrowings.json", "items", "Section Break"),
			("borrowing/workspace/borrowings/borrowings.json", "links", "Card Break"),
		):
			rows = json.loads((root / path).read_text())[field]
			self.assertEqual([row.get("link_to") for row in rows].count(name), 1)
			position = next(index for index, row in enumerate(rows) if row.get("link_to") == name)
			heading = next(row for row in reversed(rows[:position]) if row["type"] == heading_type)
			self.assertEqual(heading["label"], "Reports")
