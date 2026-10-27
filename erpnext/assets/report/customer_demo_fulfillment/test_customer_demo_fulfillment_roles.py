"""Customer Demo Fulfillment reads stock data, so only stock roles and System Manager may open it."""

import json
import unittest
from pathlib import Path

REPORT = Path(__file__).with_name("customer_demo_fulfillment.json")


class TestCustomerDemoFulfillmentRoles(unittest.TestCase):
	def test_roles_are_exactly_the_stock_roles_and_system_manager(self):
		roles = [row["role"] for row in json.loads(REPORT.read_text())["roles"]]
		self.assertEqual(sorted(roles), ["Stock Manager", "Stock User", "System Manager"])
