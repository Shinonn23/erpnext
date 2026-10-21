"""Withhold Tax on receipts and journals, the Pay / Receive accounts of a type of withholding, the Withholding Tax
Cert made on submit and the Connections of a payment (custom/withholding.py, custom/withholding_cert.py,
custom/dashboard_overrides.py). Offline: frappe is patched, the amounts go through ERPNext's own Payment Entry and
Journal Entry methods on stand-in documents."""

import contextlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, call, patch

import frappe
from frappe.utils import flt

from erpnext.accounts.doctype.journal_entry.journal_entry import JournalEntry as CoreJournalEntry
from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry as CorePaymentEntry
from erpnext.regional.thailand.custom import (
	custom_api,
	dashboard_overrides,
	payment_entry,
	withholding,
	withholding_cert,
)

CD = "Connect Diagnostics"
UB = "UB Intellisys"
SERVICE = "Service"  # 3%, Pay and Receive
RENT = "Rent"  # 5%, Pay only
PRIZE = "Prize"  # 5%, Receive only (no Pay Account)
ACCOUNTS = {
	"WHT Payable - CD": {"company": CD, "root_type": "Liability", "account_type": "", "is_group": 0},
	"WHT Receivable - CD": {"company": CD, "root_type": "Asset", "account_type": "", "is_group": 0},
	"WHT Receivable - UB": {"company": UB, "root_type": "Asset", "account_type": "", "is_group": 0},
	"WHT Payable - UB": {"company": UB, "root_type": "Liability", "account_type": "", "is_group": 0},
	"Debtors - CD": {"company": CD, "root_type": "Asset", "account_type": "Receivable", "is_group": 0},
	"Bank - CD": {"company": CD, "root_type": "Asset", "account_type": "Bank", "is_group": 0},
	"Cash - CD": {"company": CD, "root_type": "Asset", "account_type": "Cash", "is_group": 0},
	"Write Off - CD": {"company": CD, "root_type": "Expense", "account_type": "", "is_group": 0},
	"Current Assets - CD": {"company": CD, "root_type": "Asset", "account_type": "", "is_group": 1},
}
for _details in ACCOUNTS.values():
	_details["account_currency"] = "THB"


def system_settings(key):
	return "UTC" if key == "time_zone" else "Commercial Rounding"


def setUpModule():
	# flt(x, precision) reads the rounding method from System Settings; frappe.bold wraps names in markup
	global _module_patches
	_module_patches = [
		patch.object(frappe, "get_system_settings", side_effect=system_settings),
		patch.object(frappe, "bold", side_effect=lambda text: text),
	]
	for item in _module_patches:
		item.start()


def tearDownModule():
	for item in _module_patches:
		item.stop()


class Row(frappe._dict):
	def precision(self, fieldname, parentfield=None):
		return 2


class Doc(frappe._dict):
	"""A document as the Withhold Tax code uses it."""

	def precision(self, fieldname, parentfield=None):
		return 2

	def append(self, table, values):
		row = Row(values)
		row.parentfield = table
		row.idx = len(self.setdefault(table, [])) + 1
		self[table].append(row)
		return row

	def remove(self, row):
		self[row.parentfield].remove(row)
		for idx, other in enumerate(self[row.parentfield], 1):
			other.idx = idx

	def is_new(self):
		return bool(self.get("__islocal"))


class Payment(Doc):
	"""A Payment Entry whose amounts ERPNext's own methods work out."""

	def set_total_allocated_amount(self):
		self.total_allocated_amount = sum(flt(ref.allocated_amount) for ref in self.references)
		self.base_total_allocated_amount = self.total_allocated_amount * flt(self.source_exchange_rate)

	def get_included_taxes(self):
		return CorePaymentEntry.get_included_taxes(self)

	def set_amounts(self):
		self.received_amount = self.paid_amount
		self.base_paid_amount = flt(self.paid_amount * self.source_exchange_rate, 2)
		self.base_received_amount = flt(self.received_amount * self.target_exchange_rate, 2)
		self.set_total_allocated_amount()
		CorePaymentEntry.set_unallocated_amount(self)
		CorePaymentEntry.set_difference_amount(self)


class Journal(Doc):
	def set_amounts_in_company_currency(self):
		CoreJournalEntry.set_amounts_in_company_currency(self)

	def set_total_debit_credit(self):
		CoreJournalEntry.set_total_debit_credit(self)


def receipt(paid=53500, allocated=53500, references=None, deductions=None, **values):
	doc = Payment(
		doctype="Payment Entry",
		name="new-payment-entry-1",
		__islocal=1,
		docstatus=0,
		company=CD,
		posting_date="2026-10-21",
		payment_type="Receive",
		party_type="Customer",
		party="4A016",
		paid_from_account_currency="THB",
		paid_amount=paid,
		received_amount=paid,
		source_exchange_rate=1,
		target_exchange_rate=1,
		references=[],
		deductions=[],
		taxes=[],
	)
	doc.update(values)
	for ref in references if references is not None else [("Sales Invoice", "SINV-1", allocated, allocated)]:
		doc.append(
			"references",
			{
				"reference_doctype": ref[0],
				"reference_name": ref[1],
				"allocated_amount": ref[2],
				"total_amount": ref[3],
			},
		)
	for row in deductions or []:
		doc.append("deductions", row)
	doc.set_amounts()
	return doc


def journal(lines, **values):
	doc = Journal(
		doctype="Journal Entry",
		name="new-journal-entry-1",
		__islocal=1,
		docstatus=0,
		company=CD,
		posting_date="2026-10-21",
		voucher_type="Journal Entry",
		multi_currency=0,
		accounts=[],
	)
	doc.update(values)
	for line in lines:
		row = {"exchange_rate": 1, "debit_in_account_currency": 0, "credit_in_account_currency": 0, **line}
		doc.append("accounts", row)
	doc.set_amounts_in_company_currency()
	doc.set_total_debit_credit()
	return doc


def invoice(name="SINV-1", net=50000, grand=53500, items=None, company=CD):
	source = Doc(
		doctype="Sales Invoice",
		name=name,
		company=company,
		currency="THB",
		base_net_total=net,
		grand_total=grand,
		rounded_total=grand,
		base_grand_total=grand,
		base_rounded_total=grand,
		items=[Row(item) for item in (items or [{"item_code": "SERVICE-FEE", "base_net_amount": net}])],
	)
	source.check_permission = Mock()
	return source


class World:
	"""The settings and documents frappe answers with."""

	def __init__(self):
		self.categories = {
			SERVICE: {"category_name": "ค่าบริการ", "thai_applies_to": "Pay and Receive", "rate": 3},
			RENT: {"category_name": "ค่าเช่า", "thai_applies_to": "Pay", "rate": 5},
			PRIZE: {"category_name": "รางวัล", "thai_applies_to": "Receive", "rate": 5},
		}
		self.rows = {
			(SERVICE, CD): {
				"account": "WHT Payable - CD",
				"customer_withholding_account": "WHT Receivable - CD",
			},
			(RENT, CD): {"account": "WHT Payable - CD", "customer_withholding_account": None},
			(PRIZE, CD): {"account": None, "customer_withholding_account": "WHT Receivable - CD"},
		}
		self.items = {"SERVICE-FEE": SERVICE}
		self.sources = {}
		self.fields = {
			("Tax Withholding Account", "customer_withholding_account"),
			("Tax Withholding Category", "thai_applies_to"),
		}
		self.cost_centers = {}

	def has_field(self, doctype, fieldname):
		return (doctype, fieldname) in self.fields

	def get_value(self, doctype, filters=None, fieldname=None, as_dict=False, **kwargs):
		if doctype == "Tax Withholding Account":
			row = self.rows.get((filters["parent"], filters["company"]))
			return frappe._dict(row) if row else None
		if doctype == "Tax Withholding Category":
			details = self.categories.get(filters)
			if not details:
				return None
			return frappe._dict(
				name=filters,
				category_name=details["category_name"],
				round_off_tax_amount=details.get("round_off", 0),
				thai_applies_to=details.get("thai_applies_to"),
			)
		raise AssertionError(f"unexpected get_value {doctype}")

	def get_all(self, doctype, filters=None, fields=None, **kwargs):
		if doctype == "Tax Withholding Rate":
			details = self.categories.get(filters["parent"])
			rate = details and details.get("rate")
			return (
				[] if rate is None else [frappe._dict(tax_withholding_rate=rate, tax_withholding_group=None)]
			)
		if doctype == "Tax Withholding Category":
			return [
				frappe._dict(
					name=name,
					category_name=details["category_name"],
					thai_type_of_income="5",
					thai_income_tax_form="",
					round_off_tax_amount=details.get("round_off", 0),
					thai_applies_to=details.get("thai_applies_to"),
				)
				for name, details in sorted(self.categories.items())
			]
		if doctype == "Withholding Tax Type Of Income":
			return [("5", "Contract work, services, rent, transportation")]
		raise AssertionError(f"unexpected get_all {doctype}")

	def get_cached_value(self, doctype, name, fieldname, as_dict=False):
		if doctype == "Account":
			details = ACCOUNTS[name]
			if isinstance(fieldname, list | tuple):
				return frappe._dict({field: details.get(field) for field in fieldname})
			return details.get(fieldname)
		if doctype == "Company":
			return {"default_currency": "THB", "cost_center": self.cost_centers.get(name)}[fieldname]
		if doctype == "Item":
			return self.items.get(name)
		raise AssertionError(f"unexpected get_cached_value {doctype}")

	def get_doc(self, doctype, name=None):
		return self.sources[(doctype, name)]

	@contextlib.contextmanager
	def active(self):
		def throw(message, *args, **kwargs):
			raise frappe.ValidationError(message)

		with (
			patch.object(withholding, "_", side_effect=lambda text: text),
			patch.object(withholding, "has_field", side_effect=self.has_field),
			patch.object(frappe, "throw", side_effect=throw),
			patch.object(frappe, "get_all", side_effect=self.get_all),
			patch.object(frappe, "get_cached_value", side_effect=self.get_cached_value),
			patch.object(frappe, "get_doc", side_effect=self.get_doc),
			patch.object(frappe, "has_permission", return_value=True) as has_permission,
			patch.object(frappe, "db", MagicMock(get_value=Mock(side_effect=self.get_value))),
			patch.object(frappe, "format", side_effect=lambda value, *args: str(value)),
		):
			self.has_permission = has_permission
			yield self


class TestTypesOfWithholding(unittest.TestCase):
	def test_applies_to_names_the_directions_and_blank_means_both(self):
		self.assertEqual(withholding.directions(None), ("Pay", "Receive"))
		self.assertEqual(withholding.directions("Pay and Receive"), ("Pay", "Receive"))
		self.assertEqual(withholding.directions("Pay"), ("Pay",))
		self.assertEqual(withholding.directions("Receive"), ("Receive",))

	def test_receive_popup_lists_only_types_set_up_for_receipts_in_the_company(self):
		with World().active():
			choices = withholding.get_choices(CD, "2026-10-21", "Receive")
		self.assertEqual([c["name"] for c in choices], [PRIZE, SERVICE])
		service = choices[1]
		self.assertEqual(service["label"], "ค่าบริการ 3%")
		self.assertEqual(service["account"], "WHT Receivable - CD")
		self.assertEqual(service["rate"], 3)
		self.assertEqual(service["type_of_income"], "5")
		self.assertTrue(service["type_of_income_description"].startswith("Contract work"))

	def test_pay_popup_lists_types_with_a_pay_account_and_never_receive_only_ones(self):
		with World().active():
			choices = withholding.get_choices(CD, "2026-10-21", "Pay")
		self.assertEqual([c["name"] for c in choices], [RENT, SERVICE])
		self.assertEqual({c["account"] for c in choices}, {"WHT Payable - CD"})

	def test_a_type_without_a_rate_on_the_date_or_an_account_in_the_company_is_left_out(self):
		world = World()
		world.categories[SERVICE]["rate"] = None
		with world.active():
			self.assertEqual(
				[c["name"] for c in withholding.get_choices(CD, "2026-10-21", "Receive")], [PRIZE]
			)
			self.assertEqual(withholding.get_choices(UB, "2026-10-21", "Receive"), [])

	def test_the_old_pay_popup_endpoint_asks_for_the_pay_direction(self):
		with patch.object(withholding, "get_choices", return_value=["choice"]) as choices:
			self.assertEqual(custom_api.get_withholding_category_choices(CD, "2026-10-21"), ["choice"])
		choices.assert_called_once_with(CD, "2026-10-21", "Pay")

	def test_an_unknown_direction_is_refused(self):
		with World().active(), self.assertRaises(frappe.ValidationError):
			withholding.get_withholding_choices(CD, "2026-10-21", "Sideways")


class TestCategoryAccounts(unittest.TestCase):
	def category(self, applies_to, rows):
		return Doc(
			doctype="Tax Withholding Category",
			name="Service",
			thai_applies_to=applies_to,
			accounts=[
				Row(company=c, account=p, customer_withholding_account=r, idx=i)
				for i, (c, p, r) in enumerate(rows, 1)
			],
		)

	def validate(self, category):
		with World().active():
			withholding.validate_category_accounts(category)

	def test_each_direction_needs_its_account_on_every_company_row(self):
		with self.assertRaisesRegex(frappe.ValidationError, "Pay Account .*Applies To includes Pay"):
			self.validate(self.category("Pay and Receive", [(CD, None, "WHT Receivable - CD")]))
		with self.assertRaisesRegex(
			frappe.ValidationError, r"Row #2: set the Receive Account .*Applies To includes Receive"
		):
			self.validate(
				self.category(
					"Pay and Receive",
					[(CD, "WHT Payable - CD", "WHT Receivable - CD"), (UB, "WHT Payable - UB", None)],
				)
			)

	def test_a_one_direction_type_needs_only_its_own_account(self):
		self.validate(self.category("Pay", [(CD, "WHT Payable - CD", None)]))
		self.validate(self.category("Receive", [(CD, None, "WHT Receivable - CD")]))

	def test_each_account_belongs_to_the_row_company(self):
		with self.assertRaisesRegex(
			frappe.ValidationError, "Receive Account WHT Receivable - UB does not belong"
		):
			self.validate(self.category("Receive", [(CD, None, "WHT Receivable - UB")]))
		with self.assertRaisesRegex(frappe.ValidationError, "Pay Account WHT Payable - UB does not belong"):
			self.validate(self.category("Pay", [(CD, "WHT Payable - UB", None)]))

	def test_the_receive_account_is_an_asset_without_party_and_not_the_pay_account(self):
		for account, message in (
			("Write Off - CD", "must be an Asset account"),
			("Debtors - CD", "cannot be a Receivable account"),
			("Current Assets - CD", "account group"),
		):
			with self.subTest(account=account), self.assertRaisesRegex(frappe.ValidationError, message):
				self.validate(self.category("Receive", [(CD, None, account)]))
		with self.assertRaisesRegex(frappe.ValidationError, "different accounts for Pay and Receive"):
			self.validate(
				self.category("Pay and Receive", [(CD, "WHT Receivable - CD", "WHT Receivable - CD")])
			)

	def test_a_company_twice_is_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "added multiple times"):
			self.validate(
				self.category("Pay", [(CD, "WHT Payable - CD", None), (CD, "WHT Payable - CD", None)])
			)

	def test_the_category_controller_uses_these_checks_and_refuses_a_pay_account_it_lacks(self):
		from erpnext.accounts.doctype.tax_withholding_category.tax_withholding_category import (
			TaxWithholdingCategory,
		)

		class Category(withholding.TaxWithholdingCategory, TaxWithholdingCategory):
			def __init__(self, rows):
				self.name = "Prize"
				self.accounts = rows

			def get(self, key, default=None):
				return getattr(self, key, default)

		with patch.object(withholding, "validate_category_accounts") as check:
			Category([]).validate_companies_and_accounts()
		check.assert_called_once()
		category = Category([Row(company=CD, account=None), Row(company=UB, account="WHT Payable - UB")])
		with World().active():
			with self.assertRaisesRegex(frappe.ValidationError, "no Pay Account for Connect Diagnostics"):
				category.get_company_account(CD)
			self.assertEqual(category.get_company_account(UB), "WHT Payable - UB")


class TestReceiveBase(unittest.TestCase):
	def test_payment_base_is_the_invoices_net_before_vat_for_the_share_paid(self):
		world = World()
		world.sources[("Sales Invoice", "SINV-1")] = invoice()
		world.sources[("Sales Invoice", "SINV-2")] = invoice("SINV-2", net=10000, grand=10700)
		world.sources[("Sales Order", "SO-1")] = Doc(
			doctype="Sales Order",
			company=CD,
			currency="THB",
			base_net_total=20000,
			grand_total=21400,
			rounded_total=21400,
			items=[],
			check_permission=Mock(),
		)
		doc = receipt(
			references=[
				("Sales Invoice", "SINV-1", 53500, 53500),
				("Sales Invoice", "SINV-2", 5350, 10700),
				("Sales Order", "SO-1", 10700, 21400),
			],
			paid=69550,
		)
		with world.active():
			self.assertEqual(withholding.get_receive_base(doc), 50000 + 5000 + 10000)
		world.sources[("Sales Invoice", "SINV-1")].check_permission.assert_called_once_with("read")

	def test_a_credit_note_paid_with_the_invoice_lowers_the_base(self):
		world = World()
		world.sources[("Sales Invoice", "SINV-1")] = invoice(net=10000, grand=10700)
		world.sources[("Sales Invoice", "CN-1")] = invoice("CN-1", net=-1000, grand=-1070)
		doc = receipt(
			references=[("Sales Invoice", "SINV-1", 10700, 10700), ("Sales Invoice", "CN-1", -1070, -1070)],
			paid=9630,
		)
		with world.active():
			self.assertEqual(withholding.get_receive_base(doc), 9000)

	def test_a_receipt_that_settles_no_invoice_counts_what_it_received(self):
		with World().active():
			self.assertEqual(withholding.get_receive_base(receipt(paid=10700, references=[])), 10700)

	def test_journal_base_reads_the_invoice_of_each_customer_line_or_the_credit_itself(self):
		world = World()
		world.sources[("Sales Invoice", "SINV-1")] = invoice(net=10000, grand=10700)
		doc = journal(
			[
				{"account": "Bank - CD", "debit_in_account_currency": 15700},
				{
					"account": "Debtors - CD",
					"party_type": "Customer",
					"party": "4A058",
					"account_currency": "THB",
					"reference_type": "Sales Invoice",
					"reference_name": "SINV-1",
					"credit_in_account_currency": 10700,
				},
				{
					"account": "Debtors - CD",
					"party_type": "Customer",
					"party": "4A058",
					"credit_in_account_currency": 5000,
				},
				{
					"account": "Debtors - CD",
					"party_type": "Supplier",
					"party": "S-1",
					"credit_in_account_currency": 0,
				},
			]
		)
		with world.active():
			self.assertEqual(withholding.get_receive_base(doc), 10000 + 5000)

	def test_a_document_of_another_company_or_unreadable_is_refused(self):
		world = World()
		world.sources[("Sales Invoice", "SINV-1")] = invoice(company=UB)
		with world.active(), self.assertRaisesRegex(frappe.ValidationError, "another company"):
			withholding.get_receive_base(receipt())
		world.sources[("Sales Invoice", "SINV-1")] = invoice()
		world.sources[("Sales Invoice", "SINV-1")].check_permission.side_effect = frappe.PermissionError
		with world.active(), self.assertRaises(frappe.PermissionError):
			withholding.get_receive_base(receipt())

	def test_line_items_group_the_items_types_for_the_share_paid(self):
		world = World()
		world.sources[("Sales Invoice", "SINV-1")] = invoice(
			net=30000,
			grand=32100,
			items=[
				{"item_code": "SERVICE-FEE", "base_net_amount": 10000},
				{"item_code": "OTHER", "tax_withholding_category": RENT, "base_net_amount": 15000},
				{"item_code": "GOODS", "base_net_amount": 5000},
			],
		)
		with world.active():
			bases = withholding.get_receive_item_bases(
				receipt(references=[("Sales Invoice", "SINV-1", 16050, 32100)])
			)
		self.assertEqual(bases, {RENT: 7500, SERVICE: 5000})


class TestPlanRows(unittest.TestCase):
	def setUp(self):
		self.world = World()
		self.world.sources[("Sales Invoice", "SINV-1")] = invoice()

	def plan(self, doc=None, **kwargs):
		with self.world.active():
			return withholding.plan_receive_rows(doc or receipt(), **kwargs)

	def test_the_amount_is_the_rate_of_the_base_and_can_be_typed(self):
		(row,) = self.plan(category=SERVICE)
		self.assertEqual(
			(row.account, row.base, row.rate, row.amount), ("WHT Receivable - CD", 50000, 3, 1500)
		)
		self.assertEqual(row.description, "Withholding tax ค่าบริการ 3%")
		(row,) = self.plan(category=SERVICE, base_amount=10000, withholding_amount=299.5)
		self.assertEqual((row.base, row.amount), (10000, 299.5))

	def test_a_type_that_rounds_off_withholds_whole_baht(self):
		self.world.categories[SERVICE]["round_off"] = 1
		(row,) = self.plan(category=SERVICE, base_amount=10010)
		self.assertEqual(row.amount, 300)

	def test_types_not_set_up_for_receipts_are_refused(self):
		for category, message in (
			(RENT, "not set up for receipts"),
			("Missing", "does not exist"),
		):
			with self.subTest(category=category), self.assertRaisesRegex(frappe.ValidationError, message):
				self.plan(category=category)
		self.world.rows[(SERVICE, CD)]["customer_withholding_account"] = None
		with self.assertRaisesRegex(frappe.ValidationError, "Set the Receive Account"):
			self.plan(category=SERVICE)
		self.world.rows[(SERVICE, CD)]["customer_withholding_account"] = "WHT Receivable - CD"
		self.world.categories[SERVICE]["rate"] = None
		with self.assertRaisesRegex(frappe.ValidationError, "has no rate"):
			self.plan(category=SERVICE)

	def test_amounts_that_cannot_be_are_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "more than zero"):
			self.plan(category=SERVICE, withholding_amount=0)
		with self.assertRaisesRegex(frappe.ValidationError, "more than its base"):
			self.plan(category=SERVICE, base_amount=100, withholding_amount=150)
		with self.assertRaisesRegex(frappe.ValidationError, "Choose the type"):
			self.plan()

	def test_line_items_without_any_type_say_so(self):
		self.world.items = {}
		with self.assertRaisesRegex(frappe.ValidationError, "Use Based On Manual Selection"):
			self.plan(based_on="Line Items")
		self.world.items = {"SERVICE-FEE": SERVICE}
		(row,) = self.plan(based_on="Line Items")
		self.assertEqual((row.name, row.amount), (SERVICE, 1500))


class TestReceiptBalances(unittest.TestCase):
	def setUp(self):
		self.world = World()
		self.world.sources[("Sales Invoice", "SINV-1")] = invoice()

	def apply(self, doc, **kwargs):
		kwargs.setdefault("tax_withholding_category", SERVICE)
		with self.world.active():
			return withholding.apply_receive_withholding(doc, **kwargs)

	def wht(self, doc):
		return [
			(row.account, row.amount, row.thai_withholding_offset)
			for row in withholding.withholding_rows(doc)
		]

	def test_a_receipt_entered_gross_receives_the_net_and_balances(self):
		doc = receipt()
		out = self.apply(doc)
		self.assertEqual(doc.paid_amount, 52000)
		self.assertEqual(self.wht(doc), [("WHT Receivable - CD", 1500, 1500)])
		self.assertEqual(doc.deductions[0].description, "Withholding tax ค่าบริการ 3%")
		self.assertEqual(doc.deductions[0].thai_withholding_base, 50000)
		self.assertEqual((doc.unallocated_amount, doc.difference_amount), (0, 0))
		self.assertEqual((out["withheld"], out["money_reduced"], out["paid_amount"]), (1500, True, 52000))

	def test_a_receipt_of_the_net_money_keeps_it_and_the_tax_fills_the_gap(self):
		doc = receipt(paid=52000)
		out = self.apply(doc)
		self.assertEqual(doc.paid_amount, 52000)
		self.assertEqual(self.wht(doc), [("WHT Receivable - CD", 1500, 0)])
		self.assertEqual((doc.unallocated_amount, doc.difference_amount, out["money_reduced"]), (0, 0, False))

	def test_running_it_again_replaces_the_rows_and_puts_the_money_back_first(self):
		doc = receipt(deductions=[{"account": "Write Off - CD", "amount": 0.5}])
		self.apply(doc)
		self.apply(doc, withholding_amount=1499.5)
		self.assertEqual(self.wht(doc), [("WHT Receivable - CD", 1499.5, 1499.5)])
		self.assertEqual(doc.paid_amount, 52000.5)
		self.assertEqual([row.account for row in doc.deductions], ["Write Off - CD", "WHT Receivable - CD"])
		net = receipt(paid=52000)
		self.apply(net)
		self.apply(net, withholding_amount=1499.5)
		self.assertEqual((net.paid_amount, self.wht(net)), (52000, [("WHT Receivable - CD", 1499.5, 0)]))
		self.assertEqual(net.difference_amount, 0.5)

	def test_the_row_gets_the_company_cost_center_when_cost_center_is_used(self):
		doc = receipt()
		self.apply(doc)
		self.assertNotIn("cost_center", doc.deductions[0])
		self.world.fields |= {("Payment Entry Deduction", "cost_center"), ("Company", "cost_center")}
		self.world.cost_centers[CD] = "Main - CD"
		doc = receipt()
		self.apply(doc)
		self.assertEqual(doc.deductions[0].cost_center, "Main - CD")

	def test_only_a_receipt_from_a_customer_takes_deductions(self):
		doc = receipt(payment_type="Pay", party_type="Supplier")
		with self.assertRaisesRegex(frappe.ValidationError, "receipt from a Customer"):
			self.apply(doc)
		doc = receipt(paid=1000, allocated=1000, references=[("Sales Invoice", "SINV-1", 1000, 53500)])
		with self.assertRaisesRegex(frappe.ValidationError, "more than the customer pays"):
			self.apply(doc, withholding_amount=1000, base_amount=50000)


class TestJournalBalances(unittest.TestCase):
	def setUp(self):
		self.world = World()
		self.world.sources[("Sales Invoice", "SINV-1")] = invoice(net=10000, grand=10700)

	def lines(self, bank=10700, banks=1):
		customer = {
			"account": "Debtors - CD",
			"party_type": "Customer",
			"party": "4A058",
			"account_currency": "THB",
			"reference_type": "Sales Invoice",
			"reference_name": "SINV-1",
			"credit_in_account_currency": 10700,
			"department": "Finance - CD",
		}
		if banks == 2:
			return [
				{"account": "Bank - CD", "debit_in_account_currency": bank - 700},
				{"account": "Cash - CD", "debit_in_account_currency": 700},
				customer,
			]
		return [{"account": "Bank - CD", "debit_in_account_currency": bank}, customer]

	def apply(self, doc, **kwargs):
		kwargs.setdefault("tax_withholding_category", SERVICE)
		with self.world.active():
			return withholding.apply_receive_withholding(doc, **kwargs)

	def debit(self, doc, account):
		return sum(row.debit for row in doc.accounts if row.account == account)

	def test_a_balanced_journal_with_one_bank_line_receives_the_net(self):
		doc = journal(self.lines())
		out = self.apply(doc)
		self.assertEqual(self.debit(doc, "Bank - CD"), 10400)
		(row,) = withholding.withholding_rows(doc)
		self.assertEqual(
			(row.account, row.debit, row.thai_withholding_offset), ("WHT Receivable - CD", 300, 300)
		)
		self.assertEqual((row.user_remark, row.department), ("Withholding tax ค่าบริการ 3%", "Finance - CD"))
		self.assertFalse(row.get("party") or row.get("credit"))
		self.assertEqual((doc.total_debit, doc.total_credit, doc.difference), (10700, 10700, 0))
		self.assertEqual((out["money_reduced"], out["bank_account"]), (True, "Bank - CD"))

	def test_a_journal_holding_the_money_received_keeps_the_bank_line(self):
		doc = journal(self.lines(bank=10400))
		self.apply(doc)
		self.assertEqual(self.debit(doc, "Bank - CD"), 10400)
		self.assertEqual(doc.difference, 0)
		self.assertEqual(withholding.withholding_rows(doc)[0].thai_withholding_offset, 0)

	def test_running_it_again_replaces_the_line(self):
		doc = journal(self.lines())
		self.apply(doc)
		self.apply(doc, withholding_amount=299.5)
		(row,) = withholding.withholding_rows(doc)
		self.assertEqual(row.debit, 299.5)
		self.assertEqual(self.debit(doc, "Bank - CD"), 10400.5)
		self.assertEqual(doc.difference, 0)

	def test_lines_the_form_has_not_rated_yet_count_in_company_currency(self):
		# a journal as typed: amounts in the account's currency, no rate and no company-currency amounts yet
		doc = journal(self.lines())
		for row in doc.accounts:
			row.update(exchange_rate=0, debit=0, credit=0, account_currency="THB")
		with self.world.active():
			self.assertEqual(withholding.get_receive_base(doc), 10000)
		self.apply(doc)
		self.assertEqual(self.debit(doc, "Bank - CD"), 10400)
		self.assertEqual((doc.total_debit, doc.total_credit), (10700, 10700))
		unreferenced = journal(
			[
				{"account": "Bank - CD", "debit_in_account_currency": 5000},
				{
					"account": "Debtors - CD",
					"party_type": "Customer",
					"party": "4A058",
					"credit_in_account_currency": 5000,
				},
			]
		)
		for row in unreferenced.accounts:
			row.update(exchange_rate=0, debit=0, credit=0)
		with self.world.active():
			self.assertEqual(withholding.get_receive_base(unreferenced), 5000)

	def test_with_several_bank_lines_the_tax_is_added_and_the_difference_shown(self):
		doc = journal(self.lines(banks=2))
		out = self.apply(doc)
		self.assertEqual(out["money_reduced"], False)
		self.assertEqual(doc.difference, 300)

	def test_it_needs_a_customer_credit_and_no_supplier_withholding(self):
		doc = journal(
			[
				{"account": "Bank - CD", "debit_in_account_currency": 100},
				{"account": "Write Off - CD", "credit_in_account_currency": 100},
			]
		)
		with self.assertRaisesRegex(frappe.ValidationError, "credits a Customer"):
			self.apply(doc)
		doc = journal(self.lines(), apply_tds=1)
		with self.assertRaisesRegex(frappe.ValidationError, "Untick Consider for Tax Withholding"):
			self.apply(doc)


class TestPermissions(unittest.TestCase):
	def test_a_new_entry_needs_create_and_a_saved_one_write(self):
		world = World()
		world.sources[("Sales Invoice", "SINV-1")] = invoice()
		doc = receipt()
		with world.active():
			withholding.apply_receive_withholding(doc, SERVICE)
			world.has_permission.assert_called_once_with("Payment Entry", "create", throw=True)
		saved = receipt()
		saved.pop("__islocal")
		saved.check_permission = Mock(side_effect=frappe.PermissionError)
		with world.active(), self.assertRaises(frappe.PermissionError):
			withholding.apply_receive_withholding(saved, SERVICE)
		saved.check_permission.assert_called_once_with("write")
		self.assertEqual(withholding.withholding_rows(saved), [])

	def test_a_submitted_entry_is_left_alone(self):
		with World().active(), self.assertRaisesRegex(frappe.ValidationError, "works on a draft"):
			withholding.apply_receive_withholding(receipt(docstatus=1), SERVICE)

	def test_the_popup_reads_through_the_same_checks(self):
		world = World()
		world.sources[("Sales Invoice", "SINV-1")] = invoice()
		doc = receipt(
			deductions=[
				{"account": "WHT Receivable - CD", "amount": 1500, "thai_withholding_category": SERVICE}
			]
		)
		with world.active(), patch.object(withholding.frappe, "parse_json", return_value=doc):
			world.get_doc = lambda doctype, name=None: (
				doc if isinstance(doctype, dict | Doc) else world.sources[(doctype, name)]
			)
			with patch.object(frappe, "get_doc", side_effect=world.get_doc):
				out = withholding.get_receive_withholding_dialog("{}")
		self.assertEqual([c["name"] for c in out["choices"]], [PRIZE, SERVICE])
		self.assertEqual((out["base"], out["current"], out["settles_documents"]), (50000, SERVICE, True))
		world.has_permission.assert_called_with("Payment Entry", "create", throw=True)


class TestSaveChecks(unittest.TestCase):
	def check(self, doc):
		with World().active():
			withholding.validate_receive_rows(doc)

	def test_a_receipt_row_goes_to_the_receive_account_of_its_type(self):
		row = {"account": "WHT Receivable - CD", "amount": 1500, "thai_withholding_category": SERVICE}
		self.check(receipt(deductions=[row]))
		with self.assertRaisesRegex(frappe.ValidationError, "goes to WHT Receivable - CD"):
			self.check(receipt(deductions=[{**row, "account": "Write Off - CD"}]))
		with self.assertRaisesRegex(frappe.ValidationError, "more than zero"):
			self.check(receipt(deductions=[{**row, "amount": 0}]))
		with self.assertRaisesRegex(frappe.ValidationError, "receipt from a Customer"):
			self.check(receipt(deductions=[row], payment_type="Pay", party_type="Supplier"))
		self.check(receipt(deductions=[{"account": "Write Off - CD", "amount": 10}]))

	def test_a_journal_line_is_a_debit_without_party_next_to_a_customer_credit(self):
		customer = {
			"account": "Debtors - CD",
			"party_type": "Customer",
			"party": "4A058",
			"credit_in_account_currency": 10700,
		}
		wht = {
			"account": "WHT Receivable - CD",
			"debit_in_account_currency": 300,
			"thai_withholding_category": SERVICE,
		}
		bank = {"account": "Bank - CD", "debit_in_account_currency": 10400}
		self.check(journal([bank, customer, wht]))
		with self.assertRaisesRegex(frappe.ValidationError, "debit without a party"):
			self.check(journal([bank, customer, {**wht, "party_type": "Customer", "party": "4A058"}]))
		with self.assertRaisesRegex(frappe.ValidationError, "credits that customer"):
			self.check(
				journal([bank, wht, {"account": "Write Off - CD", "credit_in_account_currency": 10700}])
			)
		with self.assertRaisesRegex(frappe.ValidationError, "cannot both"):
			self.check(journal([bank, customer, wht], apply_tds=1))


class TestMigration(unittest.TestCase):
	def test_types_made_before_applies_to_withhold_from_suppliers_only(self):
		db = MagicMock(has_column=Mock(return_value=True))
		with (
			patch.object(frappe, "db", db),
			patch.object(
				frappe,
				"get_all",
				side_effect=lambda doctype, **kwargs: (
					["Service"]
					if doctype == "Tax Withholding Account"
					else [
						("Service", "Pay and Receive"),
						("Rent", "Pay and Receive"),
						("Old", None),
						("Prize", "Receive"),
					]
				),
			),
		):
			withholding.set_withholding_directions()
		self.assertEqual(
			db.set_value.call_args_list,
			[
				call("Tax Withholding Category", "Rent", "thai_applies_to", "Pay", update_modified=False),
				call("Tax Withholding Category", "Old", "thai_applies_to", "Pay", update_modified=False),
			],
		)
		db = MagicMock(has_column=Mock(return_value=False))
		with patch.object(frappe, "db", db):
			withholding.set_withholding_directions()
		db.set_value.assert_not_called()


class Cert(SimpleNamespace):
	def append(self, table, row):
		self.withholding_tax_items.append(frappe._dict(row))

	def get(self, key, default=None):
		return getattr(self, key, default)


class TestCertificateOnSubmit(unittest.TestCase):
	def setUp(self):
		self.made = []
		self.messages = []
		self.categories = {
			"Advertising": {
				"category_name": "ค่าโฆษณา",
				"thai_type_of_income": "5",
				"thai_income_tax_form": None,
			},
			"Services": {
				"category_name": "ค่าบริการ",
				"thai_type_of_income": "5",
				"thai_income_tax_form": None,
			},
			"Interest": {
				"category_name": "ดอกเบี้ย",
				"thai_type_of_income": "4",
				"thai_income_tax_form": "PND3",
			},
			"NoType": {"category_name": "อื่น ๆ", "thai_type_of_income": None, "thai_income_tax_form": None},
		}
		self.existing = []
		self.supplier = {
			"supplier_name": "บริษัท พัฒนา",
			"supplier_primary_address": "B038-Office",
			"supplier_type": "Company",
		}

	def payment(self, entries, **values):
		doc = frappe._dict(
			doctype="Payment Entry",
			name="ACC-PAY-1",
			company=CD,
			posting_date="2026-10-21",
			payment_type="Pay",
			party_type="Supplier",
			party="B038",
			company_tax_address="CD-Billing",
			tax_withholding_entries=[frappe._dict(e) for e in entries],
		)
		doc.update(values)
		return doc

	def get_value(self, doctype, name, fields=None, as_dict=False):
		if doctype == "Tax Withholding Category":
			return frappe._dict(self.categories[name])
		if doctype == "Supplier":
			if fields == "supplier_type":
				return self.supplier["supplier_type"]
			return frappe._dict({f: self.supplier[f] for f in fields})
		raise AssertionError(doctype)

	def new_doc(self, doctype):
		cert = Cert(
			doctype=doctype,
			name=f"WHT-CERT-{len(self.made) + 1}",
			withholding_tax_items=[],
			flags=frappe._dict(),
		)
		cert.insert = Mock()
		cert.submit = Mock()
		self.made.append(cert)
		return cert

	@contextlib.contextmanager
	def active(self, insert_error=None):
		db = MagicMock(get_value=Mock(side_effect=self.get_value))
		with (
			patch.object(withholding_cert, "_", side_effect=lambda text: text),
			patch.object(frappe, "db", db),
			patch.object(frappe, "new_doc", side_effect=self.new_doc),
			patch.object(
				frappe,
				"msgprint",
				side_effect=lambda message, **kwargs: self.messages.append(
					(kwargs.get("indicator"), message)
				),
			),
			patch.object(frappe, "log_error") as log_error,
			patch.object(withholding_cert, "get_link_to_form", side_effect=lambda dt, name: name),
			patch.object(
				withholding_cert,
				"existing_certificates",
				side_effect=lambda dt, dn: {n: v for n, v in self.existing},
			),
			patch.object(
				withholding_cert,
				"company_address_of",
				side_effect=lambda voucher: voucher.get("company_tax_address"),
			),
			patch.object(withholding_cert, "linked_address", return_value=None),
		):
			self.db = db
			self.log_error = log_error
			yield

	def test_a_payment_that_withheld_gets_its_certificate_submitted(self):
		entries = [
			{
				"tax_withholding_category": "Advertising",
				"taxable_amount": 10000,
				"tax_rate": 2,
				"withholding_amount": 200,
			},
			{
				"tax_withholding_category": "Services",
				"taxable_amount": 5000,
				"tax_rate": 3,
				"withholding_amount": 150,
			},
			{
				"tax_withholding_category": "Services",
				"taxable_amount": 1000,
				"tax_rate": 3,
				"withholding_amount": 30,
			},
		]
		with self.active():
			made = withholding_cert.create_withholding_certificates(self.payment(entries))
		(cert,) = self.made
		self.assertEqual(made, ["WHT-CERT-1"])
		self.assertEqual(
			(cert.company, cert.company_address, cert.supplier, cert.supplier_address, cert.income_tax_form),
			(CD, "CD-Billing", "B038", "B038-Office", "PND53"),
		)
		self.assertEqual(
			(cert.date, cert.tax_payer, cert.voucher_type, cert.voucher_no),
			("2026-10-21", "Withholding", "Payment Entry", "ACC-PAY-1"),
		)
		self.assertEqual(
			[
				(r.type_of_income, r.tax_rate, r.tax_base, r.tax_amount, r.description)
				for r in cert.withholding_tax_items
			],
			[("5", 2.0, 10000.0, 200.0, "ค่าโฆษณา"), ("5", 3.0, 6000.0, 180.0, "ค่าบริการ")],
		)
		self.assertTrue(cert.flags.ignore_permissions)
		cert.insert.assert_called_once_with()
		cert.submit.assert_called_once_with()
		self.db.savepoint.assert_called_once_with(withholding_cert.SAVEPOINT)
		self.assertEqual(self.messages[0][0], "green")

	def test_each_income_tax_form_gets_its_own_certificate_and_existing_ones_are_kept(self):
		self.supplier["supplier_type"] = "Individual"
		entries = [
			{
				"tax_withholding_category": "Services",
				"taxable_amount": 10000,
				"tax_rate": 3,
				"withholding_amount": 300,
			},
			{
				"tax_withholding_category": "Interest",
				"taxable_amount": 1000,
				"tax_rate": 15,
				"withholding_amount": 150,
			},
		]
		self.categories["Services"]["thai_income_tax_form"] = "PND53"
		with self.active():
			withholding_cert.create_withholding_certificates(self.payment(entries))
		self.assertEqual(sorted(cert.income_tax_form for cert in self.made), ["PND3", "PND53"])
		self.made.clear()
		self.existing = [("WHT-CERT-9", ("PND53", 1))]
		with self.active():
			withholding_cert.create_withholding_certificates(self.payment(entries))
		self.assertEqual([cert.income_tax_form for cert in self.made], ["PND3"])
		self.made.clear()
		self.existing = [("WHT-CERT-9", ("PND53", 1)), ("WHT-CERT-10", ("PND3", 0))]
		with self.active():
			self.assertEqual(withholding_cert.create_withholding_certificates(self.payment(entries)), [])
		self.assertEqual(self.made, [])

	def test_a_certificate_missing_something_stays_a_draft_and_says_what(self):
		entries = [
			{
				"tax_withholding_category": "NoType",
				"taxable_amount": 1000,
				"tax_rate": 3,
				"withholding_amount": 30,
			}
		]
		self.supplier["supplier_primary_address"] = None
		with self.active():
			withholding_cert.create_withholding_certificates(self.payment(entries))
		(cert,) = self.made
		cert.insert.assert_called_once_with()
		cert.submit.assert_not_called()
		self.assertTrue(cert.flags.ignore_mandatory)
		indicator, message = self.messages[0]
		self.assertEqual(indicator, "orange")
		self.assertIn("Supplier Address", message)
		self.assertIn("Thai Type of Income of Tax Withholding Category NoType", message)

	def test_a_failure_never_stops_the_submit(self):
		entries = [
			{
				"tax_withholding_category": "Services",
				"taxable_amount": 1000,
				"tax_rate": 3,
				"withholding_amount": 30,
			}
		]
		with self.active():
			original = self.new_doc

			def broken(doctype):
				cert = original(doctype)
				cert.submit.side_effect = frappe.ValidationError("boom")
				return cert

			with patch.object(frappe, "new_doc", side_effect=broken):
				self.assertEqual(withholding_cert.create_withholding_certificates(self.payment(entries)), [])
			self.db.rollback.assert_called_once_with(save_point=withholding_cert.SAVEPOINT)
			self.log_error.assert_called_once()
		self.assertEqual(self.messages[-1][0], "red")

	def test_receipts_and_entries_without_tax_get_none(self):
		entry = {
			"tax_withholding_category": "Services",
			"taxable_amount": 1000,
			"tax_rate": 3,
			"withholding_amount": 30,
		}
		with self.active():
			withholding_cert.create_withholding_certificates(self.payment([entry], payment_type="Receive"))
			withholding_cert.create_withholding_certificates(
				self.payment([{**entry, "withholding_amount": 0}])
			)
			withholding_cert.create_withholding_certificates(self.payment([entry], party_type="Customer"))
		self.assertEqual(self.made, [])

	def test_a_journal_uses_its_supplier_line(self):
		entry = {
			"tax_withholding_category": "Services",
			"taxable_amount": 10000,
			"tax_rate": 3,
			"withholding_amount": 300,
		}
		doc = frappe._dict(
			doctype="Journal Entry",
			name="ACC-JV-1",
			company=CD,
			posting_date="2026-10-20",
			company_tax_address=None,
			accounts=[frappe._dict(party_type=None), frappe._dict(party_type="Supplier", party="5B0012")],
			tax_withholding_entries=[frappe._dict(entry)],
		)
		with self.active(), patch.object(withholding_cert, "company_address_of", return_value="CD-Office"):
			withholding_cert.create_withholding_certificates(doc)
		(cert,) = self.made
		self.assertEqual(
			(cert.supplier, cert.voucher_type, cert.voucher_no, cert.date),
			("5B0012", "Journal Entry", "ACC-JV-1", "2026-10-20"),
		)

	def test_cancel_cancels_submitted_certificates_and_deletes_drafts(self):
		submitted = SimpleNamespace(flags=frappe._dict(), cancel=Mock())
		with (
			patch.object(
				withholding_cert,
				"existing_certificates",
				return_value={"C-1": ("PND53", 1), "C-2": ("PND3", 0)},
			),
			patch.object(frappe, "get_doc", return_value=submitted) as get_doc,
			patch.object(frappe, "delete_doc") as delete_doc,
		):
			withholding_cert.cancel_withholding_certificates(
				frappe._dict(doctype="Payment Entry", name="ACC-PAY-1")
			)
		get_doc.assert_called_once_with("Withholding Tax Cert", "C-1")
		submitted.cancel.assert_called_once_with()
		self.assertTrue(submitted.flags.ignore_permissions)
		delete_doc.assert_called_once_with("Withholding Tax Cert", "C-2", ignore_permissions=True)

	def test_an_amended_entry_makes_new_ones(self):
		entry = {
			"tax_withholding_category": "Services",
			"taxable_amount": 1000,
			"tax_rate": 3,
			"withholding_amount": 30,
		}
		self.existing = []
		with self.active() as _:
			withholding_cert.create_withholding_certificates(
				self.payment([entry], name="ACC-PAY-1-1", amended_from="ACC-PAY-1")
			)
		self.assertEqual(self.made[0].voucher_no, "ACC-PAY-1-1")

	def test_existing_certificates_reads_the_entrys_live_ones(self):
		with patch.object(
			frappe, "get_all", return_value=[frappe._dict(name="C-1", income_tax_form="PND53", docstatus=1)]
		) as get_all:
			self.assertEqual(
				withholding_cert.existing_certificates("Payment Entry", "ACC-PAY-1"), {"C-1": ("PND53", 1)}
			)
		self.assertEqual(
			get_all.call_args.kwargs["filters"],
			{"voucher_type": "Payment Entry", "voucher_no": "ACC-PAY-1", "docstatus": ("<", 2)},
		)

	def test_the_fallback_button_asks_with_the_entrys_read_permission(self):
		with (
			patch.object(frappe, "has_permission", side_effect=frappe.PermissionError) as permission,
			patch.object(withholding_cert, "existing_certificates") as existing,
			self.assertRaises(frappe.PermissionError),
		):
			withholding_cert.get_voucher_certificates("Payment Entry", "ACC-PAY-1")
		permission.assert_called_once_with("Payment Entry", "read", doc="ACC-PAY-1", throw=True)
		existing.assert_not_called()
		with (
			patch.object(withholding_cert, "_", side_effect=lambda text: text),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			self.assertRaises(frappe.ValidationError),
		):
			withholding_cert.get_voucher_certificates("Sales Invoice", "SINV-1")

	def test_the_manual_button_refuses_a_second_certificate(self):
		source = Mock()
		with (
			patch.object(payment_entry, "_", side_effect=lambda text: text),
			patch.object(frappe, "get_doc", return_value=source),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(payment_entry, "existing_certificates", return_value={"C-1": ("PND53", 1)}),
			patch.object(frappe.utils, "get_link_to_form", side_effect=lambda dt, name: name),
			patch.object(
				frappe,
				"throw",
				side_effect=lambda message, *a, **k: (_ for _ in ()).throw(frappe.ValidationError(message)),
			),
			patch.object(frappe, "new_doc") as new_doc,
			self.assertRaisesRegex(frappe.ValidationError, "already has Withholding Tax Cert C-1"),
		):
			payment_entry.make_withholding_tax_cert("{}", '{"doctype": "Payment Entry", "name": "ACC-PAY-1"}')
		source.check_permission.assert_called_once_with("read")
		new_doc.assert_not_called()


class TestConnections(unittest.TestCase):
	def test_payment_and_journal_list_their_certificates_and_tax_invoices(self):
		from erpnext.accounts.doctype.payment_entry.payment_entry_dashboard import get_data

		with patch.object(dashboard_overrides, "_", side_effect=lambda text: text):
			payment = dashboard_overrides.get_dashboard_data_for_payment_entry(frappe._dict(get_data()))
			journal_data = dashboard_overrides.get_dashboard_data_for_journal_entry(frappe._dict())
		for data, doctype in ((payment, "Payment Entry"), (journal_data, "Journal Entry")):
			with self.subTest(doctype=doctype):
				self.assertEqual(data["non_standard_fieldnames"]["Withholding Tax Cert"], "voucher_no")
				self.assertEqual(data["dynamic_links"]["voucher_no"], [doctype, "voucher_type"])
				items = [item for group in data["transactions"] for item in group["items"]]
				self.assertIn("Withholding Tax Cert", items)
				self.assertIn("Purchase Tax Invoice", items)
		self.assertEqual(
			[item for group in payment["transactions"] for item in group["items"]].count("Payment Receipt"), 1
		)


class TestWiring(unittest.TestCase):
	def test_hooks_and_fields(self):
		from erpnext import hooks
		from erpnext.regional.thailand.constants import ERP_CUSTOM_FIELDS, ERP_PROPERTY_SETTERS

		self.assertEqual(
			hooks.extend_doctype_class["Tax Withholding Category"],
			"erpnext.regional.thailand.custom.withholding.TaxWithholdingCategory",
		)
		self.assertEqual(
			hooks.doctype_js["Tax Withholding Category"], "public/js/thailand/tax_withholding_category.js"
		)
		for doctype in ("Payment Entry", "Journal Entry"):
			events = hooks.doc_events[doctype]
			self.assertIn(
				"erpnext.regional.thailand.custom.withholding_cert.create_withholding_certificates",
				events["on_submit"],
			)
			cancel = events["on_cancel"] if isinstance(events["on_cancel"], list) else [events["on_cancel"]]
			self.assertIn(
				"erpnext.regional.thailand.custom.withholding_cert.cancel_withholding_certificates", cancel
			)
			self.assertIn(doctype, hooks.override_doctype_dashboards)
		fields = {dt: {f["fieldname"] for f in rows} for dt, rows in ERP_CUSTOM_FIELDS.items()}
		self.assertIn("customer_withholding_account", fields["Tax Withholding Account"])
		for doctype in ("Payment Entry Deduction", "Journal Entry Account"):
			self.assertTrue(
				{"thai_withholding_category", "thai_withholding_base", "thai_withholding_offset"}
				<= fields[doctype]
			)
		# the retired Thai-only fields (migration.remove_duplicate_withholding_fields deletes them) are not back
		self.assertFalse({"withholding_tax_type", "withholding_tax_base"} & fields["Payment Entry Deduction"])
		labels = {(f, p): v for f, p, v, _t in ERP_PROPERTY_SETTERS["Tax Withholding Account"]}
		self.assertEqual(labels[("account", "label")], "Pay Account (tax we withhold)")
		self.assertEqual(labels[("account", "reqd")], "0")
		receive = next(
			f
			for f in ERP_CUSTOM_FIELDS["Tax Withholding Account"]
			if f["fieldname"] == "customer_withholding_account"
		)
		self.assertEqual(
			(receive["label"], receive["in_list_view"]), ("Receive Account (tax withheld by customers)", 1)
		)
		applies_to = next(
			f for f in ERP_CUSTOM_FIELDS["Tax Withholding Category"] if f["fieldname"] == "thai_applies_to"
		)
		self.assertEqual(
			(applies_to["insert_after"], applies_to["default"]), ("category_name", "Pay and Receive")
		)

	def test_documents_expose_withhold_tax_and_check_their_rows_on_save(self):
		from erpnext.regional.thailand.custom.journal_entry import JournalEntry

		for cls in (payment_entry.PaymentEntry, JournalEntry):
			with self.subTest(cls=cls.__name__):
				method = cls.apply_thai_withholding
				self.assertIn(method, frappe.whitelisted)
				with patch.object(
					payment_entry
					if cls is payment_entry.PaymentEntry
					else __import__(cls.__module__, fromlist=["x"]),
					"apply_receive_withholding",
					return_value="done",
				) as apply:
					self.assertEqual(method("doc", SERVICE, 10000, 300, "Manual"), "done")
				apply.assert_called_once_with("doc", SERVICE, 10000, 300, "Manual")


def run_unit_tests():
	result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromName(__name__))
	if not result.wasSuccessful():
		raise AssertionError("Thai withholding tests failed")
