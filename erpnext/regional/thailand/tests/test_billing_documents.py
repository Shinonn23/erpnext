"""Offline tests of the Thai billing documents: Purchase Billing, Payment Receipt and the BRANCH of their names."""

import importlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import frappe
from pypika import Table

from erpnext.regional.doctype.payment_receipt import payment_receipt
from erpnext.regional.doctype.purchase_billing import purchase_billing
from erpnext.regional.doctype.sales_billing import sales_billing
from erpnext.regional.thailand.custom import naming_branch

DOCTYPES = Path(purchase_billing.__file__).resolve().parents[1]


class Thrown(Exception):
	pass


def throw(message, *args, **kwargs):
	raise Thrown(message)


class Chain:
	"""frappe.qb stand-in that records the where conditions."""

	def __init__(self, rows):
		self.rows = rows
		self.wheres = []

	def from_(self, *a):
		return self

	join = on = select = from_

	def where(self, criterion):
		self.wheres.append(str(criterion))
		return self

	def run(self):
		return self.rows


def doctype_json(name):
	folder = name.lower().replace(" ", "_")
	return json.loads((DOCTYPES / folder / f"{folder}.json").read_text())


class TestDoctypeJson(unittest.TestCase):
	def test_purchase_billing_closes_like_sales_billing(self):
		field = next(f for f in doctype_json("Purchase Billing")["fields"] if f["fieldname"] == "closed")
		self.assertEqual((field["fieldtype"], field["allow_on_submit"], field["read_only"]), ("Check", 1, 1))

	def test_branch_field_on_the_three_documents(self):
		for name in ("Sales Billing", "Purchase Billing", "Payment Receipt"):
			with self.subTest(name):
				data = doctype_json(name)
				self.assertIn("company_tax_address", data["field_order"])
				self.assertIn(name, naming_branch.NAMING_BRANCH_FIELDS)
				self.assertEqual(naming_branch.NAMING_BRANCH_FIELDS[name], "company_tax_address")

	def test_accounts_user_submits_payment_receipt(self):
		rows = {p["role"]: p for p in doctype_json("Payment Receipt")["permissions"]}
		for right in ("create", "submit", "cancel", "amend"):
			self.assertEqual(rows["Accounts User"].get(right), 1, right)

	def test_hook_closes_purchase_billing_on_submit_and_cancel(self):
		hooks = (DOCTYPES.parents[1] / "hooks.py").read_text()
		handler = "purchase_billing.update_purchase_billing_outstanding_amount"
		self.assertEqual(hooks.count(handler), 2)
		self.assertEqual(hooks.count("payment_entry.update_sales_billing_outstanding_amount"), 2)


class TestPurchaseBillingDue(unittest.TestCase):
	def due(self, excluded):
		chain = Chain(excluded)
		fake = mock.MagicMock()
		fake.get_list.return_value = [{"name": "PI-1"}, {"name": "PI-2"}, {"name": "PI-3"}]
		fake.qb = chain
		with (
			mock.patch.object(purchase_billing, "frappe", fake),
			mock.patch.object(purchase_billing, "DocType", Table),
		):
			result = purchase_billing.get_due_billing("S", "THB", None, "Due Date", "2026-10-31")
		return result, chain

	def test_only_open_submitted_billings_hold_invoices_back(self):
		result, chain = self.due([("PI-2",)])
		self.assertEqual([r["name"] for r in result], ["PI-1", "PI-3"])
		joined = " ".join(chain.wheres)
		self.assertIn('"docstatus"=1', joined)
		self.assertIn('"closed"=0', joined)


class TestBranchOfNames(unittest.TestCase):
	def patched(self, values):
		fake = mock.MagicMock()
		fake.db.get_value.side_effect = lambda doctype, name, field=None, **kw: values.get(
			(doctype, name, field)
		)
		return mock.patch.object(naming_branch, "frappe", fake)

	def test_billing_takes_the_branch_of_its_first_invoice(self):
		doc = frappe._dict(
			doctype="Sales Billing",
			sales_billing_line=[frappe._dict(sales_invoice="SI-1"), frappe._dict(sales_invoice="SI-2")],
		)
		with self.patched({("Sales Invoice", "SI-1", "company_address"): "CD-B3"}):
			self.assertEqual(naming_branch.billing_document_address(doc), "CD-B3")
		doc = frappe._dict(
			doctype="Purchase Billing", purchase_billing_line=[frappe._dict(purchase_invoice="PI-1")]
		)
		with self.patched({("Purchase Invoice", "PI-1", "billing_address"): "CD-B7"}):
			self.assertEqual(naming_branch.billing_document_address(doc), "CD-B7")

	def test_receipt_takes_the_single_branch_of_its_payments(self):
		doc = frappe._dict(
			doctype="Payment Receipt",
			payment_references=[frappe._dict(payment_entry="PE-1"), frappe._dict(payment_entry="PE-2")],
		)
		same = {("Payment Entry", n, "company_tax_address"): "CD-B3" for n in ("PE-1", "PE-2")}
		with self.patched(same):
			self.assertEqual(naming_branch.billing_document_address(doc), "CD-B3")
		two = {
			("Payment Entry", "PE-1", "company_tax_address"): "CD-B3",
			("Payment Entry", "PE-2", "company_tax_address"): "CD-B7",
		}
		with self.patched(two):
			self.assertIsNone(naming_branch.billing_document_address(doc))


PE = {
	"PE-1": frappe._dict(
		docstatus=1,
		payment_type="Receive",
		party_type="Customer",
		party="C1",
		company="CD",
		company_tax_address="CD-B3",
	),
}


class TestPaymentReceiptRules(unittest.TestCase):
	def check(self, entries, customer="C1", other=None, company="CD", pes=None):
		pes = {**PE, **(pes or {})}
		stub = SimpleNamespace(
			payment_references=[SimpleNamespace(payment_entry=n) for n in entries],
			customer=customer,
			company=company,
			company_tax_address=None,
			other_receipt_of=lambda name: other,
		)
		fake = mock.MagicMock()
		fake.throw.side_effect = throw
		fake.db.get_value.side_effect = lambda doctype, name, fields, as_dict=False: pes.get(name)
		with (
			mock.patch.object(payment_receipt, "frappe", fake),
			mock.patch.object(payment_receipt, "_", lambda m: m),
		):
			payment_receipt.PaymentReceipt.validate_payment_references(stub)
		return stub

	def test_a_receipt_without_a_sales_billing_takes_the_payments_branch_and_company(self):
		stub = self.check(["PE-1"], company=None)
		self.assertEqual((stub.company, stub.company_tax_address), ("CD", "CD-B3"))

	def test_refusals(self):
		cases = {
			"listed more than once": dict(entries=["PE-1", "PE-1"]),
			"does not exist": dict(entries=["PE-9"]),
			"is cancelled": dict(entries=["PE-2"], pes={"PE-2": frappe._dict(PE["PE-1"], docstatus=2)}),
			"not a receipt from a customer": dict(
				entries=["PE-2"], pes={"PE-2": frappe._dict(PE["PE-1"], payment_type="Pay")}
			),
			"is for customer": dict(entries=["PE-1"], customer="C2"),
			"belongs to company": dict(entries=["PE-1"], company="UB"),
			"is already on Payment Receipt": dict(entries=["PE-1"], other="PRE-1"),
			"different company branches": dict(
				entries=["PE-1", "PE-2"], pes={"PE-2": frappe._dict(PE["PE-1"], company_tax_address="CD-B7")}
			),
		}
		for text, kwargs in cases.items():
			with self.subTest(text):
				with self.assertRaises(Thrown) as ctx:
					self.check(**kwargs)
				self.assertIn(text, str(ctx.exception))


class TestMultiPaymentsLeaveDraft(unittest.TestCase):
	def test_no_submit_in_create_multi_payment_entries(self):
		source = Path(sales_billing.__file__).read_text()
		body = source.split("def create_multi_payment_entries", 1)[1]
		self.assertNotIn(".submit()", body)
		self.assertIn("used", body)


class Line:
	def __init__(self, invoice, outstanding):
		self.purchase_invoice = self.sales_invoice = invoice
		self.outstanding_amount = outstanding
		self.name = invoice


class Bill:
	"""A submitted billing as a Document: save() records the rights it was saved with."""

	def __init__(self, outstanding, closed=0):
		self.lines = [Line(i, o) for i, o in outstanding.items()]
		self.purchase_billing_line = self.sales_billing_line = self.lines
		self.total_outstanding_amount = sum(outstanding.values())
		self.closed = closed
		self.saved = []

	def save(self, *args, **kwargs):
		self.saved.append(kwargs)


class TestBillingHooks(unittest.TestCase):
	def run_hook(self, module, function, doctype, invoice_doctype, bill, paid, docstatus=1, named=None):
		fake = mock.MagicMock()
		fake.get_all.return_value = ["B-1"]
		fake.get_doc.return_value = bill

		def get_value(dt, name, field=None):
			if dt == doctype:
				return docstatus
			return paid[name]

		fake.db.get_value.side_effect = get_value
		doc = frappe._dict(
			purchase_billing=named,
			sales_billing=named,
			references=[frappe._dict(reference_doctype=invoice_doctype, reference_name="INV-1")],
		)
		with mock.patch.object(module, "frappe", fake):
			getattr(module, function)(doc, "on_submit")
		return fake

	def both(self):
		from erpnext.regional.thailand.custom import payment_entry

		return (
			(
				purchase_billing,
				"update_purchase_billing_outstanding_amount",
				"Purchase Billing",
				"Purchase Invoice",
			),
			(payment_entry, "update_sales_billing_outstanding_amount", "Sales Billing", "Sales Invoice"),
		)

	def test_closes_when_paid_and_opens_when_not_saved_without_the_users_rights(self):
		for module, function, doctype, invoice_doctype in self.both():
			with self.subTest(function):
				bill = Bill({"INV-1": 100, "INV-2": 50})
				self.run_hook(module, function, doctype, invoice_doctype, bill, {"INV-1": 0, "INV-2": 0})
				self.assertEqual((bill.closed, bill.total_outstanding_amount), (1, 0))
				self.assertEqual(bill.saved, [{"ignore_permissions": True}])
				bill = Bill({"INV-1": 0, "INV-2": 0}, closed=1)
				self.run_hook(module, function, doctype, invoice_doctype, bill, {"INV-1": 0, "INV-2": 50})
				self.assertEqual((bill.closed, bill.total_outstanding_amount), (0, 50))

	def test_the_billing_is_found_through_its_invoices_without_the_payment_link(self):
		for module, function, doctype, invoice_doctype in self.both():
			bill = Bill({"INV-1": 10})
			fake = self.run_hook(module, function, doctype, invoice_doctype, bill, {"INV-1": 0})
			fake.get_all.assert_called_once()
			self.assertEqual(bill.closed, 1)

	def test_cancelled_or_draft_billings_and_unchanged_ones_are_left_alone(self):
		for module, function, doctype, invoice_doctype in self.both():
			for docstatus in (0, 2):
				bill = Bill({"INV-1": 10})
				self.run_hook(module, function, doctype, invoice_doctype, bill, {"INV-1": 0}, docstatus)
				self.assertEqual(bill.saved, [])
			bill = Bill({"INV-1": 10})
			self.run_hook(module, function, doctype, invoice_doctype, bill, {"INV-1": 10})
			self.assertEqual(bill.saved, [])


class Ref:
	def __init__(self, name, outstanding):
		self.reference_name = name
		self.outstanding_amount = outstanding


class Entry:
	def __init__(self, rows):
		self.references = [Ref(n, o) for n, o in rows]

	def remove(self, ref):
		self.references.remove(ref)


class TestMultiPaymentAllocation(unittest.TestCase):
	def test_two_payments_and_a_credit_note(self):
		rows = [("SI-1", 100.0), ("SI-2", 60.0), ("CN-1", -30.0)]
		used = {}
		first = Entry(rows)
		sales_billing.deduct_used_allocations(first, used)
		self.assertEqual([r.reference_name for r in first.references], ["SI-1", "SI-2", "CN-1"])
		# the first payment takes SI-1 in full, 10 of SI-2 and the whole credit note
		used.update({"SI-1": 100.0, "SI-2": 10.0, "CN-1": -30.0})
		second = Entry(rows)
		sales_billing.deduct_used_allocations(second, used)
		self.assertEqual(
			[(r.reference_name, r.outstanding_amount) for r in second.references], [("SI-2", 50.0)]
		)

	def test_a_credit_note_nothing_has_used_stays(self):
		entry = Entry([("SI-1", 100.0), ("CN-1", -30.0)])
		sales_billing.deduct_used_allocations(entry, {"SI-1": 40.0})
		self.assertEqual(
			[(r.reference_name, r.outstanding_amount) for r in entry.references],
			[("SI-1", 60.0), ("CN-1", -30.0)],
		)


class TestBillingBranches(unittest.TestCase):
	def test_invoices_of_two_branches_are_refused(self):
		for cls, _line in (
			(sales_billing.SalesBilling, "sales_billing_line"),
			(purchase_billing.PurchaseBilling, "purchase_billing_line"),
		):
			with self.subTest(cls.__name__):
				stub = SimpleNamespace(company_tax_address=None)
				module = importlib.import_module(cls.__module__)
				fake = mock.MagicMock()
				fake.throw.side_effect = throw
				with (
					mock.patch.object(module, "frappe", fake),
					mock.patch.object(module, "_", lambda m: m),
					mock.patch.object(naming_branch, "billing_line_addresses", return_value=["B3", "B7"]),
				):
					with self.assertRaises(Thrown) as ctx:
						cls.set_branch_address(stub)
				self.assertIn("different company branches", str(ctx.exception))
				with mock.patch.object(naming_branch, "billing_line_addresses", return_value=["B3"]):
					cls.set_branch_address(stub)
				self.assertEqual(stub.company_tax_address, "B3")


if __name__ == "__main__":
	unittest.main()
