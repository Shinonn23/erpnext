from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.borrowing import demo_consumption


class TestDemoConsumption(TestCase):
	def test_accepts_the_configured_active_company_expense_account(self):
		account = frappe._dict(company="CD", root_type="Expense", is_group=0, disabled=0)
		db = Mock()
		db.get_value.side_effect = ["Demo Expense - CD", account]
		with patch.object(frappe, "db", db):
			self.assertEqual(demo_consumption.expense_account("CD"), "Demo Expense - CD")
		db.get_value.assert_any_call("Company", "CD", "demo_consumable_expense_account")

	def test_rejects_missing_or_ineligible_accounts(self):
		eligible = {"company": "CD", "root_type": "Expense", "is_group": 0, "disabled": 0}
		for overrides in (
			None,
			{},
			{"company": "OTHER"},
			{"root_type": "Asset"},
			{"is_group": 1},
			{"disabled": 1},
		):
			with self.subTest(overrides=overrides):
				db = Mock()
				db.get_value.side_effect = (
					[None]
					if overrides is None
					else ["Demo Expense - CD", frappe._dict({**eligible, **overrides}) if overrides else None]
				)
				with (
					patch.object(frappe, "db", db),
					patch.object(demo_consumption, "_", lambda message: message),
					patch.object(frappe, "throw", side_effect=frappe.ValidationError),
					self.assertRaises(frappe.ValidationError),
				):
					demo_consumption.expense_account("CD")


class TestDemoConfirmation(TestCase):
	def setUp(self):
		self.session = frappe._dict(user="employee@example.com")
		self.flags = frappe._dict(applied_workflow_action="Existing Action")
		self.db = Mock()
		self.loan = Mock(name="loan", status="Active", company="CD")
		self.loan.name = "BOR-1"
		self.entry = Mock(doctype="Stock Entry", docstatus=1, meta=frappe._dict(queue_in_background=0))
		self.entry.name = "STE-1"
		self.adjustment = Mock()
		self.adjustment.name = "ADJ-1"
		self.adjustment.prepare_stock_entry.return_value = {"doctype": "Stock Entry"}
		self.token = "confirmation-token-001"
		self.items = [{"loan_item": "BIR-1", "batch_no": "BATCH-1", "qty": 1, "serial_no": "S-2\nS-1"}]
		self.account = "Demo Expense - CD"
		for patcher in (
			patch.object(frappe, "session", self.session, create=True),
			patch.object(frappe, "flags", self.flags),
			patch.object(frappe, "db", self.db),
			patch.object(frappe, "has_permission", return_value=True),
			patch.object(demo_consumption, "_", lambda text: text),
			patch.object(demo_consumption, "today", return_value="2026-01-02"),
			patch.object(frappe, "throw", side_effect=lambda message, *args: self.fail_validation(message)),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	@staticmethod
	def fail_validation(message):
		raise frappe.ValidationError(message)

	def _confirm(self, items=None):
		return demo_consumption.confirm("BOR-1", "CUS-1", "2026-01-01", items or self.items, self.token)

	def test_confirmation_posts_the_exact_selected_rows_once(self):
		from contextlib import nullcontext

		selected = [
			{
				"loan_item": "BIR-1",
				"item_code": "ITEM",
				"batch_no": "BATCH-1",
				"qty": 1,
				"serial_nos": ["S-1", "S-2", "S-3"],
				"serial_per_unit": 2,
			}
		]
		self.db.get_value.side_effect = [0, None, "STE-1"]
		with (
			patch.object(demo_consumption, "own_employee_loan", return_value=self.loan) as load,
			patch.object(demo_consumption, "_available_options", return_value=selected),
			patch.object(demo_consumption, "expense_account", return_value=self.account),
			patch.object(demo_consumption, "as_user", return_value=nullcontext()) as user_context,
			patch.object(frappe, "new_doc", return_value=self.adjustment),
			patch.object(frappe, "get_doc", return_value=self.entry),
			patch("erpnext.borrowing.doctype.borrowing.borrowing_core.set_loan_parties"),
			patch.object(demo_consumption, "_submit_stock_entry", return_value=self.entry),
		):
			self.assertEqual(self._confirm(), {"adjustment": "ADJ-1", "stock_entry": "STE-1"})
		load.assert_called_with("BOR-1", allow_closed=True, for_update=True)
		user_context.assert_called_once_with("Administrator")
		values = self.adjustment.update.call_args.args[0]
		self.assertEqual(values["demo_confirmed_by"], self.session.user)
		self.assertEqual(values["adjustment_account"], self.account)
		self.assertEqual(
			values["items"],
			[
				{
					"borrowing_item": "BIR-1",
					"item_code": "ITEM",
					"qty": 1,
					"batch_no": "BATCH-1",
					"serial_no": "S-1\nS-2",
				}
			],
		)
		self.adjustment.submit.assert_called_once()
		self.entry.insert.assert_called_once_with(ignore_permissions=True)

	def test_same_token_replays_existing_submitted_movement_after_loan_closes(self):
		import hashlib
		import json

		legacy_rows = [{"loan_item": "BIR-1", "batch_no": "BATCH-1", "qty": 1.0, "serials": ["S-1", "S-2"]}]
		payload = ["BOR-1", "CUS-1", "2026-01-01", legacy_rows]
		existing = frappe._dict(
			name="ADJ-1",
			borrowing="BOR-1",
			demo_confirmed_by=self.session.user,
			demo_customer="CUS-1",
			demo_request_hash=hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest(),
			stock_entry="STE-1",
			docstatus=1,
		)
		self.loan.status = "Closed"
		self.db.get_value.side_effect = [0, existing, "STE-1", 0, existing]
		with (
			patch.object(demo_consumption, "own_employee_loan", return_value=self.loan),
			patch.object(demo_consumption, "as_user") as user_context,
		):
			self.assertEqual(self._confirm(), {"adjustment": "ADJ-1", "stock_entry": "STE-1"})
			with self.assertRaisesRegex(frappe.ValidationError, "already belongs"):
				self._confirm([{**self.items[0], "qty": 2}])
		user_context.assert_not_called()
		self.assertTrue(self.db.get_value.call_args_list[1].kwargs["for_update"])

	def test_stock_entry_uses_the_workflow_submitted_status_not_its_name(self):
		from frappe.model import workflow

		policy = frappe._dict(
			states=[
				frappe._dict(state="Inventory moved", doc_status="1"),
				frappe._dict(state="Awaiting review", doc_status="0"),
			]
		)
		transition_rows = [
			frappe._dict(action="Post", next_state="Inventory moved", allow_self_approval=1),
			frappe._dict(action="Send", next_state="Awaiting review"),
		]

		def apply(doc, action):
			frappe.flags.applied_workflow_action = action
			return self.entry

		with (
			patch.object(workflow, "get_workflow_name", return_value="Stock policy"),
			patch.object(workflow, "get_workflow", return_value=policy),
			patch.object(workflow, "get_transitions", return_value=transition_rows),
			patch.object(workflow, "apply_workflow", side_effect=apply) as apply_workflow,
		):
			self.assertIs(demo_consumption._submit_stock_entry(self.entry), self.entry)
			apply_workflow.assert_called_once_with(self.entry.as_json(), "Post")
			self.assertEqual(self.flags.applied_workflow_action, "Existing Action")
			transition_rows.append(
				frappe._dict(action="Another post", next_state="Inventory moved", allow_self_approval=1)
			)
			with self.assertRaisesRegex(frappe.ValidationError, "cannot be submitted"):
				demo_consumption._submit_stock_entry(self.entry)
			apply_workflow.assert_called_once()

	def test_stock_entry_without_active_workflow_submits_normally(self):
		with patch("frappe.model.workflow.get_workflow_name", return_value=None):
			self.assertIs(demo_consumption._submit_stock_entry(self.entry), self.entry)
		self.entry.submit.assert_called_once()
