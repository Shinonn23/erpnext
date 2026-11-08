"""Return a loan set with other lots and consumed stock (Siwat, 5 Nov 2026). Offline."""

import unittest
from datetime import date
from unittest.mock import MagicMock, patch

import frappe

from erpnext.loan.doctype.custody_loan import custody_loan_set as module


def raise_validation(message, *args, **kwargs):
	raise frappe.ValidationError(message)


class Doc(frappe._dict):
	@property
	def items(self):
		return self["items"]

	@items.setter
	def items(self, value):
		self["items"] = value


def loan(name, loan_type, *rows, **values):
	data = dict(
		update_status=MagicMock(),
		name=name,
		loan_type=loan_type,
		company="CD",
		borrower_type="Customer",
		borrower="CUST-1",
		material_request="MR-1",
		items=[frappe._dict(row) for row in rows],
	)
	data.update(values)
	return Doc(**data)


ASSET = dict(name="A1", item_code="DEVICE", asset="AST-1", source_location="Store", item_name="Device")
REAGENT = dict(name="S1", item_code="REAGENT", item_name="Reagent")


class Patched(unittest.TestCase):
	def setUp(self):
		self.available = {"A1": 1, "S1": 10}
		for patcher in (
			patch.object(module, "_", side_effect=lambda text: text),
			patch.object(frappe, "throw", side_effect=raise_validation),
			patch.object(
				module,
				"loan_item_available_to_receive",
				side_effect=lambda loan_name, row: self.available[row.name],
			),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def set_up(self):
		assets = loan("CL-A", "Asset", ASSET)
		stock = loan("CL-S", "Stock", REAGENT)
		loans = {"CL-A": assets, "CL-S": stock}
		items = {row.name: (doc, row) for doc in loans.values() for row in doc.items}
		return loans, items


class TestPlan(Patched):
	def test_returns_and_consumed_are_grouped_per_loan_and_loan_item(self):
		loans, items = self.set_up()
		plan = module._check_plan(
			loans,
			items,
			[{"custody_loan_item": "A1"}],
			[
				{"custody_loan_item": "S1", "batch_no": "NEW-LOT", "qty": 4},
				{"custody_loan_item": "S1", "batch_no": "B-1", "qty": 2, "consumed": 4},
			],
		)
		self.assertEqual([row.asset for row in plan["assets"]["CL-A"]], ["AST-1"])
		self.assertEqual(
			[(line["batch_no"], line["returned_qty"]) for line in plan["returns"]["CL-S"]],
			[("NEW-LOT", 4), ("B-1", 2)],
		)
		self.assertEqual(plan["consumed"], {"CL-S": {"S1": 4}})

	def test_more_than_is_out_another_machine_and_a_foreign_row_are_refused(self):
		loans, items = self.set_up()
		with self.assertRaisesRegex(frappe.ValidationError, "more than the 10.0 still out"):
			module._check_plan(loans, items, [], [{"custody_loan_item": "S1", "qty": 8, "consumed": 3}])
		with self.assertRaisesRegex(frappe.ValidationError, "different machine is an exchange"):
			module._check_plan(loans, items, [{"custody_loan_item": "A1", "asset": "AST-2"}], [])
		with self.assertRaisesRegex(frappe.ValidationError, "not a Stock row"):
			module._check_plan(loans, items, [], [{"custody_loan_item": "A1", "qty": 1}])
		self.available["A1"] = 0
		with self.assertRaisesRegex(frappe.ValidationError, "already returned"):
			module._check_plan(loans, items, [{"custody_loan_item": "A1"}], [])


class TestReturnLoanSet(Patched):
	def run_set(self, request_type, borrower_type="Customer", invoice="SINV-1"):
		loans, _items = self.set_up()
		loans["CL-S"].borrower_type = borrower_type
		calls = []
		fresh = MagicMock()

		def get_doc(doctype, name=None):
			return loans.get(name) or fresh

		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(frappe, "get_doc", side_effect=get_doc),
			patch.object(frappe, "db", MagicMock(get_value=MagicMock(return_value=request_type))),
			patch.object(module, "loans_of_request", return_value=["CL-A", "CL-S"]),
			patch.object(
				module,
				"_return_assets",
				side_effect=lambda doc, rows, made: calls.append(("assets", doc.name)),
			),
			patch.object(
				module,
				"_return_stock",
				side_effect=lambda doc, rows, made: calls.append(("stock", doc.name, rows)),
			),
			patch.object(
				module,
				"_consume_stock",
				side_effect=lambda doc, rows, made: calls.append(("consumed", doc.name, rows)),
			),
		):
			module.return_loan_set(
				material_request="MR-1",
				assets=[{"custody_loan_item": "A1"}],
				stock=[
					{"custody_loan_item": "S1", "batch_no": "NEW-LOT", "qty": 4, "consumed": 6},
				],
			)
		return calls

	def test_one_dialog_makes_the_asset_return_the_stock_return_and_the_write_off(self):
		calls = self.run_set("Customer Demo")
		self.assertEqual(
			[call[:2] for call in calls], [("assets", "CL-A"), ("stock", "CL-S"), ("consumed", "CL-S")]
		)
		self.assertEqual(calls[1][2][0]["batch_no"], "NEW-LOT")
		self.assertEqual(calls[2][2], {"S1": 6})


class TestConsume(Patched):
	"""Write off consumption; accounts explicitly starts invoicing a Customer Borrow."""

	def consume(self, request_type, borrower_type="Customer"):
		stock = loan(
			"CL-S",
			"Stock",
			REAGENT,
			borrower_type=borrower_type,
			recipient_address=None,
			recipient_contact=None,
		)
		adjustment = MagicMock()
		adjustment.name = "ADJ-1"
		adjustment.items = []
		adjustment.append.side_effect = lambda key, value: adjustment.items.append(value)
		made = {"returns": [], "adjustments": [], "invoices": [], "waiting": [], "notes": []}

		def get_value(doctype, name, field=None, *a, **k):
			return request_type if doctype == "Material Request" else "5150-02 - Stock Adjustment"

		with (
			patch.object(frappe, "db", MagicMock(get_value=MagicMock(side_effect=get_value))),
			patch.object(frappe, "new_doc", return_value=adjustment) as new_doc,
			patch.object(frappe, "get_doc", return_value=stock),
			patch.object(module, "loan_item_available_to_adjust", return_value=10),
			patch.object(module, "is_tracked", return_value=True),
			patch.object(
				module,
				"grouped_open_rows",
				return_value=[
					{"batch_no": "B-1", "serials": [], "qty": 2},
					{"batch_no": "B-2", "serials": [], "qty": 5},
				],
			),
			patch.object(module, "_make_consumption_invoice") as invoice,
		):
			module._consume_stock(stock, {"S1": 4}, made)
		new_doc.assert_called_once_with("Custody Loan Adjustment")
		adjustment.insert.assert_called_once_with()
		adjustment.submit.assert_called_once_with()
		return adjustment, invoice, made

	def test_a_demo_is_written_off_as_a_demo_expense_from_the_earliest_lots(self):
		adjustment, invoice, made = self.consume("Customer Demo")
		invoice.assert_not_called()
		self.assertEqual(adjustment.reason, "Consumed during demo")
		self.assertEqual(adjustment.adjustment_account, "5150-02 - Stock Adjustment")
		self.assertEqual(
			[(row["batch_no"], row["qty"]) for row in adjustment.items], [("B-1", 2), ("B-2", 2)]
		)
		adjustment.submit.assert_called_once_with()
		self.assertEqual(len(made["adjustments"]), 1)
		self.assertEqual(made["invoices"], [])
		self.assertEqual(made["notes"], [])

	def test_a_borrow_by_a_customer_leaves_invoicing_to_an_explicit_accounts_action(self):
		adjustment, invoice, made = self.consume("Customer Borrow / Loan")
		invoice.assert_not_called()
		self.assertEqual(adjustment.reason, "Consumed by the borrower")
		self.assertEqual(made["adjustments"], ["ADJ-1"])
		self.assertEqual(made["invoices"], [])
		self.assertEqual(
			made["notes"], ["Accounts must create the consumption invoice from adjustment ADJ-1."]
		)

	def test_a_borrow_by_an_employee_is_not_invoiced(self):
		adjustment, invoice, made = self.consume("Customer Borrow / Loan", borrower_type="Employee")
		invoice.assert_not_called()
		self.assertEqual(adjustment.reason, "Consumed by the borrower")
		self.assertEqual(made["invoices"], [])
		self.assertEqual(made["notes"], [])


class TestRegisterLot(Patched):
	def register(self, existing_item=None, **kwargs):
		created = []

		class Document:
			def __init__(self, values):
				self.values = values

			def insert(self, **kw):
				created.append(self.values)
				return self

		flags = frappe._dict(has_batch_no=1, has_serial_no=1)
		with (
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(module, "item_flags", return_value=flags),
			patch.object(module, "getdate", side_effect=lambda value: date.fromisoformat(str(value))),
			patch.object(module, "nowdate", return_value="2026-11-05"),
			patch.object(
				frappe,
				"db",
				MagicMock(
					get_value=MagicMock(return_value=existing_item), exists=MagicMock(return_value=False)
				),
			),
			patch.object(frappe, "get_doc", side_effect=lambda values: Document(values)),
		):
			module.register_returned_lot("REAGENT", **kwargs)
		return created

	def test_an_unknown_batch_needs_its_expiry_date(self):
		with self.assertRaisesRegex(frappe.ValidationError, "expiry date of the new batch"):
			self.register(batch_no="NEW-LOT")
		created = self.register(batch_no="NEW-LOT", expiry_date="2099-01-01", manufacturing_date="2026-01-01")
		self.assertEqual(
			(
				created[0]["doctype"],
				created[0]["item"],
				created[0]["expiry_date"],
				created[0]["manufacturing_date"],
			),
			("Batch", "REAGENT", "2099-01-01", "2026-01-01"),
		)

	def test_an_expired_batch_and_a_batch_of_another_item_are_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "already expired"):
			self.register(batch_no="OLD", expiry_date="2020-01-01")
		with self.assertRaisesRegex(frappe.ValidationError, "belongs to another Item"):
			self.register(existing_item="OTHER", batch_no="B-1")

	def test_unknown_serial_numbers_are_created(self):
		created = self.register(serial_nos="SN-1\nSN-2, SN-3")
		self.assertEqual([values["serial_no"] for values in created], ["SN-1", "SN-2", "SN-3"])


class TestTheSet(Patched):
	def test_the_set_is_the_loans_of_the_same_request(self):
		with (
			patch.object(frappe, "db", MagicMock(get_value=MagicMock(return_value="MR-1"))),
			patch.object(frappe, "get_all", return_value=["CL-A", "CL-S"]) as get_all,
		):
			self.assertEqual(module.loans_of_set("CL-A"), ["CL-A", "CL-S"])
		self.assertEqual(get_all.call_args.kwargs["filters"], {"material_request": "MR-1", "docstatus": 1})


class TestSetStatus(unittest.TestCase):
	def test_one_status_for_the_loans_of_a_request(self):
		status = module.set_status
		self.assertEqual(status(["Pending Pick", "Pending Delivery"]), "Being delivered")
		self.assertEqual(status(["Pending Delivery", "Active"]), "Being delivered")
		self.assertEqual(status(["Active", "Active"]), "Out")
		self.assertEqual(status(["Active", "Returned"]), "Partly back")
		self.assertEqual(status(["Active", "Partially Returned"]), "Partly back")
		self.assertEqual(status(["Active", "Pending Receipt"]), "Return being checked")
		self.assertEqual(status(["Returned", "Closed"]), "Closed")
		self.assertIsNone(status([]))

	def test_the_summary_lists_each_readable_loan(self):
		readable = MagicMock()
		readable.name = "CL-A"
		readable.loan_type = "Asset"
		readable.status = "Active"
		readable.has_permission.return_value = True
		readable.items = [
			frappe._dict(
				item_code="DEVICE",
				item_name="Device",
				asset="AST-1",
				issued_qty=1,
				outstanding_qty=1,
				returned_qty=0,
				adjusted_qty=0,
				sold_qty=0,
			)
		]
		hidden = MagicMock()
		hidden.has_permission.return_value = False
		with (
			patch.object(module, "loans_of_request", return_value=["CL-A", "CL-X"]),
			patch.object(frappe, "get_doc", side_effect=[readable, hidden]),
		):
			summary = module.request_loan_summary("MR-1")
		self.assertEqual(summary["status"], "Out")
		self.assertEqual([loan["name"] for loan in summary["loans"]], ["CL-A"])
		self.assertEqual(summary["loans"][0]["items"][0]["outstanding"], 1)


class TestSetDelivery(unittest.TestCase):
	"""An Asset loan of a set moves its Assets with the stock loan's Delivery Note (Siwat, 5 Nov 2026)."""

	def waits(self, stock_loan, delivery_note_status=None, **values):
		from types import SimpleNamespace

		from erpnext.loan.doctype.custody_loan.custody_loan_core import CustodyLoan

		loan = SimpleNamespace(
			loan_type="Asset", asset_movement=None, material_request="MR-1", name="CL-A", **values
		)

		def get_value(doctype, *args, **kwargs):
			return stock_loan if doctype == "Custody Loan" else delivery_note_status

		with patch.object(frappe, "db", MagicMock(get_value=MagicMock(side_effect=get_value))):
			return CustodyLoan.waits_for_stock_delivery(loan)

	def test_an_asset_only_request_moves_at_once(self):
		self.assertFalse(self.waits(None))

	def test_a_set_waits_until_the_delivery_note_is_submitted(self):
		from types import SimpleNamespace

		self.assertTrue(self.waits(SimpleNamespace(name="CL-S", delivery_note="DN-1"), 0))
		self.assertTrue(self.waits(SimpleNamespace(name="CL-S", delivery_note=None)))
		self.assertFalse(self.waits(SimpleNamespace(name="CL-S", delivery_note="DN-1"), 1))

	def test_a_moved_asset_loan_never_waits(self):
		from types import SimpleNamespace

		from erpnext.loan.doctype.custody_loan.custody_loan_core import CustodyLoan

		loan = SimpleNamespace(loan_type="Asset", asset_movement="AM-1")
		self.assertFalse(CustodyLoan.waits_for_stock_delivery(loan))
		self.assertFalse(
			CustodyLoan.waits_for_stock_delivery(SimpleNamespace(loan_type="Stock", asset_movement=None))
		)

	def test_the_held_asset_loan_moves_with_the_delivery_note(self):
		from types import SimpleNamespace

		from erpnext.loan.doctype.custody_loan import custody_loan_core as core

		calls = []
		held = SimpleNamespace(
			loan_type="Asset",
			asset_movement=None,
			release_assets=lambda: calls.append("moved"),
			update_status=lambda: None,
		)
		done = SimpleNamespace(
			loan_type="Asset",
			asset_movement="AM-1",
			release_assets=lambda: calls.append("again"),
			update_status=lambda: None,
		)
		with patch.object(core, "delivery_loans", return_value=[held, done]):
			core.release_held_assets(SimpleNamespace(loan_type="Stock", material_request="MR-1"))
		self.assertEqual(calls, ["moved"])

	def test_an_asset_loan_releases_nothing(self):
		from types import SimpleNamespace

		from erpnext.loan.doctype.custody_loan import custody_loan_core as core

		with patch.object(core.frappe, "get_all") as get_all:
			core.release_held_assets(SimpleNamespace(loan_type="Asset", material_request="MR-1"))
		get_all.assert_not_called()
