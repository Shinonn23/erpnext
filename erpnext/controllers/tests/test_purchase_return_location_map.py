"""Offline checks for warehouse/location-aware purchase return accounting."""

import unittest

import frappe

from erpnext.controllers.sales_and_purchase_return import aggregate_purchase_flow_returns


class TestPurchaseReturnLocationMap(unittest.TestCase):
	def test_accepted_and_rejected_returns_stay_separate_in_one_warehouse(self):
		returned = aggregate_purchase_flow_returns(
			[
				frappe._dict(
					warehouse="QUARANTINE - CO",
					storage_location="QI Zone",
					qty=-2,
					stock_qty=-2,
					rejected_warehouse="QUARANTINE - CO",
					rejected_storage_location="Damage Zone",
					rejected_qty=-1,
					conversion_factor=1,
				)
			]
		)

		self.assertEqual(returned[("QUARANTINE - CO", "QI Zone")].qty, 2)
		self.assertEqual(returned[("QUARANTINE - CO", "QI Zone")].stock_qty, 2)
		self.assertEqual(returned[("QUARANTINE - CO", "Damage Zone")].qty, 1)
		self.assertEqual(returned[("QUARANTINE - CO", "Damage Zone")].stock_qty, 1)

	def test_missing_locations_keep_the_legacy_null_location_bucket(self):
		returned = aggregate_purchase_flow_returns(
			[frappe._dict(warehouse="MAIN - CO", qty=-3, stock_qty=-3, conversion_factor=1)]
		)

		self.assertEqual(returned[("MAIN - CO", None)].qty, 3)
