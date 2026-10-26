"""Offline tests of the selling branch rules on documents, the tax invoice snapshot and its backfill patch."""

import importlib
import unittest
from unittest import mock

import frappe

from erpnext.regional.thailand.custom import company_branch, custom_api


class ThrowError(Exception):
	pass


def address(name, code="00000"):
	return frappe._dict(name=name, branch_code=code)


ADDRESSES = [address("HO", "00000"), address("B3", "00003"), address("B4", "00004")]

DOCUMENT_ADDRESSES = {
	("Sales Invoice", "SINV-1"): "B3",
	("Sales Order", "SO-HO"): "HO",
	("Sales Order", "SO-B3"): "B3",
	("Sales Order", "SO-B4"): "B4",
	("Delivery Note", "DN-B3"): "B3",
	("Delivery Note", "DN-B4"): "B4",
	("Sales Order", "SO-OLD"): None,
}


def fake_frappe(addresses=ADDRESSES):
	fake = mock.MagicMock()
	fake.db.get_all.return_value = list(addresses)
	fake.db.get_value.side_effect = lambda doctype, name, field: DOCUMENT_ADDRESSES.get((doctype, name))
	fake.bold.side_effect = lambda value: value
	fake.throw.side_effect = lambda message, *args, **kwargs: (_ for _ in ()).throw(ThrowError(message))
	return fake


def document(doctype="Sales Invoice", items=(), **values):
	rows = [frappe._dict(row) for row in items]
	values.setdefault("company", "CD")
	values.setdefault("company_address", "B3")
	return frappe._dict(doctype=doctype, name="NEW-1", items=rows, **values)


def run(doc, addresses=ADDRESSES):
	with (
		mock.patch.object(company_branch, "frappe", fake_frappe(addresses)),
		mock.patch.object(company_branch, "_", lambda text: text),
	):
		company_branch.validate_company_branch(doc, "validate")
	return doc


class TestSellingBranch(unittest.TestCase):
	def test_branch_code_normalisation(self):
		self.assertEqual(company_branch.normalise_company_branch_code("3"), "00003")
		self.assertTrue(company_branch.is_valid_branch_code("0"))
		self.assertFalse(company_branch.is_valid_branch_code("HO"))
		self.assertFalse(company_branch.is_valid_branch_code(""))
		self.assertFalse(company_branch.is_valid_branch_code("123456"))

	def test_valid_branch_passes_on_every_selling_doctype(self):
		for doctype in ("Quotation", "Sales Order", "Delivery Note", "Sales Invoice"):
			run(document(doctype))

	def test_several_addresses_require_a_branch(self):
		with self.assertRaises(ThrowError):
			run(document(company_address=None))

	def test_branch_must_be_a_company_address_of_the_company(self):
		with self.assertRaises(ThrowError):
			run(document(company_address="OTHER-CO-Office"))

	def test_branch_needs_a_five_digit_code(self):
		for code in ("", "HO", "123456"):
			with self.assertRaises(ThrowError):
				run(document(company_address="BAD"), [address("HO"), address("BAD", code)])

	def test_short_numeric_code_is_accepted(self):
		run(document(company_address="B3"), [address("HO", "0"), address("B3", "3")])

	def test_single_address_company_is_left_alone(self):
		single = [address("UB-Office", "")]
		doc = run(document(company_address="whatever", is_return=1, return_against="SINV-1"), single)
		self.assertEqual(doc.company_address, "whatever")

	def test_single_address_is_defaulted_silently(self):
		doc = run(document(company_address=None), [address("UB-Office", "")])
		self.assertEqual(doc.company_address, "UB-Office")

	def test_company_without_address_is_left_alone(self):
		doc = run(document(company_address=None), [])
		self.assertIsNone(doc.company_address)

	def test_return_with_another_branch_throws(self):
		with self.assertRaises(ThrowError):
			run(document(company_address="HO", is_return=1, return_against="SINV-1"))

	def test_return_with_same_branch_passes(self):
		run(document(company_address="B3", is_return=1, return_against="SINV-1"))

	def test_sales_invoice_from_sales_orders_of_its_branch(self):
		run(document(items=[{"sales_order": "SO-B3"}, {"sales_order": "SO-B3"}, {"sales_order": None}]))

	def test_sales_invoice_mismatching_its_sales_order_throws(self):
		with self.assertRaises(ThrowError):
			run(document(items=[{"sales_order": "SO-B4"}]))

	def test_sales_invoice_from_mixed_orders_throws(self):
		with self.assertRaises(ThrowError):
			run(document(items=[{"sales_order": "SO-B3"}, {"sales_order": "SO-B4"}]))

	def test_sales_invoice_from_order_and_note_of_two_branches_throws(self):
		with self.assertRaises(ThrowError):
			run(document(items=[{"sales_order": "SO-B3", "delivery_note": "DN-B4"}]))

	def test_sales_invoice_mismatching_its_delivery_note_throws(self):
		with self.assertRaises(ThrowError):
			run(document(company_address="HO", items=[{"delivery_note": "DN-B3"}]))

	def test_delivery_note_follows_its_sales_orders(self):
		run(document("Delivery Note", items=[{"against_sales_order": "SO-B3"}]))
		with self.assertRaises(ThrowError):
			run(
				document(
					"Delivery Note",
					items=[{"against_sales_order": "SO-B3"}, {"against_sales_order": "SO-B4"}],
				)
			)
		with self.assertRaises(ThrowError):
			run(document("Delivery Note", company_address="HO", items=[{"against_sales_order": "SO-B3"}]))

	def test_delivery_note_follows_its_sales_invoice(self):
		run(document("Delivery Note", items=[{"against_sales_invoice": "SINV-1"}]))
		with self.assertRaises(ThrowError):
			run(document("Delivery Note", company_address="HO", items=[{"against_sales_invoice": "SINV-1"}]))

	def test_source_without_branch_is_ignored(self):
		run(document(items=[{"sales_order": "SO-OLD"}]))


class TestSnapshot(unittest.TestCase):
	def snapshot(self, row, company_tax_id="0105555999999"):
		fake = mock.MagicMock()
		fake.db.get_value.side_effect = lambda doctype, name, fields, **kwargs: (
			row if doctype == "Address" else company_tax_id
		)
		with mock.patch.object(company_branch, "frappe", fake):
			return company_branch.get_company_branch_snapshot("CD", "B3")

	def test_address_branch_and_tax_id(self):
		row = frappe._dict(branch_code="3", tax_id="0105555000001")
		self.assertEqual(
			self.snapshot(row), {"company_branch_code": "00003", "company_tax_id": "0105555000001"}
		)

	def test_tax_id_falls_back_to_the_company(self):
		row = frappe._dict(branch_code="00000", tax_id=" ")
		self.assertEqual(self.snapshot(row)["company_tax_id"], "0105555999999")

	def test_no_address_has_no_branch(self):
		fake = mock.MagicMock()
		fake.db.get_value.return_value = "0105555999999"
		with mock.patch.object(company_branch, "frappe", fake):
			snapshot = company_branch.get_company_branch_snapshot("CD", None)
		self.assertEqual(snapshot, {"company_branch_code": "", "company_tax_id": "0105555999999"})

	def test_zero_tax_sales_tax_invoice_is_snapshotted_at_insert(self):
		fake = mock.MagicMock()
		fake.db.get_value.side_effect = lambda doctype, name, fields, **kwargs: (
			frappe._dict(branch_code="4", tax_id="T4") if doctype == "Address" else "T"
		)
		inserted = []
		fake.get_doc.side_effect = lambda values: inserted.append(dict(values)) or mock.MagicMock()
		invoice = frappe._dict(
			company="CD", company_address="B4", posting_date="2026-10-01", customer="C", name="SI-1"
		)
		with (
			mock.patch.object(custom_api, "frappe", fake),
			mock.patch.object(company_branch, "frappe", fake),
		):
			custom_api.create_sales_tax_invoice_zero_tax(invoice, "Sales Tax Invoice", 100, "VAT")
		self.assertEqual(inserted[0]["company_branch_code"], "00004")
		self.assertEqual(inserted[0]["company_tax_address"], "B4")


class TestAmendedSnapshot(unittest.TestCase):
	def amend(self, doctype, docstatus=0, address="B3"):
		from erpnext.regional.doctype.purchase_tax_invoice.purchase_tax_invoice import PurchaseTaxInvoice
		from erpnext.regional.doctype.sales_tax_invoice.sales_tax_invoice import SalesTaxInvoice

		controller = {"Sales Tax Invoice": SalesTaxInvoice, "Purchase Tax Invoice": PurchaseTaxInvoice}
		doc = controller[doctype].__new__(controller[doctype])
		doc.__dict__.update(
			docstatus=docstatus,
			company="CD",
			company_tax_address=address,
			amended_from="OLD-1",
			company_branch_code=None,
			company_tax_id=None,
		)
		fake = mock.MagicMock()
		fake.db.get_value.side_effect = lambda doctype, name, fields, **kwargs: (
			frappe._dict(branch_code="3", tax_id="T3") if doctype == "Address" else "T"
		)
		with mock.patch.object(company_branch, "frappe", fake):
			company_branch.set_company_branch_snapshot(doc)
		return doc

	def test_amended_copy_gets_the_snapshot_back(self):
		for doctype in ("Sales Tax Invoice", "Purchase Tax Invoice"):
			doc = self.amend(doctype)
			self.assertEqual((doc.company_branch_code, doc.company_tax_id), ("00003", "T3"))

	def test_submitted_invoice_snapshot_is_not_rewritten(self):
		doc = self.amend("Sales Tax Invoice", docstatus=1)
		self.assertIsNone(doc.company_branch_code)

	def test_controllers_call_the_snapshot_in_validate(self):
		import inspect

		from erpnext.regional.doctype.purchase_tax_invoice.purchase_tax_invoice import PurchaseTaxInvoice
		from erpnext.regional.doctype.sales_tax_invoice.sales_tax_invoice import SalesTaxInvoice

		for controller in (SalesTaxInvoice, PurchaseTaxInvoice):
			self.assertIn("set_company_branch_snapshot(self)", inspect.getsource(controller.validate))


class TestSnapshotPatch(unittest.TestCase):
	# address -> (branch code, is primary); HO-CO has two addresses, UB has one
	def run_patch(self, invoices, company_addresses, primary=()):
		patch_module = importlib.import_module(
			"erpnext.patches.v16_0.snapshot_company_branch_on_tax_invoices"
		)
		fake = mock.MagicMock()
		fake.get_all.side_effect = lambda doctype, **kwargs: invoices.get(doctype, [])
		fake.db.has_column.return_value = True
		fake.db.get_value.side_effect = lambda doctype, name, field: int(name in primary)
		codes = {"B3": "00003", "HO": "", "SOLO": ""}
		with (
			mock.patch.object(patch_module, "frappe", fake),
			mock.patch.object(
				patch_module,
				"get_company_addresses",
				side_effect=lambda company: [frappe._dict(name=n) for n in company_addresses[company]],
			),
			mock.patch.object(
				patch_module,
				"get_company_branch_snapshot",
				side_effect=lambda company, addr, default="": {
					"company_branch_code": codes[addr] or default,
					"company_tax_id": "T",
				},
			),
		):
			patch_module.execute()
		return fake

	def invoice(self, name, company, address, **values):
		row = {
			"name": name,
			"company": company,
			"company_tax_address": address,
			"company_branch_code": None,
			"company_tax_id": None,
		}
		return frappe._dict({**row, **values})

	def test_backfills_without_touching_modified(self):
		fake = self.run_patch(
			{
				"Sales Tax Invoice": [
					self.invoice("S1", "CD", "B3"),
					self.invoice("S2", "CD", None),
				],
				"Purchase Tax Invoice": [self.invoice("P1", "CD", "B3")],
			},
			{"CD": ["HO", "B3"]},
		)
		calls = fake.db.set_value.call_args_list
		self.assertEqual(
			[c.args[:2] for c in calls], [("Sales Tax Invoice", "S1"), ("Purchase Tax Invoice", "P1")]
		)
		self.assertEqual(calls[0].args[2], {"company_branch_code": "00003", "company_tax_id": "T"})
		self.assertTrue(all(c.kwargs == {"update_modified": False} for c in calls))

	def test_blank_branch_is_head_office_only_when_certain(self):
		invoices = {
			"Sales Tax Invoice": [
				self.invoice("S1", "CD", "HO"),
				self.invoice("S2", "UB", "SOLO"),
			]
		}
		fake = self.run_patch(invoices, {"CD": ["HO", "B3"], "UB": ["SOLO"]})
		first, second = (c.args[2] for c in fake.db.set_value.call_args_list)
		self.assertEqual(first, {"company_tax_id": "T"})  # two addresses, not primary: branch left empty
		self.assertEqual(second["company_branch_code"], "00000")  # single address
		fake = self.run_patch(invoices, {"CD": ["HO", "B3"], "UB": ["SOLO"]}, primary=("HO",))
		self.assertEqual(fake.db.set_value.call_args_list[0].args[2]["company_branch_code"], "00000")

	def test_filled_fields_are_kept_and_complete_rows_skipped(self):
		done = self.invoice("S1", "CD", "B3", company_branch_code="00003", company_tax_id="T")
		fake = self.run_patch({"Sales Tax Invoice": [done]}, {"CD": ["HO", "B3"]})
		fake.db.set_value.assert_not_called()

	def test_selects_submitted_and_cancelled_incomplete_rows(self):
		fake = self.run_patch({}, {})
		kwargs = fake.get_all.call_args_list[0].kwargs
		self.assertEqual(kwargs["filters"]["docstatus"], ["in", [1, 2]])
		self.assertEqual(
			kwargs["or_filters"],
			{"company_branch_code": ["is", "not set"], "company_tax_id": ["is", "not set"]},
		)


if __name__ == "__main__":
	unittest.main()
