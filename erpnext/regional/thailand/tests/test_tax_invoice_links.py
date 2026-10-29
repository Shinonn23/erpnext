"""Offline tests of the links from vouchers to their tax invoices: the helper that writes them after submit,
the cancel of every tax invoice of a voucher, the Purchase Tax Invoice supplier number rules, the field
definitions and the backfill patch."""

import importlib
import json
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import frappe

from erpnext.regional.doctype.purchase_tax_invoice.purchase_tax_invoice import PurchaseTaxInvoice
from erpnext.regional.doctype.sales_tax_invoice.sales_tax_invoice import SalesTaxInvoice
from erpnext.regional.thailand import constants
from erpnext.regional.thailand.custom import custom_api, dashboard_overrides

DOCTYPE_DIR = Path(custom_api.__file__).parents[2] / "doctype"


def doctype_json(folder):
	return json.loads((DOCTYPE_DIR / folder / f"{folder}.json").read_text())


def tinv(doctype="Purchase Tax Invoice", name="CD00003-PTI6910001", **values):
	values.setdefault("voucher_type", "Payment Entry")
	values.setdefault("voucher_no", "PE-1")
	values.setdefault("splitted_tax_invoice", None)
	return SimpleNamespace(doctype=doctype, name=name, **values)


class TestLinkHelper(unittest.TestCase):
	def run_helper(self, gl, invoice, header_value=None):
		fake = mock.MagicMock()
		fake.db.get_value.return_value = header_value
		with mock.patch.object(custom_api, "frappe", fake):
			custom_api.link_voucher_to_tax_invoice(gl, None, invoice)
		return fake.db.set_value

	def link(self, invoice):
		return {"tax_invoice_ref_type": invoice.doctype, "tax_invoice_ref": invoice.name}

	def test_journal_entry_links_the_row_named_by_the_gl_entry(self):
		invoice = tinv(voucher_type="Journal Entry", voucher_no="JV-1")
		gl = SimpleNamespace(voucher_type="Journal Entry", voucher_detail_no="ROW1")
		set_value = self.run_helper(gl, invoice)
		set_value.assert_called_once_with(
			"Journal Entry Tax Invoice Detail", "ROW1", self.link(invoice), update_modified=False
		)

	def test_a_split_invoice_links_its_split_row(self):
		invoice = tinv(voucher_type="Purchase Invoice", voucher_no="PINV-1", splitted_tax_invoice="SPL1")
		set_value = self.run_helper(SimpleNamespace(voucher_type="Purchase Invoice"), invoice)
		set_value.assert_called_once_with(
			"Purchase Invoice Tax Invoice Detail", "SPL1", self.link(invoice), update_modified=False
		)
		invoice = tinv(voucher_type="Expense Claim", voucher_no="EC-1", splitted_tax_invoice="SPL2")
		set_value = self.run_helper(SimpleNamespace(voucher_type="Expense Claim"), invoice)
		self.assertEqual(set_value.call_args.args[:2], ("Expense Claim Tax Invoice Detail", "SPL2"))

	def test_other_vouchers_link_the_header(self):
		invoice = tinv(doctype="Sales Tax Invoice", voucher_type="Sales Invoice", voucher_no="SINV-1")
		set_value = self.run_helper(None, invoice)
		set_value.assert_called_once_with(
			"Sales Invoice", "SINV-1", self.link(invoice), update_modified=False
		)

	def test_a_payment_entry_keeps_the_first_tax_invoice(self):
		set_value = self.run_helper(SimpleNamespace(voucher_type="Payment Entry"), tinv(), "FIRST")
		set_value.assert_not_called()


class TestCancelAll(unittest.TestCase):
	def test_every_tax_invoice_of_the_voucher_is_cancelled(self):
		fake = mock.MagicMock()
		fake.get_all.side_effect = lambda doctype, **kw: (
			["PTI-1", "PTI-2"] if doctype == "Purchase Tax Invoice" else []
		)
		docs = []
		fake.get_doc.side_effect = lambda doctype, name: docs.append(name) or mock.MagicMock()
		with mock.patch.object(custom_api, "frappe", fake):
			custom_api.cancel_related_tax_invoice(
				SimpleNamespace(doctype="Purchase Invoice", name="P1"), None
			)
		self.assertEqual(docs, ["PTI-1", "PTI-2"])


class TestCancelKeepsSourceLinks(unittest.TestCase):
	def test_tax_invoices_ignore_their_submitted_source_vouchers_on_cancel(self):
		for controller in (PurchaseTaxInvoice, SalesTaxInvoice):
			doc = SimpleNamespace()
			controller.before_cancel(doc)
			self.assertEqual(set(doc.ignore_linked_doctypes), set(custom_api.TAX_INVOICE_SOURCE_DOCTYPES))
		self.assertEqual(
			set(custom_api.TAX_INVOICE_SOURCE_DOCTYPES),
			{"Sales Invoice", "Purchase Invoice", "Payment Entry", "Journal Entry", "Expense Claim"},
		)


class TestSupplierNumber(unittest.TestCase):
	def check(self, number, duplicates=()):
		doc = SimpleNamespace(number=number, party="SUP-1", name="CD00003-PTI6910002")
		fake = mock.MagicMock()
		fake.get_all.return_value = list(duplicates)
		fake.throw.side_effect = lambda message, *a, **kw: (_ for _ in ()).throw(
			frappe.ValidationError(message)
		)
		fake.bold.side_effect = lambda value: value
		module = "erpnext.regional.doctype.purchase_tax_invoice.purchase_tax_invoice.frappe"
		with mock.patch(module, fake):
			PurchaseTaxInvoice.validate_supplier_number(doc)
		return fake

	def test_the_number_is_required(self):
		for blank in (None, "", "   "):
			with self.assertRaises(frappe.ValidationError):
				self.check(blank)

	def test_a_unique_number_passes_quietly(self):
		fake = self.check(" INV-9 ")
		fake.msgprint.assert_not_called()

	def test_the_same_number_of_the_same_supplier_is_warned_not_refused(self):
		fake = self.check("INV-9", ["CD00003-PTI6910001"])
		fake.msgprint.assert_called_once()
		filters = fake.get_all.call_args.kwargs["filters"]
		self.assertEqual((filters["party"], filters["number"]), ("SUP-1", "INV-9"))
		self.assertEqual(filters["docstatus"], ["!=", 2])


class TestDefinitions(unittest.TestCase):
	def test_purchase_tax_invoice_shows_its_system_name_in_links(self):
		meta = doctype_json("purchase_tax_invoice")
		self.assertEqual(meta["show_title_field_in_link"], 0)
		self.assertEqual(meta["title_field"], "number")

	def test_the_tax_invoices_name_the_row_they_came_from(self):
		for folder in ("purchase_tax_invoice", "sales_tax_invoice"):
			meta = doctype_json(folder)
			self.assertIn("voucher_detail_no", meta["field_order"])
			field = next(f for f in meta["fields"] if f["fieldname"] == "voucher_detail_no")
			self.assertEqual(field["read_only"], 1)

	def test_the_detail_rows_have_a_dynamic_link_to_the_tax_invoice(self):
		for folder in (
			"journal_entry_tax_invoice_detail",
			"purchase_invoice_tax_invoice_detail",
			"expense_claim_tax_invoice_detail",
		):
			fields = {f["fieldname"]: f for f in doctype_json(folder)["fields"]}
			link = fields["tax_invoice_ref"]
			self.assertEqual(link["fieldtype"], "Dynamic Link")
			self.assertEqual(link["options"], "tax_invoice_ref_type")
			for field in (link, fields["tax_invoice_ref_type"]):
				self.assertEqual((field["read_only"], field["allow_on_submit"], field["no_copy"]), (1, 1, 1))

	def test_the_voucher_headers_have_the_link_fields(self):
		for doctype, source in (
			("Sales Invoice", constants.ERP_CUSTOM_FIELDS),
			("Purchase Invoice", constants.ERP_CUSTOM_FIELDS),
			("Payment Entry", constants.ERP_CUSTOM_FIELDS),
			("Expense Claim", constants.HRMS_CUSTOM_FIELDS),
		):
			fields = {f["fieldname"]: f for f in source[doctype]}
			self.assertEqual(fields["tax_invoice_ref"]["options"], "tax_invoice_ref_type", doctype)
			self.assertEqual(fields["tax_invoice_ref"]["fieldtype"], "Dynamic Link")
			self.assertEqual(fields["tax_invoice_ref_type"]["hidden"], 1)
			self.assertEqual(fields["tax_invoice_ref_type"]["insert_after"] in fields, True, doctype)

	def test_invoices_and_claims_show_their_tax_invoices_in_connections(self):
		hooks = importlib.import_module("erpnext.hooks")
		for doctype in ("Sales Invoice", "Purchase Invoice", "Expense Claim"):
			self.assertIn(doctype, hooks.override_doctype_dashboards)
		data = dashboard_overrides.get_dashboard_data_for_sales_invoice(
			{"transactions": [{"label": "Reference", "items": []}], "non_standard_fieldnames": {}}
		)
		self.assertEqual(data["non_standard_fieldnames"]["Sales Tax Invoice"], "voucher_no")
		self.assertEqual(data["dynamic_links"]["voucher_no"], ["Sales Invoice", "voucher_type"])


class FakeDB:
	def __init__(self, invoices, gl_rows, existing):
		self.invoices, self.gl_rows, self.existing = invoices, gl_rows, existing
		self.values = {}

	def get_value(self, doctype, name, field):
		if doctype == "GL Entry":
			return self.gl_rows[name]
		return self.values.get((doctype, name, field))

	def set_value(self, doctype, name, values, value=None, update_modified=True):
		assert update_modified is False
		for field, val in (values if isinstance(values, dict) else {values: value}).items():
			self.values[(doctype, name, field)] = val

	def exists(self, doctype, name):
		return (doctype, name) in self.existing

	def has_column(self, doctype, field):
		return True


class TestBackfillPatch(unittest.TestCase):
	def run_patch(self, db, invoices):
		patch_module = importlib.import_module("erpnext.patches.v16_0.link_tax_invoices_to_vouchers")
		fake = mock.MagicMock()
		fake.db = db
		fake.get_all.side_effect = lambda doctype, **kw: invoices.get(doctype, [])
		with mock.patch.object(patch_module, "frappe", fake):
			patch_module.execute()

	def test_it_links_rows_and_headers_once(self):
		def inv(name, **values):
			base = dict(
				voucher_type="Payment Entry",
				voucher_no="PE-1",
				gl_entry=None,
				splitted_tax_invoice=None,
				voucher_detail_no=None,
			)
			base.update(values)
			return frappe._dict(name=name, **base)

		invoices = {
			"Sales Tax Invoice": [inv("STI-1", voucher_type="Sales Invoice", voucher_no="SINV-1")],
			"Purchase Tax Invoice": [
				inv("PTI-1"),
				inv("PTI-2"),
				inv("PTI-3", voucher_type="Journal Entry", voucher_no="JV-1", gl_entry="GL-1"),
				inv("PTI-4", voucher_type="Purchase Invoice", voucher_no="P-1", splitted_tax_invoice="SPL1"),
			],
		}
		existing = {
			("Sales Invoice", "SINV-1"),
			("Payment Entry", "PE-1"),
			("Journal Entry Tax Invoice Detail", "ROW1"),
			("Purchase Invoice Tax Invoice Detail", "SPL1"),
		}
		db = FakeDB(invoices, {"GL-1": "ROW1"}, existing)
		self.run_patch(db, invoices)
		values = dict(db.values)
		self.assertEqual(values[("Sales Invoice", "SINV-1", "tax_invoice_ref")], "STI-1")
		self.assertEqual(values[("Payment Entry", "PE-1", "tax_invoice_ref")], "PTI-1")
		self.assertEqual(values[("Journal Entry Tax Invoice Detail", "ROW1", "tax_invoice_ref")], "PTI-3")
		self.assertEqual(values[("Purchase Tax Invoice", "PTI-3", "voucher_detail_no")], "ROW1")
		self.assertEqual(values[("Purchase Invoice Tax Invoice Detail", "SPL1", "tax_invoice_ref")], "PTI-4")
		self.assertEqual(values[("Purchase Tax Invoice", "PTI-4", "voucher_detail_no")], "SPL1")
		self.assertNotIn(("Purchase Invoice", "P-1", "tax_invoice_ref"), values)
		# a second run changes nothing
		self.run_patch(db, invoices)
		self.assertEqual(values, db.values)
