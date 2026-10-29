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
		self.assertEqual(db.values[("Purchase Tax Invoice", "PTI-3", "voucher_detail_no")], "ROW1")
		self.assertEqual(db.values[("Purchase Tax Invoice", "PTI-4", "voucher_detail_no")], "SPL1")

	def test_links_that_exist_are_not_overwritten(self):
		invoice = frappe._dict(
			name="PTI-9",
			voucher_type="Payment Entry",
			voucher_no="PE-9",
			gl_entry=None,
			splitted_tax_invoice=None,
			voucher_detail_no=None,
		)
		db = FakeDB({}, {}, {("Payment Entry", "PE-9")})
		db.values[("Payment Entry", "PE-9", "tax_invoice_ref")] = "PTI-1"
		self.run_patch(db, {"Purchase Tax Invoice": [invoice]})
		self.assertEqual(db.values[("Payment Entry", "PE-9", "tax_invoice_ref")], "PTI-1")


def voucher(**values):
	"""A voucher in memory with a link on its header and on the rows of its tax invoice table."""
	row = frappe._dict(name="ROW1", tax_invoice_ref_type="Purchase Tax Invoice", tax_invoice_ref="PTI-1")
	doc = frappe._dict(
		doctype="Purchase Invoice",
		name="PINV-1",
		docstatus=0,
		tax_invoice_ref_type="Purchase Tax Invoice",
		tax_invoice_ref="PTI-1",
		splitted_tax_invoices=[row],
	)
	doc.update(values)
	return doc


class TestLinksFollowTheLifeOfTheTaxInvoice(unittest.TestCase):
	def test_an_amended_or_duplicated_draft_starts_without_the_link(self):
		doc = voucher(amended_from="PINV-0")
		custom_api.reset_draft_tax_invoice_links(doc)
		self.assertIsNone(doc.tax_invoice_ref)
		self.assertIsNone(doc.tax_invoice_ref_type)
		self.assertIsNone(doc.splitted_tax_invoices[0].tax_invoice_ref)

	def test_a_submitted_voucher_keeps_its_link_on_validate(self):
		doc = voucher(docstatus=1)
		custom_api.reset_draft_tax_invoice_links(doc)
		self.assertEqual(doc.tax_invoice_ref, "PTI-1")

	def test_cancelling_the_voucher_cancels_its_tax_invoices_and_clears_the_link_in_memory(self):
		fake = mock.MagicMock()
		fake.get_all.side_effect = lambda doctype, **kw: (
			["PTI-1"] if doctype == "Purchase Tax Invoice" else []
		)
		doc = voucher(docstatus=1)
		with mock.patch.object(custom_api, "frappe", fake):
			custom_api.cancel_related_tax_invoice(doc, None)
		fake.get_doc.assert_called_once_with("Purchase Tax Invoice", "PTI-1")
		self.assertIsNone(doc.tax_invoice_ref)
		self.assertIsNone(doc.splitted_tax_invoices[0].tax_invoice_ref)

	def test_a_cancelled_tax_invoice_is_dropped_from_every_voucher_table(self):
		fake = mock.MagicMock()
		fake.db.has_column.return_value = True
		with mock.patch.object(custom_api, "frappe", fake):
			custom_api.clear_tax_invoice_links("Purchase Tax Invoice", "PTI-1")
		tables = [c.args[0] for c in fake.db.set_value.call_args_list]
		self.assertEqual(
			tables,
			[*custom_api.TAX_INVOICE_SOURCE_DOCTYPES, *custom_api.TAX_INVOICE_ROW_DOCTYPES],
		)
		first = fake.db.set_value.call_args_list[0]
		self.assertEqual(
			first.args[1], {"tax_invoice_ref_type": "Purchase Tax Invoice", "tax_invoice_ref": "PTI-1"}
		)
		self.assertEqual(first.args[2], {"tax_invoice_ref_type": None, "tax_invoice_ref": None})
		self.assertIs(first.kwargs["update_modified"], False)

	def test_the_tax_invoices_clear_the_links_when_cancelled(self):
		for controller, doctype in (
			(PurchaseTaxInvoice, "Purchase Tax Invoice"),
			(SalesTaxInvoice, "Sales Tax Invoice"),
		):
			doc = SimpleNamespace(doctype=doctype, name="X-1")
			with mock.patch(f"{controller.__module__}.clear_tax_invoice_links") as clear:
				controller.on_cancel(doc)
			clear.assert_called_once_with(doctype, "X-1")

	def run_guard(self, db_link, doc, cancelled=False):
		fake = mock.MagicMock()

		def get_value(doctype, name, fields=None):
			if doctype == "Purchase Tax Invoice":
				return 2 if cancelled else 1
			return db_link

		fake.db.get_value.side_effect = get_value
		with mock.patch.object(custom_api, "frappe", fake):
			custom_api.keep_tax_invoice_links(doc)

	def test_a_form_without_the_link_does_not_blank_it(self):
		doc = voucher(docstatus=1, tax_invoice_ref_type=None, tax_invoice_ref=None)
		doc.splitted_tax_invoices[0].update(tax_invoice_ref_type=None, tax_invoice_ref=None)
		self.run_guard(("Purchase Tax Invoice", "PTI-7"), doc)
		self.assertEqual((doc.tax_invoice_ref_type, doc.tax_invoice_ref), ("Purchase Tax Invoice", "PTI-7"))
		self.assertEqual(doc.splitted_tax_invoices[0].tax_invoice_ref, "PTI-7")

	def test_a_link_to_a_cancelled_tax_invoice_is_not_restored(self):
		doc = voucher(docstatus=1, tax_invoice_ref_type=None, tax_invoice_ref=None)
		self.run_guard(("Purchase Tax Invoice", "PTI-7"), doc, cancelled=True)
		self.assertIsNone(doc.tax_invoice_ref)

	def test_a_link_in_the_form_is_left_as_it_is(self):
		doc = voucher(docstatus=1, tax_invoice_ref="PTI-5")
		doc.splitted_tax_invoices[0].name = None
		self.run_guard(("Purchase Tax Invoice", "PTI-7"), doc)
		self.assertEqual(doc.tax_invoice_ref, "PTI-5")

	def test_the_submitted_voucher_in_memory_gets_the_link_of_the_zero_tax_invoice(self):
		doc = frappe._dict(doctype="Sales Invoice", name="SINV-1")
		invoice = tinv(doctype="Sales Tax Invoice", voucher_type="Sales Invoice", voucher_no="SINV-1")
		fake = mock.MagicMock()
		fake.db.get_value.return_value = None
		with mock.patch.object(custom_api, "frappe", fake):
			custom_api.link_voucher_to_tax_invoice(None, doc, invoice)
		self.assertEqual((doc.tax_invoice_ref_type, doc.tax_invoice_ref), ("Sales Tax Invoice", invoice.name))

	def test_the_hooks_are_registered_for_the_five_vouchers(self):
		hooks = importlib.import_module("erpnext.hooks")
		for doctype in custom_api.TAX_INVOICE_SOURCE_DOCTYPES:
			events = hooks.doc_events[doctype]
			self.assertIn(
				"erpnext.regional.thailand.custom.custom_api.reset_draft_tax_invoice_links",
				events["validate"],
			)
			self.assertIn(
				"erpnext.regional.thailand.custom.custom_api.keep_tax_invoice_links",
				events["before_update_after_submit"],
			)


class TestDashboardGroups(unittest.TestCase):
	def test_an_expense_claim_that_already_lists_purchase_tax_invoice_gets_no_second_group(self):
		data = {
			"transactions": [{"label": "Tax", "items": ["Purchase Tax Invoice"]}],
			"non_standard_fieldnames": {},
		}
		data = dashboard_overrides.get_dashboard_data_for_expense_claim(data)
		self.assertEqual(len(data["transactions"]), 1)
		self.assertEqual(data["non_standard_fieldnames"]["Purchase Tax Invoice"], "voucher_no")
		self.assertEqual(data["dynamic_links"]["voucher_no"], ["Expense Claim", "voucher_type"])

	def test_a_missing_one_is_added(self):
		data = dashboard_overrides.get_dashboard_data_for_expense_claim({"transactions": []})
		self.assertEqual(data["transactions"][0]["items"], ["Purchase Tax Invoice"])


class TestSupplierNumberOnce(unittest.TestCase):
	def doc(self, number, before_number, action):
		doc = SimpleNamespace(
			number=number,
			party="SUP-1",
			name="CD00003-PTI6910002",
			_action=action,
			get_doc_before_save=lambda: SimpleNamespace(number=before_number),
			validate_account=lambda: None,
			compute_report_date=lambda: None,
		)
		doc.validate_supplier_number = lambda **kw: PurchaseTaxInvoice.validate_supplier_number(doc, **kw)
		return doc

	def run_validate(self, doc, duplicates):
		fake = mock.MagicMock()
		fake.get_all.return_value = duplicates
		fake.throw.side_effect = lambda message, *a, **kw: (_ for _ in ()).throw(
			frappe.ValidationError(message)
		)
		fake.bold.side_effect = lambda value: value
		module = PurchaseTaxInvoice.__module__
		with mock.patch(f"{module}.frappe", fake), mock.patch(f"{module}.set_company_branch_snapshot"):
			PurchaseTaxInvoice.validate(doc)
		return fake

	def test_submit_warns_once(self):
		doc = self.doc("INV-9", None, "submit")
		fake = self.run_validate(doc, ["PTI-1"])
		fake.msgprint.assert_not_called()
		module = PurchaseTaxInvoice.__module__
		fake = mock.MagicMock()
		fake.get_all.return_value = ["PTI-1"]
		fake.bold.side_effect = lambda value: value
		with mock.patch(f"{module}.frappe", fake):
			PurchaseTaxInvoice.before_submit(doc)
		fake.msgprint.assert_called_once()

	def test_an_unchanged_number_after_submit_is_left_alone_even_when_empty(self):
		for number in ("", "INV-9"):
			fake = self.run_validate(self.doc(number, number, "update_after_submit"), ["PTI-1"])
			fake.msgprint.assert_not_called()
			fake.throw.assert_not_called()

	def test_a_changed_number_after_submit_is_checked_and_warned_once(self):
		fake = self.run_validate(self.doc("INV-10", "INV-9", "update_after_submit"), ["PTI-1"])
		fake.msgprint.assert_called_once()
		with self.assertRaises(frappe.ValidationError):
			self.run_validate(self.doc("", "INV-9", "update_after_submit"), [])
