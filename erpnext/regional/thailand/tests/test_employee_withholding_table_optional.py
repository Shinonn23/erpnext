"""An Employee payment withholds on its header category, so the categories table is not required for it.

e2e srv1110e AC-EMP-WHT-01 (10 Nov 2026): the form refused "Tax Withholding Categories is required" for an Employee
payment, although the Withhold Tax popup never fills that table for an Employee (payment_entry.py
get_payment_entry_tax_withholding_categories refuses invoice lines for Employees).
"""

import json
import unittest
from pathlib import Path

PAYMENT_ENTRY = Path(__file__).parents[3] / "accounts" / "doctype" / "payment_entry" / "payment_entry.json"


class TestEmployeeWithholdingTable(unittest.TestCase):
	def test_categories_table_is_required_only_for_a_supplier_withholding(self):
		fields = {f["fieldname"]: f for f in json.loads(PAYMENT_ENTRY.read_text())["fields"]}
		condition = fields["tax_withholding_categories"]["mandatory_depends_on"]
		self.assertIn("doc.apply_tds", condition)
		self.assertIn("doc.party_type != 'Employee'", condition)


if __name__ == "__main__":
	unittest.main()
