# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
# For license information, please see license.txt

"""A selling document sits in its customer's Territory and carries that Territory's Sales Person."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpnext.controllers import selling_controller
from erpnext.controllers.selling_controller import SellingController

MODULE = "erpnext.controllers.selling_controller."
SALES_PERSON = "erpnext.setup.doctype.sales_person.sales_person."


class Doc(SimpleNamespace):
	"""Stands in for a saved document and borrows the real controller methods under test."""

	set_territory_and_sales_person = SellingController.set_territory_and_sales_person
	get_territory_party = SellingController.get_territory_party
	get_party_territory = SellingController.get_party_territory
	get_territory_stamp = SellingController.get_territory_stamp
	get_stamp_source = SellingController.get_stamp_source
	set_sales_team_from_sales_person = SellingController.set_sales_team_from_sales_person

	def get(self, key, default=None):
		return getattr(self, key, default)

	def set(self, key, value):
		setattr(self, key, value)

	def append(self, key, row):
		getattr(self, key).append(frappe._dict(row))

	def is_new(self):
		return self.previous is None

	def calculate_contribution(self):
		self.priced_teams.append([(row.sales_person, row.commission_rate) for row in self.sales_team])

	def get_doc_before_save(self):
		return self.previous


class TestSellingTerritoryStamp(unittest.TestCase):
	def setUp(self):
		self.db = MagicMock()
		self.msgprint = MagicMock()
		self.managers = {"East": "Alice", "West": "Bob"}
		self.customer_territory = "East"
		self.own_territory = None
		self.stored = {}  # (doctype, name) -> {"territory": .., "sales_person": ..}

		def get_value(doctype, name, fields=None, as_dict=False, **kwargs):
			if doctype == "Customer":
				return self.customer_territory
			stored = self.stored.get((doctype, name))
			return frappe._dict(stored) if stored else None

		self.db.get_value.side_effect = get_value
		self.db.get_single_value.return_value = "Rest Of The World"
		for patcher in (
			patch.object(frappe, "db", new=self.db),
			patch.object(frappe, "session", new=SimpleNamespace(user="seller@example.com")),
			patch.object(frappe, "msgprint", new=self.msgprint),
			patch.object(
				frappe,
				"get_cached_value",
				new=lambda doctype, name, field: 2.5 if field == "commission_rate" else None,
			),
			patch(MODULE + "_", new=lambda text: text),
			patch(SALES_PERSON + "get_territory_manager", new=lambda territory: self.managers.get(territory)),
			patch(MODULE + "get_territory_of_user", new=lambda user: self.own_territory),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def doc(self, doctype="Sales Invoice", previous=None, **values):
		fields = {"territory", "sales_person", "sales_team", "amount_eligible_for_commission"}
		doc = Doc(
			doctype=doctype,
			customer="Customer",
			territory=None,
			sales_person=None,
			sales_team=[],
			items=[],
			previous=previous,
			flags=frappe._dict(),
			priced_teams=[],
			meta=SimpleNamespace(has_field=lambda fieldname: fieldname in fields),
		)
		doc.__dict__.update(values)
		return doc

	def stamped(self, doctype, name, territory, sales_person, customer="Customer"):
		self.stored[(doctype, name)] = {
			"territory": territory,
			"sales_person": sales_person,
			"customer": customer,
		}

	def test_territory_follows_the_customer_and_stamps_its_manager(self):
		doc = self.doc(territory="West", sales_person="Mallory")
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), ("East", "Alice"))
		self.assertEqual(
			[(row.sales_person, row.allocated_percentage) for row in doc.sales_team], [("Alice", 100)]
		)

	def test_territory_without_a_manager_leaves_the_sales_person_empty(self):
		self.customer_territory = "D02"
		doc = self.doc(sales_team=[frappe._dict(sales_person="Old", allocated_percentage=100)])
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), ("D02", None))
		self.assertEqual(doc.sales_team, [])

	def test_saved_stamp_survives_a_change_of_territory_manager(self):
		stamp = {"territory": "East", "sales_person": "Carol"}
		previous = SimpleNamespace(get=stamp.get, sales_person="Carol")
		doc = self.doc(previous=previous)
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.sales_person, "Carol")

	def test_saved_document_without_a_stamp_picks_up_the_manager(self):
		previous = SimpleNamespace(get=lambda key: {"territory": "East", "sales_person": None}.get(key))
		doc = self.doc(previous=previous)
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.sales_person, "Alice")

	def test_moved_territory_gets_the_new_manager(self):
		previous = SimpleNamespace(get=lambda key: {"territory": "West", "sales_person": "Bob"}.get(key))
		doc = self.doc(previous=previous)
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), ("East", "Alice"))

	def test_document_continues_the_stamp_of_the_one_it_comes_from(self):
		self.stamped("Sales Order", "SO-1", "East", "Carol", "Customer")
		doc = self.doc(items=[frappe._dict(sales_order="SO-1")])
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.sales_person, "Carol")

	def test_source_in_another_territory_is_not_inherited(self):
		self.stamped("Sales Order", "SO-1", "West", "Bob", "Customer")
		doc = self.doc(items=[frappe._dict(sales_order="SO-1")])
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.sales_person, "Alice")

	def test_amended_document_keeps_the_stamp_of_the_original(self):
		self.stored[("Sales Invoice", "SI-1")] = {"territory": "East", "sales_person": "Carol"}
		doc = self.doc(amended_from="SI-1")
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.sales_person, "Carol")

	def test_return_reverses_the_attribution_even_after_the_customer_moved(self):
		self.customer_territory = "West"
		self.stored[("Sales Invoice", "SI-1")] = {"territory": "East", "sales_person": "Carol"}
		doc = self.doc(return_against="SI-1")
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), ("East", "Carol"))
		self.assertEqual([row.sales_person for row in doc.sales_team], ["Carol"])

	def test_sales_person_without_a_customer_territory_uses_their_own(self):
		self.customer_territory = None
		self.own_territory = "West"
		doc = self.doc(territory="Rest Of The World")  # the Selling Settings default
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), ("West", "Bob"))

	def test_explicit_territory_is_kept_when_the_customer_has_none(self):
		self.customer_territory = None
		self.own_territory = "West"
		doc = self.doc(territory="East")
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), ("East", "Alice"))

	def test_new_document_for_another_sellers_customer_warns_but_is_allowed(self):
		self.own_territory = "West"
		doc = self.doc()
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.territory, "East")
		self.msgprint.assert_called_once()

	def test_own_customer_gives_no_warning(self):
		self.own_territory = "East"
		self.doc().set_territory_and_sales_person()
		self.msgprint.assert_not_called()

	def test_documents_without_a_sales_person_field_only_follow_the_customer(self):
		doc = self.doc()
		doc.meta = SimpleNamespace(has_field=lambda fieldname: fieldname == "territory")
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.territory, "East")
		self.assertIsNone(doc.sales_person)

	def test_quotation_to_a_lead_uses_the_lead_territory(self):
		self.customer_territory = "West"
		doc = self.doc("Quotation", customer=None, quotation_to="Lead", party_name="LEAD-1")
		doc.set_territory_and_sales_person()
		# Customer lookups are answered by the stub; a Lead must be asked as a Lead
		queried = [call.args[:2] for call in self.db.get_value.call_args_list]
		self.assertIn(("Lead", "LEAD-1"), queried)

	def test_consolidated_invoice_keeps_the_stamp_of_the_pos_invoices(self):
		self.customer_territory = "West"
		doc = self.doc()
		doc.flags.territory_stamp = ("East", "Carol")
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), ("East", "Carol"))
		self.assertEqual([row.sales_person for row in doc.sales_team], ["Carol"])

	def test_consolidated_invoice_of_unstamped_pos_invoices_derives_the_stamp(self):
		doc = self.doc()
		doc.flags.territory_stamp = (None, None)
		doc.set_territory_and_sales_person()
		self.assertEqual((doc.territory, doc.sales_person), ("East", "Alice"))

	def test_sales_team_row_is_priced_with_the_commission_rate_of_its_sales_person(self):
		doc = self.doc()
		doc.set_territory_and_sales_person()
		# the base validate priced the rows that were replaced, so the new one is priced again
		self.assertEqual(doc.priced_teams, [[("Alice", 2.5)]])

	def test_a_team_without_a_sales_person_is_still_priced(self):
		self.customer_territory = "D02"
		doc = self.doc()
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.priced_teams, [[]])

	def test_documents_without_commission_fields_are_not_priced(self):
		doc = self.doc()
		fields = ("territory", "sales_person", "sales_team")
		doc.meta = SimpleNamespace(has_field=lambda fieldname: fieldname in fields)
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.priced_teams, [])

	def test_another_customers_document_is_not_continued(self):
		self.stamped("Sales Order", "SO-1", "East", "Carol", "Other")
		doc = self.doc(items=[frappe._dict(sales_order="SO-1")])
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.sales_person, "Alice")

	def test_an_unstamped_source_does_not_hide_a_stamped_one(self):
		self.stamped("Sales Order", "SO-1", "East", None, "Customer")
		self.stamped("Delivery Note", "DN-1", "East", "Carol", "Customer")
		doc = self.doc(items=[frappe._dict(sales_order="SO-1", delivery_note="DN-1")])
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.sales_person, "Carol")

	def test_a_quotation_is_continued_without_a_customer_field(self):
		self.stored[("Quotation", "QTN-1")] = {"territory": "East", "sales_person": "Carol"}
		doc = self.doc("Sales Order", items=[frappe._dict(prevdoc_docname="QTN-1")])
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.sales_person, "Carol")

	def test_a_customer_in_the_default_territory_stays_there(self):
		# the default is a real choice when it is the customer's own Territory
		self.customer_territory = "Rest Of The World"
		self.own_territory = "West"
		doc = self.doc()
		doc.set_territory_and_sales_person()
		self.assertEqual(doc.territory, "Rest Of The World")
