# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""A customer payment is credited to the customer's Territory; each invoice keeps its own stamp."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry

SALES_PERSON = "erpnext.setup.doctype.sales_person.sales_person."


class Doc(SimpleNamespace):
	set_territory_and_sales_person = PaymentEntry.set_territory_and_sales_person

	def get(self, key, default=None):
		return getattr(self, key, default)

	def get_doc_before_save(self):
		return self.previous


class TestPaymentEntryTerritory(unittest.TestCase):
	def setUp(self):
		self.customer_territory = "East"
		self.stamps = {}  # (doctype, name) -> stamp of the document being paid
		db = MagicMock()

		def get_value(doctype, name, fields=None, as_dict=False):
			if doctype == "Customer":
				return self.customer_territory
			stamp = self.stamps.get((doctype, name))
			return frappe._dict(stamp) if stamp else None

		db.get_value.side_effect = get_value
		for patcher in (
			patch.object(frappe, "db", new=db),
			patch(SALES_PERSON + "get_territory_manager", new={"East": "Alice"}.get),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def entry(self, references=(), party_type="Customer", previous=None):
		rows = [
			frappe._dict(reference_doctype=doctype, reference_name=name, territory="X", sales_person="X")
			for doctype, name in references
		]
		return Doc(party_type=party_type, party="CUST", references=rows, previous=previous)

	def test_header_takes_the_customer_territory_and_its_manager(self):
		doc = self.entry()
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), ("East", "Alice"))

	def test_each_invoice_keeps_its_own_stamp(self):
		self.stamps[("Sales Invoice", "SI-1")] = {"territory": "West", "sales_person": "Bob"}
		self.stamps[("Sales Invoice", "SI-2")] = {"territory": "East", "sales_person": "Carol"}
		doc = self.entry([("Sales Invoice", "SI-1"), ("Sales Invoice", "SI-2")])
		doc.set_territory_and_sales_person()
		self.assertEqual(
			[(row.territory, row.sales_person) for row in doc.references],
			[("West", "Bob"), ("East", "Carol")],
		)
		self.assertEqual((doc.territory, doc.sales_person), ("East", "Alice"))

	def test_references_without_a_stamp_use_the_header(self):
		self.stamps[("Sales Order", "SO-1")] = {"territory": None, "sales_person": None}
		doc = self.entry([("Sales Order", "SO-1"), ("Journal Entry", "JV-1"), ("Sales Invoice", "SI-9")])
		doc.set_territory_and_sales_person()
		self.assertEqual({(row.territory, row.sales_person) for row in doc.references}, {("East", "Alice")})

	def test_supplier_payment_has_no_territory(self):
		doc = self.entry([("Purchase Invoice", "PI-1")], party_type="Supplier")
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), (None, None))
		self.assertEqual((doc.references[0].territory, doc.references[0].sales_person), (None, None))

	def test_saved_stamp_survives_a_change_of_territory_manager(self):
		previous = SimpleNamespace(get=lambda key: {"territory": "East", "sales_person": "Carol"}.get(key))
		doc = self.entry(previous=previous)
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.sales_person, "Carol")
