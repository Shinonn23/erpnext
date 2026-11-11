"""Billing mappings use current authorized invoices and native Payment Entry amounts without saving."""

import unittest
from contextlib import ExitStack
from unittest.mock import Mock, call, patch

import frappe

from erpnext.accounts.doctype.payment_entry.payment_entry import PaymentEntry
from erpnext.regional.doctype import billing_payment
from erpnext.regional.doctype.purchase_billing import purchase_billing
from erpnext.regional.doctype.sales_billing import sales_billing


class Row(frappe._dict):
	def as_dict(self, **kwargs):
		return dict(self)


class Payment(frappe._dict):
	set_amounts = PaymentEntry.set_amounts
	set_received_amount = PaymentEntry.set_received_amount
	set_amounts_in_company_currency = PaymentEntry.set_amounts_in_company_currency
	set_total_allocated_amount = PaymentEntry.set_total_allocated_amount
	calculate_base_allocated_amount_for_reference = PaymentEntry.calculate_base_allocated_amount_for_reference
	set_unallocated_amount = PaymentEntry.set_unallocated_amount
	get_included_taxes = PaymentEntry.get_included_taxes
	set_exchange_gain_loss = PaymentEntry.set_exchange_gain_loss
	set_difference_amount = PaymentEntry.set_difference_amount
	allocate_amount_to_references = PaymentEntry.allocate_amount_to_references

	def precision(self, *args):
		return 2

	def set(self, field, value):
		self[field] = [Row(row) for row in value] if isinstance(value, list) else value

	def append(self, field, values):
		row = Row(values)
		self[field].append(row)
		return row

	def remove(self, row):
		self.deductions.remove(row)

	def insert(self, *args, **kwargs):
		raise AssertionError("The mapper must not insert a document")

	save = submit = insert


class TestBillingPayment(unittest.TestCase):
	def setUp(self):
		self.stack = ExitStack()
		self.addCleanup(self.stack.close)
		for module in (billing_payment,):
			self.stack.enter_context(patch.object(module, "_", lambda text: text))
		self.stack.enter_context(patch.object(frappe, "throw", side_effect=frappe.ValidationError))
		self.stack.enter_context(
			patch(
				"erpnext.accounts.doctype.payment_entry.payment_entry.get_advance_payment_doctypes",
				return_value=[],
			)
		)
		self.stack.enter_context(
			patch.object(frappe, "get_system_settings", return_value="Commercial Rounding")
		)
		self.permission = self.stack.enter_context(patch.object(frappe, "has_permission", return_value=True))
		self.stack.enter_context(
			patch.object(billing_payment, "get_accounting_dimensions", return_value=["department"])
		)
		self.get_doc = self.stack.enter_context(patch.object(frappe, "get_doc", side_effect=self.load))
		self.mapper = self.stack.enter_context(
			patch.object(billing_payment, "get_payment_entry", side_effect=self.native)
		)
		self.details = self.stack.enter_context(
			patch.object(billing_payment, "get_reference_details", side_effect=self.reference)
		)
		self.reset()

	def reset(self, purchase=False, outstanding=(100, 60, -30)):
		self.billing_doctype = "Purchase Billing" if purchase else "Sales Billing"
		self.invoice_doctype = "Purchase Invoice" if purchase else "Sales Invoice"
		self.party_field = "supplier" if purchase else "customer"
		self.table = "purchase_billing_line" if purchase else "sales_billing_line"
		self.invoice_field = "purchase_invoice" if purchase else "sales_invoice"
		self.address_field = "billing_address" if purchase else "company_address"
		self.docs = {}
		self.billing = frappe._dict(
			doctype=self.billing_doctype,
			name="BILL-1",
			docstatus=1,
			closed=0,
			company="Company",
			currency="THB",
			company_tax_address="Branch",
			total_outstanding_amount=9999,
			check_permission=Mock(),
			**{self.party_field: "Party", self.table: []},
		)
		self.docs[(self.billing_doctype, "BILL-1")] = self.billing
		for idx, amount in enumerate(outstanding):
			name = f"INV-{idx}"
			self.billing[self.table].append(
				frappe._dict({self.invoice_field: name, "outstanding_amount": 9999})
			)
			self.docs[(self.invoice_doctype, name)] = frappe._dict(
				doctype=self.invoice_doctype,
				name=name,
				docstatus=1,
				company="Company",
				currency="THB",
				outstanding_amount=amount,
				check_permission=Mock(),
				invoice_is_blocked=Mock(return_value=False),
				party_account="Party Account",
				**{self.party_field: "Party", self.address_field: "Branch"},
			)

	def load(self, doctype, name):
		return self.docs[(doctype, name)]

	def reference(self, doctype, name, *args):
		return frappe._dict(outstanding_amount=self.load(doctype, name).outstanding_amount, exchange_rate=1)

	def native(self, doctype, name, bank_account=None):
		invoice = self.load(doctype, name)
		normal = "Pay" if doctype == "Purchase Invoice" else "Receive"
		direction = (
			normal if invoice.outstanding_amount > 0 else ("Pay" if normal == "Receive" else "Receive")
		)
		party_currency = invoice.get("party_account_currency") or invoice.currency
		party_rate = invoice.get("party_exchange_rate") or 1
		party_amount = abs(invoice.outstanding_amount) - (invoice.get("discount") or 0)
		accounts = (invoice.party_account, bank_account or "Bank")
		currencies = (party_currency, "THB")
		rates = (party_rate, 1)
		amounts = (party_amount, party_amount * party_rate)
		if direction == "Pay":
			accounts, currencies, rates, amounts = (
				tuple(reversed(values)) for values in (accounts, currencies, rates, amounts)
			)
		return Payment(
			doctype="Payment Entry",
			docstatus=0,
			__islocal=1,
			payment_type=direction,
			party="Party",
			party_type="Supplier" if doctype == "Purchase Invoice" else "Customer",
			company="Company",
			paid_from=accounts[0],
			paid_to=accounts[1],
			paid_from_account_currency=currencies[0],
			paid_to_account_currency=currencies[1],
			source_exchange_rate=rates[0],
			target_exchange_rate=rates[1],
			paid_amount=amounts[0],
			received_amount=amounts[1],
			party_account_currency=party_currency,
			cost_center=invoice.get("cost_center"),
			department=invoice.get("department"),
			taxes=[],
			deductions=[
				Row(account="Discount", amount=invoice.discount * (1 if direction == "Receive" else -1))
			]
			if invoice.get("discount")
			else [],
			references=[]
			if invoice.get("empty_references")
			else [
				Row(
					reference_doctype=doctype,
					reference_name=name,
					outstanding_amount=invoice.outstanding_amount,
					allocated_amount=invoice.outstanding_amount,
					exchange_rate=party_rate,
				)
			],
		)

	def make(self):
		return billing_payment.make_billing_payment(self.billing_doctype, "BILL-1")

	def test_both_billings_map_live_signed_balances_and_stay_unsaved(self):
		for purchase in (False, True):
			with self.subTest(purchase=purchase):
				self.reset(purchase)
				payment = self.make()
				self.assertEqual(
					(payment.paid_amount, payment.received_amount, payment.total_allocated_amount),
					(130, 130, 130),
				)
				self.assertEqual([row.allocated_amount for row in payment.references], [100, 60, -30])
				self.assertEqual(payment.difference_amount, 0)
				self.assertEqual((payment.docstatus, payment.get("__islocal")), (0, 1))
				self.assertEqual(payment.get(frappe.scrub(self.billing_doctype)), "BILL-1")
				self.assertEqual(payment.company_tax_address, "Branch")
				self.billing.check_permission.assert_called_once_with("read")
				for (dt, _name), doc in self.docs.items():
					if dt == self.invoice_doctype:
						doc.check_permission.assert_called_once_with("read")
				self.docs[(self.invoice_doctype, "INV-0")].outstanding_amount = 0
				self.docs[(self.invoice_doctype, "INV-1")].outstanding_amount = 40
				fresh = self.make()
				self.assertEqual([ref.reference_name for ref in fresh.references], ["INV-1", "INV-2"])
				self.assertEqual(fresh.paid_amount, 10)
		self.permission.assert_called_with("Payment Entry", ptype="create", throw=True)

	def test_native_partial_allocation_and_foreign_currency_amounts(self):
		for purchase in (False, True):
			self.reset(purchase)
			for (dt, _name), invoice in self.docs.items():
				if dt == self.invoice_doctype:
					invoice.currency = "USD"
					invoice.party_exchange_rate = 2
			self.billing.currency = "USD"
			payment = self.make()
			self.assertEqual(
				(payment.paid_amount, payment.received_amount), (260, 130) if purchase else (130, 260)
			)
			payment.allocate_amount_to_references(80, False, True)
			self.assertEqual([ref.allocated_amount for ref in payment.references], [100, 10, -30])

	def test_native_discount_rows_are_kept_when_invoices_are_combined(self):
		for purchase in (False, True):
			self.reset(purchase, outstanding=(100, 60))
			self.docs[(self.invoice_doctype, "INV-0")].discount = 5
			self.docs[(self.invoice_doctype, "INV-1")].discount = 3
			payment = self.make()
			self.assertEqual((payment.paid_amount, payment.total_allocated_amount), (152, 160))
			self.assertEqual(payment.difference_amount, 0)

	def test_credit_with_native_term_allocation_uses_native_reference_details(self):
		self.docs[(self.invoice_doctype, "INV-2")].empty_references = True
		payment = self.make()
		self.assertEqual([ref.allocated_amount for ref in payment.references], [100, 60, -30])
		self.details.assert_called_once_with("Sales Invoice", "INV-2", "THB", "Customer", "Party")

	def test_source_permissions_and_payment_create_permission_are_required(self):
		for kind in ("billing", "invoice", "create"):
			self.reset()
			self.permission.side_effect = None
			if kind == "create":
				self.permission.side_effect = frappe.PermissionError
			else:
				doc = self.billing if kind == "billing" else self.docs[(self.invoice_doctype, "INV-1")]
				doc.check_permission.side_effect = frappe.PermissionError
			with self.subTest(kind=kind), self.assertRaises(frappe.PermissionError):
				self.make()

	def test_invalid_billing_and_invoice_sources_are_refused_before_mapping(self):
		for purchase in (False, True):
			for target, values in (
				("billing", {"docstatus": 0}),
				("billing", {"docstatus": 2}),
				("billing", {"closed": 1}),
				("invoice", {"docstatus": 0}),
				("invoice", {"docstatus": 2}),
				("invoice", {"company": "Other"}),
				("invoice", {"currency": "USD"}),
				("party", {}),
				("branch", {}),
				("duplicate", {}),
				("empty", {}),
			):
				with self.subTest(purchase=purchase, target=target, values=values):
					self.reset(purchase)
					self.mapper.reset_mock()
					invoice = self.docs[(self.invoice_doctype, "INV-1")]
					if target == "billing":
						self.billing.update(values)
					elif target == "party":
						invoice[self.party_field] = "Other"
					elif target == "branch":
						invoice[self.address_field] = "Other Branch"
					elif target == "duplicate":
						self.billing[self.table].append(self.billing[self.table][0])
					elif target == "empty":
						self.billing[self.table][0][self.invoice_field] = None
					else:
						invoice.update(values)
					with self.assertRaises(frappe.ValidationError):
						self.make()
					self.mapper.assert_not_called()

	def test_settled_zero_or_negative_net_and_blocked_purchase_invoices_are_refused(self):
		for values in ((0, 0), (30, -30), (20, -30)):
			self.reset(outstanding=values)
			with self.assertRaises(frappe.ValidationError):
				self.make()
		self.reset(purchase=True)
		self.docs[(self.invoice_doctype, "INV-1")].invoice_is_blocked.return_value = True
		with self.assertRaises(frappe.ValidationError):
			self.make()

	def test_mixed_native_accounts_currencies_rates_or_dimensions_are_refused(self):
		for change in (
			{"party_account": "Other"},
			{"party_account_currency": "USD"},
			{"party_exchange_rate": 2},
			{"cost_center": "Other"},
			{"department": "Other"},
		):
			for purchase in (False, True):
				self.reset(purchase)
				self.docs[(self.invoice_doctype, "INV-1")].update(change)
				with (
					self.subTest(purchase=purchase, change=change),
					self.assertRaises(frappe.ValidationError),
				):
					self.make()

	def test_public_mappers_fix_the_billing_doctype_and_only_accept_a_name(self):
		with patch.object(billing_payment, "make_billing_payment", return_value="Draft") as make:
			self.assertEqual(sales_billing.make_payment_entry("SB-1"), "Draft")
			self.assertEqual(purchase_billing.make_payment_entry("PB-1"), "Draft")
		make.assert_has_calls([call("Sales Billing", "SB-1"), call("Purchase Billing", "PB-1")])
