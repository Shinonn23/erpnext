"""Offline tests of the company branch address choice and the Payment Entry branch derivation."""

import unittest
from unittest import mock

import frappe

from erpnext.regional.thailand.custom import branches, custom_api
from erpnext.regional.thailand.custom.custom_api import (
	create_tax_invoice,
	get_company_tax_address,
	validate_company_address,
	voucher_company_tax_address,
)


def address(name, code="", address_type="Office", primary=0, disabled=0):
	return frappe._dict(
		name=name,
		address_type=address_type,
		branch_code=code,
		is_primary_address=primary,
		disabled=disabled,
	)


class ThrowError(Exception):
	pass


def fake_frappe(get_all=None, get_value=None):
	"""A stand-in for the frappe module inside custom_api (no site, no database)."""
	fake = mock.MagicMock()

	def db_get_all(doctype, filters=None, order_by=None, **kwargs):
		rows = list(get_all or [])
		if ["disabled", "=", 0] in (filters or []):
			rows = [row for row in rows if not row.get("disabled")]
		return sorted(rows, key=lambda row: row.name) if order_by == "name" else rows

	fake.db.get_all.side_effect = db_get_all
	fake.db.get_value.side_effect = get_value
	fake.throw.side_effect = lambda message, *args, **kwargs: (_ for _ in ()).throw(ThrowError(message))
	return fake


def pick(*addresses):
	with mock.patch.object(custom_api, "frappe", fake_frappe(list(addresses))):
		return get_company_tax_address("CD")


def payment(references=(), company_tax_address=None):
	return frappe._dict(
		company="CD",
		company_tax_address=company_tax_address,
		references=[
			frappe._dict(reference_doctype=doctype, reference_name=name) for doctype, name in references
		],
	)


INVOICE_ADDRESSES = {
	("Sales Invoice", "SI-1"): "HO",
	("Sales Invoice", "SI-2"): "HO",
	("Sales Invoice", "SI-3"): "B3",
	("Purchase Invoice", "PI-1"): "B4",
	("Sales Order", "SO-3"): "B3",
	("Purchase Order", "PO-4"): "B4",
	("Expense Claim", "EC-4"): "B4",
	("Journal Entry", "JV-3"): "B3",
	("Sales Invoice", "SI-BLANK"): None,
}


def validate(doc):
	def get_value(doctype, name, field):
		return INVOICE_ADDRESSES.get((doctype, name))

	with (
		mock.patch.object(custom_api, "frappe", fake_frappe([address("HO", "00000")], get_value)),
		mock.patch.object(custom_api, "_", lambda text: text),
	):
		validate_company_address(doc, None)
	return doc.company_tax_address


class TestCompanyTaxAddress(unittest.TestCase):
	def test_none_without_addresses(self):
		self.assertIsNone(pick())

	def test_only_one(self):
		self.assertEqual(pick(address("A", "00003")), "A")

	def test_head_office_among_branches(self):
		self.assertEqual(pick(address("B3", "00003"), address("HO", "0"), address("B4", "00004")), "HO")
		self.assertEqual(pick(address("B3", "00003"), address("HO", "00000")), "HO")

	def test_head_office_beats_billing_branches(self):
		self.assertEqual(
			pick(
				address("HO", "00000"), address("B3", "00003", "Billing"), address("B4", "00004", "Billing")
			),
			"HO",
		)

	def test_three_billing_head_office_via_code(self):
		self.assertEqual(
			pick(
				address("B4", "00004", "Billing"),
				address("HO", "0", "Billing"),
				address("B3", "00003", "Billing"),
			),
			"HO",
		)

	def test_two_head_offices_billing_breaks_tie(self):
		self.assertEqual(pick(address("A", "00000"), address("B", "00000", "Billing")), "B")

	def test_two_head_offices_undecidable(self):
		self.assertIsNone(pick(address("A", "00000"), address("B", "00000")))

	def test_billing_among_branches_without_head_office(self):
		self.assertEqual(pick(address("B3", "00003"), address("B4", "00004", "Billing")), "B4")

	def test_disabled_ignored(self):
		self.assertEqual(pick(address("HO", "00000", disabled=1), address("B3", "00003")), "B3")

	def test_only_disabled_address_kept(self):
		self.assertEqual(pick(address("HO", "00000", disabled=1)), "HO")

	def test_order_independent(self):
		rows = [address("B4", "00004"), address("HO", "00000"), address("B3", "00003")]
		self.assertEqual(pick(*rows), pick(*reversed(rows)))

	def test_primary_when_no_head_office(self):
		self.assertEqual(pick(address("B3", "00003"), address("B4", "00004", primary=1)), "B4")

	def test_not_none_with_several_addresses(self):
		self.assertIsNotNone(pick(address("HO", "00000"), address("B3", "00003"), address("B4", "00004")))

	def test_undecidable_is_none(self):
		self.assertIsNone(pick(address("B3", "00003"), address("B4", "00004")))


class TestVoucherCompanyTaxAddress(unittest.TestCase):
	def test_per_voucher_type(self):
		voucher = frappe._dict(company_address="CA", billing_address="BA", company_tax_address="TA")
		self.assertEqual(voucher_company_tax_address("Sales Invoice", voucher), "CA")
		self.assertEqual(voucher_company_tax_address("Purchase Invoice", voucher), "BA")
		for voucher_type in ("Payment Entry", "Expense Claim", "Journal Entry"):
			self.assertEqual(voucher_company_tax_address(voucher_type, voucher), "TA")

	def test_missing_is_none(self):
		self.assertIsNone(voucher_company_tax_address("Sales Invoice", frappe._dict()))


class TestPaymentEntryBranch(unittest.TestCase):
	def test_single_branch_derived(self):
		self.assertEqual(validate(payment([("Sales Invoice", "SI-3")])), "B3")
		self.assertEqual(validate(payment([("Sales Invoice", "SI-1"), ("Sales Invoice", "SI-2")])), "HO")
		self.assertEqual(validate(payment([("Purchase Invoice", "PI-1")])), "B4")

	def test_mixed_branches_throw(self):
		with self.assertRaises(ThrowError) as error:
			validate(payment([("Sales Invoice", "SI-1"), ("Sales Invoice", "SI-3")]))
		self.assertIn("different company branches", str(error.exception))

	def test_explicit_head_office_becomes_the_branch(self):
		# a draft first saved at the head office, then a branch invoice is added
		self.assertEqual(validate(payment([("Sales Invoice", "SI-3")], company_tax_address="HO")), "B3")

	def test_orders_claims_and_journal_entries(self):
		self.assertEqual(validate(payment([("Sales Order", "SO-3")])), "B3")
		self.assertEqual(validate(payment([("Purchase Order", "PO-4")])), "B4")
		self.assertEqual(validate(payment([("Expense Claim", "EC-4")])), "B4")
		self.assertEqual(validate(payment([("Journal Entry", "JV-3")])), "B3")

	def test_orders_of_different_branches_throw(self):
		with self.assertRaises(ThrowError):
			validate(payment([("Sales Order", "SO-3"), ("Purchase Order", "PO-4")]))

	def test_blank_address_reference_ignored(self):
		self.assertEqual(validate(payment([("Sales Invoice", "SI-BLANK"), ("Sales Invoice", "SI-3")])), "B3")
		self.assertEqual(validate(payment([("Sales Invoice", "SI-BLANK")])), "HO")

	def test_no_references_head_office(self):
		self.assertEqual(validate(payment()), "HO")

	def test_no_references_keeps_explicit(self):
		self.assertEqual(validate(payment(company_tax_address="B4")), "B4")


class TestCreateTaxInvoiceAddress(unittest.TestCase):
	def run_create(self, voucher, split=False):
		fake = fake_frappe()
		fake.db.get_all.side_effect = None
		fake.db.get_all.return_value = [frappe._dict(party="CUST", party_type="Customer")]
		inserted = []

		def get_doc(values):
			inserted.append(dict(values))
			return mock.MagicMock()

		fake.get_doc.side_effect = get_doc
		doc = frappe._dict(
			voucher_type="Sales Invoice", voucher_no="SI-3", name="GL-1", party_type="Customer", company="CD"
		)
		fake.db.get_value.side_effect = lambda doctype, name, fields, **kwargs: (
			frappe._dict(branch_code="3", tax_id="0105555000001") if doctype == "Address" else "0105555999999"
		)
		with (
			mock.patch.object(custom_api, "frappe", fake),
			mock.patch.object(branches, "frappe", fake),
			mock.patch.object(custom_api, "_", lambda text: text),
		):
			create_tax_invoice(doc, "Sales Tax Invoice", 100, 7, voucher, split)
		return inserted

	def test_address_in_insert_dict(self):
		voucher = frappe._dict(company_address="B3")
		self.assertEqual([d["company_tax_address"] for d in self.run_create(voucher)], ["B3"])

	def test_address_in_split_insert_dicts(self):
		voucher = frappe._dict(
			company_address="B3",
			splitted_tax_invoices=[
				frappe._dict(
					supplier="S1",
					tax_amount=7,
					tax_base_amount=100,
					tax_invoice_number="1",
					tax_invoice_date="2026-10-01",
					name="r1",
				),
				frappe._dict(
					supplier="S2",
					tax_amount=3,
					tax_base_amount=50,
					tax_invoice_number="2",
					tax_invoice_date="2026-10-02",
					name="r2",
				),
			],
		)
		dicts = self.run_create(voucher, split=True)
		self.assertEqual([d["company_tax_address"] for d in dicts], ["B3", "B3"])


if __name__ == "__main__":
	unittest.main()
