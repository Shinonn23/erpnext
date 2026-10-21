"""On-time and late shipments of the Supplier Scorecard: an item received in full on or before its Required By date
(the order item's schedule date) is on time, one received after it is late.
Offline: the variables' own SQL runs on an in-memory SQLite copy of the four tables it reads."""

import re
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from erpnext.buying.doctype.supplier_scorecard_variable import supplier_scorecard_variable as variables

TABLES = {
	"tabPurchase Order": ["name", "supplier", "docstatus"],
	"tabPurchase Order Item": [
		"name",
		"parent",
		"schedule_date",
		"qty",
		"received_qty",
		"base_amount",
		"docstatus",
	],
	"tabPurchase Receipt": ["name", "posting_date", "docstatus"],
	"tabPurchase Receipt Item": ["name", "parent", "purchase_order_item", "qty", "base_amount", "docstatus"],
}
REQUIRED_BY = "2026-09-15"
# order item, received on, amount: two early, one on the day, one late, one not received yet
RECEIPTS = [
	("POI-EARLY-1", "2026-09-10", 1000),
	("POI-EARLY-2", "2026-09-14", 2000),
	("POI-ON-THE-DAY", REQUIRED_BY, 4000),
	("POI-LATE", "2026-09-20", 8000),
	("POI-OPEN", None, 16000),
]
SCORECARD = SimpleNamespace(supplier="SUP-1", start_date="2026-09-01", end_date="2026-09-30")


class TestShipmentDates(unittest.TestCase):
	def setUp(self):
		self.db = sqlite3.connect(":memory:")
		for table, columns in TABLES.items():
			self.db.execute(f"create table `{table}` ({', '.join(columns)})")
		self.db.execute("insert into `tabPurchase Order` values ('PO-1', 'SUP-1', 1)")
		for item, received_on, amount in RECEIPTS:
			self.db.execute(
				"insert into `tabPurchase Order Item` values (?, 'PO-1', ?, 10, ?, ?, 1)",
				(item, REQUIRED_BY, 10 if received_on else 0, amount),
			)
			if received_on:
				receipt = f"PR-{item}"
				self.db.execute("insert into `tabPurchase Receipt` values (?, ?, 1)", (receipt, received_on))
				self.db.execute(
					"insert into `tabPurchase Receipt Item` values (?, ?, ?, 10, ?, 1)",
					(f"{receipt}-1", receipt, item, amount),
				)
		for target in (
			patch.object(
				variables.frappe, "get_doc", side_effect=lambda doctype, name: SimpleNamespace(name=name)
			),
			patch.object(variables.frappe, "db", SimpleNamespace(sql=self.sql)),
		):
			target.start()
			self.addCleanup(target.stop)

	def sql(self, query, values=None, as_dict=0):
		return self.db.execute(re.sub(r"%\((\w+)\)s", r":\1", query), values or {}).fetchall()

	def test_items_received_on_or_before_required_by_are_on_time(self):
		self.assertEqual(variables.get_total_shipments(SCORECARD), 5)
		self.assertEqual(variables.get_on_time_shipments(SCORECARD), 3)

	def test_items_received_after_required_by_or_not_yet_are_late(self):
		self.assertEqual(variables.get_late_shipments(SCORECARD), 2)

	def test_the_cost_of_on_time_and_delayed_shipments_agrees(self):
		self.assertEqual(variables.get_cost_of_on_time_shipments(SCORECARD), 1000 + 2000 + 4000)
		self.assertEqual(variables.get_cost_of_delayed_shipments(SCORECARD), 8000 + 16000)


if __name__ == "__main__":
	unittest.main()
