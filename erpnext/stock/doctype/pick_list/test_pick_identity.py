"""A Delivery Note row delivers what its Pick List row picked (UX WP2). Offline: mocks, no site."""

import unittest
from unittest.mock import patch

import frappe

from erpnext.stock.doctype.pick_list import pick_identity as module


def refuse(message, *args, **kwargs):
	raise frappe.ValidationError(message)


def row(**values):
	defaults = {
		"idx": 1,
		"item_code": "ITEM",
		"warehouse": "W",
		"storage_location": "L1",
		"batch_no": "",
		"serial_no": "",
		"serial_and_batch_bundle": "",
		"against_pick_list": "PL-1",
	}
	return frappe._dict({**defaults, **values})


def entry(batch_no=None, serial_no=None, storage_location=None, qty=-1):
	return frappe._dict(batch_no=batch_no, serial_no=serial_no, storage_location=storage_location, qty=qty)


class TestPickMismatch(unittest.TestCase):
	def setUp(self):
		self.bundles = {}
		self.addCleanup(patch.stopall)
		patch.object(module, "bundle_entries", side_effect=lambda name: self.bundles.get(name, [])).start()
		patch.object(frappe, "throw", side_effect=refuse).start()

	def test_the_same_place_batch_and_serials_pass(self):
		pick = row(batch_no="B1", serial_no="S1\nS2")
		self.assertIsNone(module.pick_mismatch(row(batch_no="B1", serial_no="S1\nS2"), pick))

	def test_a_part_of_the_picked_serials_passes(self):
		self.assertIsNone(module.pick_mismatch(row(serial_no="S2"), row(serial_no="S1\nS2")))

	def test_another_warehouse_or_place_is_refused(self):
		self.assertIn("warehouse", module.pick_mismatch(row(warehouse="W2"), row()))
		self.assertIn("Storage Location", module.pick_mismatch(row(storage_location="L2"), row()))
		self.assertIn("Storage Location", module.pick_mismatch(row(storage_location=""), row()))

	def test_another_batch_or_serial_is_refused(self):
		self.assertIn("B2", module.pick_mismatch(row(batch_no="B2"), row(batch_no="B1")))
		self.assertIn("S9", module.pick_mismatch(row(serial_no="S9"), row(serial_no="S1")))

	def test_a_batch_or_serial_the_pick_names_cannot_be_left_empty(self):
		self.assertIsNotNone(module.pick_mismatch(row(), row(batch_no="B1")))
		self.assertIsNotNone(module.pick_mismatch(row(batch_no="B1"), row(batch_no="B1", serial_no="S1")))

	def test_a_row_that_names_nothing_takes_the_bundle_of_the_pick_on_submit(self):
		self.bundles["PB"] = [entry("B1"), entry("B2")]
		self.assertIsNone(module.pick_mismatch(row(), row(serial_and_batch_bundle="PB")))

	def test_bundle_entries_are_compared_part_by_part(self):
		self.bundles["PB"] = [entry("B1", "S1"), entry("B2", "S2", "L9")]
		pick = row(serial_and_batch_bundle="PB")
		self.bundles["DB"] = [entry("B2", "S2", "L9")]
		self.assertIsNone(module.pick_mismatch(row(serial_and_batch_bundle="DB"), pick))
		self.bundles["DB"] = [entry("B1", "S2", "L9")]
		self.assertIn("Batch No", module.pick_mismatch(row(serial_and_batch_bundle="DB"), pick))
		self.bundles["DB"] = [entry("B2", "S2", "L1")]
		self.assertIn("Storage Location", module.pick_mismatch(row(serial_and_batch_bundle="DB"), pick))

	def test_nothing_picked_by_batch_means_nothing_delivered_by_batch(self):
		self.assertIsNone(module.pick_mismatch(row(), row()))
		self.assertIsNotNone(module.pick_mismatch(row(batch_no="B1"), row()))

	def test_refuse_names_the_row_and_the_pick(self):
		with self.assertRaises(frappe.ValidationError) as caught:
			module.refuse_mismatch(row(idx=3, batch_no="B2"), row(batch_no="B1"))
		self.assertIn("Row 3", str(caught.exception))
		self.assertIn("PL-1", str(caught.exception))


if __name__ == "__main__":
	unittest.main()
