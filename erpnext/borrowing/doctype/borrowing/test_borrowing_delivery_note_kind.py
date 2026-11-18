"""A Borrowing / Demo Delivery Note is its own kind of document (UX WP3). Offline: mocks, no site."""

import json
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import frappe

from erpnext.borrowing.doctype.borrowing import borrowing_core as core

STOCK = Path(__file__).resolve().parents[3] / "stock" / "doctype"


def refuse(message, *args, **kwargs):
	raise frappe.ValidationError(message)


LINKS = ("taxes_and_charges", "tax_category", "shipping_rule", "sales_partner")


class Meta:
	def get_field(self, fieldname):
		return frappe._dict(fieldtype="Link" if fieldname in LINKS else "Currency")

	def get_label(self, fieldname):
		return fieldname.replace("_", " ").title()


class Doc(frappe._dict):
	"""A Delivery Note as the helpers read it: get / set, is_new, the stored version."""

	meta = Meta()

	def set(self, key, value):
		self[key] = value

	def is_new(self):
		return self.get("__new", False)

	def get_doc_before_save(self):
		return self.get("__before")

	@property
	def flags(self):
		return self.setdefault("__flags", frappe._dict())


def note(**values):
	return Doc({"is_borrowing_transaction": 1, "items": [], "taxes": [], "sales_team": [], **values})


class TestBorrowingSellingFields(unittest.TestCase):
	def setUp(self):
		self.addCleanup(patch.stopall)
		patch.object(frappe, "throw", side_effect=refuse).start()
		patch.object(core, "_", side_effect=lambda text: text).start()

	def test_a_tax_template_typed_into_a_saved_borrowing_note_is_refused(self):
		doc = note(taxes_and_charges="Thailand Tax - CD", __before=note())
		with self.assertRaises(frappe.ValidationError) as caught:
			core.prepare_borrowing_delivery_note(doc)
		self.assertIn("Taxes And Charges", str(caught.exception))

	def test_a_sales_team_row_typed_is_refused(self):
		doc = note(sales_team=[frappe._dict(sales_person="S1", allocated_percentage=100)], __before=note())
		with self.assertRaises(frappe.ValidationError):
			core.prepare_borrowing_delivery_note(doc)

	def test_a_stored_value_the_system_filled_is_not_refused_and_pricing_is_off(self):
		doc = note(taxes_and_charges="T", __before=note(taxes_and_charges="T"))
		core.prepare_borrowing_delivery_note(doc)
		self.assertEqual(doc.ignore_pricing_rule, 1)
		self.assertTrue(doc.flags.dont_auto_add_taxes)

	def test_a_new_note_is_never_refused(self):
		doc = note(__new=True, taxes_and_charges="T")
		core.prepare_borrowing_delivery_note(doc)
		self.assertEqual(doc.ignore_pricing_rule, 1)

	def test_the_system_fill_is_emptied_after_validate(self):
		row = Doc(price_list_rate=140, discount_percentage=65.7, discount_amount=92, rate=48)
		doc = note(
			taxes_and_charges="Thailand Tax - CD",
			taxes=[frappe._dict(account_head="2135-00", tax_amount=16.8)],
			sales_team=[frappe._dict(sales_person="S1")],
			sales_partner="P",
			discount_amount=10,
			items=[row],
		)
		self.assertTrue(core.clear_borrowing_selling_fields(doc))
		self.assertIsNone(doc.taxes_and_charges)
		self.assertEqual(doc.taxes, [])
		self.assertEqual(doc.sales_team, [])
		self.assertIsNone(doc.sales_partner)
		self.assertEqual(doc.discount_amount, 0)
		self.assertEqual((row.price_list_rate, row.discount_percentage, row.discount_amount), (0, 0, 0))
		self.assertEqual(row.rate, 48, "the valuation rate of the transfer stays")
		self.assertFalse(core.clear_borrowing_selling_fields(doc), "nothing left to empty")

	def test_the_recipient_goes_on_the_delivery_note(self):
		doc = note()
		loan = frappe._dict(
			recipient_address="ADDR-1",
			recipient_address_display="1 Road",
			recipient_contact="CON-1",
			recipient_contact_display="Khun A",
			recipient_contact_email="a@example.com",
		)
		core.set_borrowing_recipient(doc, loan)
		self.assertEqual((doc.shipping_address_name, doc.contact_person), ("ADDR-1", "CON-1"))
		self.assertEqual(doc.shipping_address, "1 Road")


class TestBorrowingStatus(unittest.TestCase):
	def status(self, **values):
		from erpnext.stock.doctype.delivery_note.delivery_note import DeliveryNote

		doc = MagicMock(spec=DeliveryNote)
		doc.get.side_effect = lambda key, default=None: values.get(key, default)
		with patch(
			"erpnext.controllers.selling_controller.SellingController.get_status",
			return_value={"status": values["native"]},
		):
			return DeliveryNote.get_status(doc)["status"]

	def test_a_borrowing_note_is_never_to_bill(self):
		self.assertEqual(self.status(is_borrowing_transaction=1, native="To Bill"), "Completed")
		self.assertEqual(self.status(is_borrowing_transaction=1, native="Partially Billed"), "Completed")

	def test_its_other_statuses_and_a_sale_stay(self):
		self.assertEqual(self.status(is_borrowing_transaction=1, native="Return Issued"), "Return Issued")
		self.assertEqual(self.status(is_borrowing_transaction=1, native="Closed"), "Closed")
		self.assertEqual(self.status(is_borrowing_transaction=0, native="To Bill"), "To Bill")


class TestBorrowingMakersRefused(unittest.TestCase):
	def setUp(self):
		self.addCleanup(patch.stopall)
		patch.object(frappe, "throw", side_effect=refuse).start()
		patch.object(frappe, "db", MagicMock(get_value=MagicMock(return_value=1)), create=True).start()

	def test_no_selling_document_is_made_from_a_borrowing_note(self):
		from erpnext.stock.doctype.delivery_note import delivery_note as dn

		for maker in (
			dn.make_delivery_trip,
			dn.make_installation_note,
			dn.make_packing_slip,
			dn.make_inter_company_purchase_receipt,
		):
			with self.subTest(maker.__name__), self.assertRaises(frappe.ValidationError):
				maker("LO6911001")


class TestNotToBeBilled(unittest.TestCase):
	"""'Delivered Items To Be Billed' never lists a Borrowing Delivery Note; 'Received Items' is unchanged."""

	def query(self, doctype, party):
		import pypika.queries
		from frappe.query_builder.builder import MariaDB

		from erpnext.accounts.report import non_billed_report as report

		args = {
			"doctype": doctype,
			"party": party,
			"date": "posting_date",
			"order": "name",
			"order_by": None,
			"reference_field": "name",
		}
		with (
			patch.object(frappe, "qb", MariaDB, create=True),
			patch.object(frappe, "get_meta", MagicMock(), create=True),
			patch.object(report, "get_field_precision", return_value=2),
			patch.object(report, "get_default_currency", return_value="THB"),
			patch.object(pypika.queries.QueryBuilder, "run", lambda self, **kw: str(self), create=True),
		):
			return report.get_ordered_to_be_billed_data(args, {"company": "C", "posting_date": "2026-11-19"})

	def test_a_borrowing_note_is_never_to_be_billed(self):
		self.assertIn(
			"IFNULL(`tabDelivery Note`.`is_borrowing_transaction`,0)=0",
			self.query("Delivery Note", "customer"),
		)
		self.assertNotIn("is_borrowing_transaction", self.query("Purchase Receipt", "supplier"))


class TestBorrowingLayout(unittest.TestCase):
	"""The form follows the kind: the fork JSON hides the selling parts of a Borrowing Delivery Note."""

	@staticmethod
	def fields(doctype):
		data = json.loads((STOCK / doctype / f"{doctype}.json").read_text())
		return {field["fieldname"]: field for field in data["fields"]}, data["field_order"]

	def test_the_selling_sections_are_hidden_on_a_borrowing_note(self):
		fields, _order = self.fields("delivery_note")
		for name in (
			"customer_po_details",
			"currency_and_price_list",
			"pricing_rule_details",
			"taxes_section",
			"sec_tax_breakup",
			"section_break_49",
			"totals_section",
			"base_totals_section",
			"sales_team_section_break",
			"section_break1",
			"terms_tab",
			"utm_analytics_section",
			"auto_repeat_section",
			"customer",
			"customer_name",
			"tax_id",
		):
			with self.subTest(name):
				self.assertIn("!doc.is_borrowing_transaction", fields[name]["depends_on"])

	def test_the_borrowing_section_shows_the_borrowing(self):
		fields, order = self.fields("delivery_note")
		self.assertEqual(fields["borrowing_section"]["depends_on"], "eval:doc.is_borrowing_transaction")
		self.assertFalse(fields["borrowing"].get("hidden"))
		self.assertTrue(fields["borrowing"].get("read_only"))
		section = order[order.index("borrowing_section") : order.index("accounting_dimensions_section")]
		for name, source in (
			("borrowing_request_type", "borrowing.material_request_type"),
			("borrowing_borrower_type", "borrowing.borrower_type"),
			("borrowing_borrower", "borrowing.borrower"),
			("borrowing_requested_by_name", "borrowing.requested_by_name"),
			("borrowing_recipient_location", "borrowing.recipient_location"),
			("borrowing_custody_warehouse", "borrowing.customer_loan_warehouse"),
		):
			with self.subTest(name):
				self.assertIn(name, section)
				self.assertEqual(fields[name]["fetch_from"], source)
				self.assertTrue(fields[name]["read_only"])

	def test_no_credit_note_on_submit_and_a_sales_return_needs_its_reason(self):
		fields, _order = self.fields("delivery_note")
		self.assertTrue(fields["issue_credit_note"]["hidden"])
		self.assertEqual(
			fields["return_reason"]["mandatory_depends_on"], fields["return_reason"]["depends_on"]
		)

	def test_the_rows_hide_prices_and_show_where_the_goods_go(self):
		fields, _order = self.fields("delivery_note_item")
		for name in (
			"section_break_17",
			"discount_and_margin",
			"section_break_1",
			"section_break_25",
			"item_tax_template",
			"billed_amt",
			"accounting_details_section",
		):
			with self.subTest(name):
				self.assertIn("!parent.is_borrowing_transaction", fields[name]["depends_on"])
		for name in ("target_warehouse", "to_storage_location"):
			with self.subTest(name):
				self.assertFalse(fields[name].get("hidden"))
				self.assertIn("parent.is_borrowing_transaction", fields[name]["depends_on"])
				self.assertEqual(fields[name]["read_only_depends_on"], "eval:parent.is_borrowing_transaction")


if __name__ == "__main__":
	unittest.main()
