"""Advance VAT on a Payment Entry: the pair of tax rows that moves the VAT of a service advance (a Sales Order /
Purchase Order, or an amount not allocated to any document) from the undue to the due VAT account on the payment date,
the Advance For question on a draft, the withholding bases of an advance.
Offline: frappe.db, the Thai Tax Settings and the document's storage are stand-ins; the amounts go through ERPNext's own
Payment Entry tax code (calculate_taxes, add_tax_gl_entries) on the same in-memory Payment Entry as test_clear_undue_tax."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import frappe

import erpnext
from erpnext.accounts.doctype.payment_entry import payment_entry as erpnext_payment_entry
from erpnext.accounts.general_ledger import toggle_debit_credit_if_negative
from erpnext.regional.thailand.custom import advance_vat, custom_api, payment_entry, withholding
from erpnext.regional.thailand.tests.test_clear_undue_tax import (
	SERVICE_INVOICE,
	SETTINGS,
	_Payment,
	_ref,
	_report_type,
	_TaxRow,
)


def setUpModule():
	# flt(x, precision) reads the rounding method from System Settings
	global _rounding
	_rounding = patch.object(frappe, "get_system_settings", return_value=None, create=True)
	_rounding.start()


def tearDownModule():
	_rounding.stop()


ORDER = {"base_net_total": 10000, "base_grand_total": 10700}


def _order_ref(doctype, amount, total=10700):
	return SimpleNamespace(
		reference_doctype=doctype, reference_name="ORD-1", allocated_amount=amount, total_amount=total
	)


class _Advance(_Payment):
	"""The in-memory Payment Entry with the fields the advance rows read."""

	def __init__(self, payment_type, party_type, references, **fields):
		super().__init__(payment_type, party_type, references[0], fields.pop("has_purchase_tax_invoice", 0))
		self.__dict__.update(
			{
				"docstatus": 0,
				"references": references,
				"total_allocated_amount": sum(ref.allocated_amount for ref in references),
				"paid_amount": 0,
				"unallocated_amount": 0,
				"deductions": [],
				"tax_base_amount": 0,
				**fields,
			}
		)


def _throw(message, *args, **kwargs):
	raise frappe.ValidationError(message)


class _World:
	"""The orders and invoices in the database: an order's taxes on the undue account, invoice GL entries."""

	def __init__(self, order_tax=700, invoice_gl=None):
		self.order_tax = order_tax
		self.invoice_gl = invoice_gl or []

	def get_value(self, doctype, name, fields=None, as_dict=False):
		return frappe._dict(ORDER)

	def get_all(self, doctype, filters=None, fields=None, pluck=None):
		if doctype.endswith("Taxes and Charges"):
			assert filters["account_head"] in ("2135-01", "1154-01"), filters
			return [{"base_tax_amount_after_discount_amount": self.order_tax}] if self.order_tax else []
		raise AssertionError(doctype)

	def db_get_all(self, doctype, filters=None, fields=None):
		return self.invoice_gl

	def run(self, payment, rate=7):
		"""Save the payment (its rows) and build its tax GL as ERPNext submits it."""
		db = SimpleNamespace(
			get_all=Mock(side_effect=self.db_get_all), get_value=Mock(side_effect=self.get_value)
		)
		with (
			patch.object(custom_api.frappe, "flags", frappe._dict()),
			patch.object(custom_api.frappe, "db", db),
			patch.object(custom_api.frappe, "get_all", side_effect=self.get_all),
			patch.object(
				custom_api.frappe,
				"get_cached_value",
				side_effect=lambda doctype, name, field: rate
				if field == "tax_rate"
				else _report_type(doctype, name, field),
			),
			patch.object(custom_api.frappe, "throw", side_effect=_throw),
			patch.object(custom_api, "_", side_effect=lambda text: text),
			patch.object(advance_vat, "_", side_effect=lambda text: text),
			patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS),
			patch.object(frappe, "get_system_settings", return_value=None, create=True),
			patch.object(erpnext_payment_entry, "get_account_currency", return_value="THB"),
			patch.object(erpnext, "get_company_currency", return_value="THB"),
		):
			custom_api.clear_invoice_undue_tax(payment, None)
			gl = []
			payment.add_tax_gl_entries(gl)
		toggle_debit_credit_if_negative(gl)
		return {d.account: (d.debit, d.credit) for d in gl}


def _rows(payment):
	return [(d.description, d.account_head, d.tax_amount) for d in payment.taxes]


class TestAdvanceOrder(unittest.TestCase):
	def test_a_service_sales_order_moves_the_advance_vat_on_the_payment_date(self):
		payment = _Advance("Receive", "Customer", [_order_ref("Sales Order", 10700)])
		gl = _World().run(payment)
		self.assertEqual(
			_rows(payment),
			[("Advance VAT Due", "2135-01", -700), ("Advance VAT Due", "2135-00", 700)],
		)
		self.assertEqual(payment.tax_base_amount, 10000)
		# Dr 2135-01 / Cr 2135-00; the bank line is the payment's own, the pair posts none
		self.assertEqual(gl, {"2135-01": (700, 0), "2135-00": (0, 700)})

	def test_the_pair_follows_the_share_allocated(self):
		payment = _Advance("Receive", "Customer", [_order_ref("Sales Order", 5350)])
		_World().run(payment)
		self.assertEqual([d.tax_amount for d in payment.taxes], [-350, 350])
		self.assertEqual(payment.tax_base_amount, 5000)

	def test_a_goods_sales_order_has_no_rows(self):
		payment = _Advance("Receive", "Customer", [_order_ref("Sales Order", 10700)])
		self.assertEqual(_World(order_tax=0).run(payment), {})
		self.assertEqual(payment.taxes, [])

	def test_a_service_purchase_order_with_a_tax_invoice_moves_the_input_vat(self):
		payment = _Advance(
			"Pay", "Supplier", [_order_ref("Purchase Order", 10700)], has_purchase_tax_invoice=1
		)
		gl = _World().run(payment)
		self.assertEqual(
			_rows(payment), [("Advance VAT Due", "1154-01", -700), ("Advance VAT Due", "1154-00", 700)]
		)
		self.assertEqual(payment.tax_base_amount, 10000)
		self.assertEqual(gl, {"1154-01": (0, 700), "1154-00": (700, 0)})

	def test_a_purchase_order_without_a_tax_invoice_has_no_rows(self):
		payment = _Advance("Pay", "Supplier", [_order_ref("Purchase Order", 10700)])
		self.assertEqual(_World().run(payment), {})
		self.assertEqual(payment.taxes, [])

	def test_a_goods_purchase_order_with_a_tax_invoice_is_refused(self):
		payment = _Advance(
			"Pay", "Supplier", [_order_ref("Purchase Order", 10700)], has_purchase_tax_invoice=1
		)
		with self.assertRaises(frappe.ValidationError):
			_World(order_tax=0).run(payment)

	def test_saving_again_without_a_change_leaves_the_rows(self):
		payment = _Advance("Receive", "Customer", [_order_ref("Sales Order", 10700)])
		world = _World()
		world.run(payment)
		before = _rows(payment)
		payment.get_doc_before_save = lambda: _Advance(
			"Receive", "Customer", [_order_ref("Sales Order", 10700)]
		)
		world.run(payment)
		self.assertEqual(_rows(payment), before)

	def test_a_submitted_entry_and_a_locked_review_are_left_alone(self):
		locked = (
			{"docstatus": 1},
			{"workflow_state": "Payment Entry Pending Bank Verification"},
			{"workflow_state": "Payment Entry Pending Amount Approval"},
			{"workflow_state": "Payment Entry Verified"},
		)
		for fields in locked:
			payment = _Advance("Receive", "Customer", [_order_ref("Sales Order", 10700)])
			payment.__dict__.update(fields)
			_World().run(payment)
			self.assertEqual(payment.taxes, [], fields)

	def test_the_rows_are_made_in_the_preparers_states_of_the_workflow(self):
		for state in (
			"Payment Entry Draft",
			"Payment Entry Amount Rejected",
			"Payment Entry Refund Rejected",
		):
			payment = _Advance(
				"Receive", "Customer", [_order_ref("Sales Order", 10700)], workflow_state=state
			)
			_World().run(payment)
			self.assertEqual(len(payment.taxes), 2, state)


class TestAdvanceUnallocated(unittest.TestCase):
	def test_services_hold_the_vat_of_what_is_not_allocated_withholding_included(self):
		# paid 10,400 and 300 withheld by the customer: 10,700 with VAT, unallocated includes the deduction
		payment = _Advance(
			"Receive",
			"Customer",
			[SimpleNamespace(reference_doctype="", reference_name="", allocated_amount=0, total_amount=0)],
			thai_advance_for="Services",
			paid_amount=10400,
			unallocated_amount=10700,
			deductions=[frappe._dict(amount=300)],
		)
		_World().run(payment)
		self.assertEqual(
			_rows(payment), [("Advance VAT Due", "2135-01", -700), ("Advance VAT Due", "2135-00", 700)]
		)
		self.assertEqual(payment.tax_base_amount, 10000)

	def test_goods_have_no_rows(self):
		payment = _Advance(
			"Receive",
			"Customer",
			[SimpleNamespace(reference_doctype="", reference_name="", allocated_amount=0, total_amount=0)],
			thai_advance_for="Goods",
			unallocated_amount=10700,
		)
		_World().run(payment)
		self.assertEqual(payment.taxes, [])

	def test_changing_services_to_goods_takes_the_rows_away(self):
		payment = _Advance(
			"Receive",
			"Customer",
			[SimpleNamespace(reference_doctype="", reference_name="", allocated_amount=0, total_amount=0)],
			thai_advance_for="Goods",
			unallocated_amount=10700,
		)
		previous = _Advance("Receive", "Customer", [payment.references[0]], thai_advance_for="Services")
		previous.unallocated_amount = 10700
		payment.get_doc_before_save = lambda: previous
		payment.taxes = [
			_TaxRow(description="Advance VAT Due", account_head="2135-01", tax_amount=-700, idx=1),
			_TaxRow(description="Advance VAT Due", account_head="2135-00", tax_amount=700, idx=2),
		]
		_World().run(payment)
		self.assertEqual(payment.taxes, [])
		self.assertEqual(payment.tax_base_amount, 0)


class TestAdvanceWithInvoice(unittest.TestCase):
	def test_an_invoice_and_an_order_advance_make_one_base_and_keep_the_user_rows(self):
		invoice = _ref("Sales Invoice", 53500)
		payment = _Advance("Receive", "Customer", [invoice, _order_ref("Sales Order", 10700)])
		user_row = _TaxRow(
			description="Bank charge",
			account_head="5310-00",
			tax_amount=25,
			idx=1,
			charge_type="Actual",
			rate=0,
			included_in_paid_amount=0,
			currency="THB",
		)
		payment.taxes = [user_row]
		_World(invoice_gl=SERVICE_INVOICE).run(payment)
		self.assertEqual(
			_rows(payment),
			[
				("Bank charge", "5310-00", 25),
				("Clear Undue Tax", "2135-01", -3500),
				("Advance VAT Due", "2135-01", -700),
				("Clear Undue Tax", "2135-00", 4200),
			],
		)
		self.assertEqual([d.idx for d in payment.taxes], [1, 2, 3, 4])
		# one Sales Tax Invoice: the invoice's base and the advance's
		self.assertEqual(payment.tax_base_amount, 60000)


class TestClearVatJournalForSales(unittest.TestCase):
	def test_the_customer_is_on_the_output_vat_row(self):
		receipt = frappe._dict(
			name="PAY-1",
			company="CD",
			payment_type="Receive",
			party_type="Customer",
			party="CUST-1",
			company_tax_address="",
			references=[_ref("Sales Invoice", 53500)],
			check_permission=Mock(),
		)
		journal = MagicMock()
		rows = []
		journal.append.side_effect = lambda table, row: rows.append(row)
		with (
			patch.object(custom_api.frappe, "get_doc", return_value=receipt),
			patch.object(custom_api.frappe, "has_permission", return_value=True),
			patch.object(custom_api.frappe, "new_doc", return_value=journal),
			patch.object(
				custom_api.frappe, "db", SimpleNamespace(get_all=Mock(return_value=SERVICE_INVOICE))
			),
			patch.object(custom_api.frappe, "get_cached_value", side_effect=_report_type),
			patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS),
			patch.object(custom_api, "_", side_effect=lambda text: text),
		):
			self.assertIs(custom_api.make_clear_vat_journal_entry("Payment Entry", "PAY-1"), journal)
		# Dr 2135-01 (undue) / Cr 2135-00, base positive; the Journal Entry has no header customer
		self.assertEqual([r["account"] for r in rows], ["2135-01", "2135-00"])
		self.assertEqual(rows[0]["debit_in_account_currency"], 3500)
		self.assertEqual(rows[1]["credit_in_account_currency"], 3500)
		self.assertEqual(rows[1]["customer"], "CUST-1")
		self.assertNotIn("customer", rows[0])
		self.assertEqual(rows[1]["tax_base_amount"], 50000)


def _order_amounts(doctype, name):
	return {"Purchase Invoice": (10700, 10000), "Purchase Order": (21400, 20000)}[doctype]


def _source(doctype, name):
	grand_total, net_total = _order_amounts(doctype, name)
	return SimpleNamespace(
		grand_total=grand_total, base_net_total=net_total, check_permission=Mock(), company="CD", docstatus=1
	)


class TestValidateAdvanceFor(unittest.TestCase):
	def _doc(self, **fields):
		doc = frappe._dict(
			docstatus=0,
			company="CD",
			payment_type="Receive",
			party_type="Customer",
			unallocated_amount=100,
			meta=SimpleNamespace(has_field=lambda name: name == "thai_advance_for"),
		)
		doc.update(fields)
		return doc

	def test_an_amount_not_allocated_needs_advance_for(self):
		with patch.object(advance_vat.frappe, "throw", side_effect=_throw), patch.object(
			advance_vat, "_", side_effect=lambda text: text
		):
			with self.assertRaisesRegex(frappe.ValidationError, "Choose Advance For"):
				advance_vat.validate_advance_vat(self._doc())
			advance_vat.validate_advance_vat(self._doc(thai_advance_for="Goods"))
			advance_vat.validate_advance_vat(self._doc(unallocated_amount=0))
			# neither a receipt from a customer nor a payment to a supplier
			advance_vat.validate_advance_vat(self._doc(payment_type="Pay"))
			# a submitted entry is not asked again
			advance_vat.validate_advance_vat(self._doc(docstatus=1))

	def test_only_the_preparers_states_and_the_way_out_of_draft_are_asked(self):
		with patch.object(advance_vat.frappe, "throw", side_effect=_throw), patch.object(
			advance_vat, "_", side_effect=lambda text: text
		):
			asked = self._doc(workflow_state="Payment Entry Draft")
			with self.assertRaises(frappe.ValidationError):
				advance_vat.validate_advance_vat(asked)
			# the transition out of Draft: the saved document is still a draft
			leaving = self._doc(workflow_state="Payment Entry Pending Bank Verification")
			leaving.get_doc_before_save = lambda: self._doc(workflow_state="Payment Entry Draft")
			with self.assertRaises(frappe.ValidationError):
				advance_vat.validate_advance_vat(leaving)
			# already in review: not stuck
			for state in ("Payment Entry Pending Bank Verification", "Payment Entry Verified"):
				stuck = self._doc(workflow_state=state)
				stuck.get_doc_before_save = lambda state=state: self._doc(workflow_state=state)
				advance_vat.validate_advance_vat(stuck)
			pending = self._doc(workflow_state="Payment Entry Pending Amount Approval")
			advance_vat.validate_advance_vat(pending)

	def test_without_the_custom_field_nothing_is_asked(self):
		advance_vat.validate_advance_vat(self._doc(meta=SimpleNamespace(has_field=lambda name: False)))

	def test_services_need_the_vat_accounts(self):
		settings = SimpleNamespace(
			sales_tax_account="2135-00", sales_tax_account_undue="", purchase_tax_account="1154-00"
		)
		with (
			patch.object(advance_vat.frappe, "throw", side_effect=_throw),
			patch.object(advance_vat, "_", side_effect=lambda text: text),
			patch.object(custom_api, "get_thai_tax_settings", return_value=settings),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "Thai Tax Settings"):
				advance_vat.validate_advance_vat(self._doc(thai_advance_for="Services"))
		with patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS):
			advance_vat.validate_advance_vat(self._doc(thai_advance_for="Services"))


class TestAdvanceWithholding(unittest.TestCase):
	def test_the_payment_base_counts_a_purchase_order_by_its_net_total(self):
		doc = {
			"payment_type": "Pay",
			"references": [
				{"reference_doctype": "Purchase Order", "reference_name": "PO-1", "allocated_amount": 10700},
				{"reference_doctype": "Purchase Invoice", "reference_name": "PI-1", "allocated_amount": 5350},
			],
			"unallocated_amount": 0,
		}
		with patch.object(payment_entry.frappe, "get_doc", side_effect=_source):
			# 20,000 x 10,700 / 21,400 + 10,000 x 5,350 / 10,700
			self.assertEqual(payment_entry.get_payment_withholding_base(doc), 15000)

	def test_the_payment_base_of_an_unallocated_services_advance_leaves_the_vat_out(self):
		doc = {"company": "CD", "unallocated_amount": 10700, "thai_advance_for": "Services"}
		with (
			patch.object(payment_entry, "get_thai_tax_settings", return_value=SETTINGS),
			patch.object(payment_entry.frappe, "get_cached_value", return_value=7),
		):
			self.assertEqual(payment_entry.get_payment_withholding_base(doc), 10000)
		# goods and an entry without the answer: as it is
		for answer in ("Goods", None):
			doc["thai_advance_for"] = answer
			self.assertEqual(payment_entry.get_payment_withholding_base(doc), 10700)

	def test_the_categories_take_a_purchase_order_from_the_items_defaults(self):
		order = SimpleNamespace(
			check_permission=Mock(),
			company="CD",
			docstatus=1,
			items=[frappe._dict(item_code="SVC", base_net_amount=20000)],
		)
		doc = {
			"payment_type": "Pay",
			"company": "CD",
			"references": [
				frappe._dict(
					reference_doctype="Purchase Order",
					reference_name="PO-1",
					total_amount=21400,
					allocated_amount=10700,
				)
			],
		}
		item = SimpleNamespace(purchase_tax_withholding_category="Service", sales_tax_withholding_category="")
		with (
			patch.object(payment_entry.frappe, "get_doc", return_value=order),
			patch.object(payment_entry.frappe, "get_cached_doc", return_value=item),
		):
			self.assertEqual(
				payment_entry.get_payment_entry_tax_withholding_categories(doc),
				[{"tax_withholding_category": "Service", "taxable_amount": 10000}],
			)
			doc["payment_type"] = "Receive"
			doc["references"][0]["reference_doctype"] = "Sales Order"
			item.sales_tax_withholding_category = "Sales Service"
			self.assertEqual(
				payment_entry.get_payment_entry_tax_withholding_categories(doc),
				[{"tax_withholding_category": "Sales Service", "taxable_amount": 10000}],
			)

	def test_the_first_withhold_tax_of_a_services_advance_works_back_from_the_money_received(self):
		receipt = frappe._dict(
			doctype="Payment Entry",
			company="CD",
			references=[],
			base_paid_amount=10400,
			deductions=[],
			thai_advance_for="Services",
		)
		with (
			patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS),
			patch.object(withholding.frappe, "get_cached_value", return_value=7),
		):
			# 10,400 received = 10,000 + 700 VAT - 300 withheld (3%)
			self.assertEqual(withholding.get_receive_base(receipt, 3), 10000)
			# the popup of a later run, rows in place, is stable
			receipt.deductions = [frappe._dict(amount=300, thai_withholding_category="Service")]
			self.assertEqual(withholding.get_receive_base(receipt, 3), 10000)

	def test_a_receipt_that_settles_a_document_and_holds_an_advance_adds_the_advance_base(self):
		receipt = frappe._dict(
			doctype="Payment Entry",
			company="CD",
			references=[],
			base_paid_amount=0,
			deductions=[],
			thai_advance_for="Services",
			unallocated_amount=10700,
			source_exchange_rate=1,
		)
		source = frappe._dict(base_net_total=5000, grand_total=5350, currency="THB")
		sources = [
			frappe._dict(
				doctype="Sales Invoice", name="SI-1", allocated=5350, total=5350, account_currency="THB"
			)
		]
		with (
			patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS),
			patch.object(withholding.frappe, "get_cached_value", return_value=7),
			patch.object(withholding, "receive_sources", return_value=sources),
			patch.object(withholding, "_source", return_value=source),
		):
			self.assertEqual(withholding.get_receive_base(receipt), 15000)
			receipt.thai_advance_for = "Goods"
			self.assertEqual(withholding.get_receive_base(receipt), 5000)

	def test_the_receive_base_of_a_services_advance_leaves_the_vat_out(self):
		def receipt(**fields):
			return frappe._dict(
				doctype="Payment Entry",
				company="CD",
				references=[],
				base_paid_amount=10400,
				deductions=[
					frappe._dict(amount=300, thai_withholding_category="Service"),
					frappe._dict(amount=50),
				],
				**fields,
			)

		with (
			patch.object(custom_api, "get_thai_tax_settings", return_value=SETTINGS),
			patch.object(withholding.frappe, "get_cached_value", return_value=7),
		):
			# the money received and the tax withheld make the price with VAT: 10,700
			self.assertEqual(withholding.get_receive_base(receipt(thai_advance_for="Services")), 10000)
			# goods, or no answer: what was received, as before
			self.assertEqual(withholding.get_receive_base(receipt(thai_advance_for="Goods")), 10400)
			self.assertEqual(withholding.get_receive_base(receipt()), 10400)


if __name__ == "__main__":
	unittest.main()
