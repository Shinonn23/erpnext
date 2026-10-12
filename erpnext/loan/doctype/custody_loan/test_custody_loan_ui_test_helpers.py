"""Unit checks for browser helper boundaries; no site or database required."""

import inspect
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import frappe

from erpnext.loan.doctype.custody_loan import custody_loan_ui_test_helpers as helpers
from erpnext.loan.doctype.custody_loan.custody_loan_ui_preflight import _json_keys, _normal


class TestCustodyBrowserHelperBoundaries(unittest.TestCase):
	def setUp(self):
		# Native precision reads this site setting; fix it without initializing a site.
		self.rounding = patch.object(frappe, "get_system_settings", return_value="Banker's Rounding")
		self.rounding.start()
		self.addCleanup(self.rounding.stop)

	def test_invalid_run_ids_never_resolve_site_files(self):
		with patch.object(helpers.frappe, "get_site_path") as path:
			for run_id in ("../site_config", "a" * 31, None, "g" * 32, "/tmp/manifest"):
				with self.subTest(run_id=run_id), self.assertRaises(frappe.PermissionError):
					helpers._path(run_id)
			path.assert_not_called()

	def test_helper_rejects_non_manager_even_in_test_mode(self):
		with (
			patch.object(helpers.frappe, "session", SimpleNamespace(user="reader@example.com")),
			patch.object(helpers.frappe, "get_roles", return_value=["Stock User"]),
		):
			with self.assertRaises(frappe.PermissionError):
				helpers._authorize()

	def test_manifest_cannot_be_used_by_another_manager(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "run.json"
			path.write_text(json.dumps({"owner": "first@example.com"}))
			with (
				patch.object(helpers, "_authorize"),
				patch.object(helpers, "_path", return_value=path),
				patch.object(helpers.frappe, "session", SimpleNamespace(user="other@example.com")),
			):
				with self.assertRaises(frappe.PermissionError):
					helpers._manifest("a" * 32)

	def test_foreign_company_rejected_before_following_links(self):
		doc = SimpleNamespace(company="Other Company", material_request="foreign")
		with patch.object(helpers.frappe, "get_doc", return_value=doc) as read:
			with self.assertRaises(frappe.PermissionError):
				helpers._owned({"company": "Fixture Company"}, "Custody Loan", "foreign")
			read.assert_called_once_with("Custody Loan", "foreign")

	def test_same_company_request_with_foreign_item_is_rejected(self):
		doc = SimpleNamespace(company="Fixture", items=[SimpleNamespace(item_code="Other Item")])
		with patch.object(helpers.frappe, "get_doc", return_value=doc):
			with self.assertRaises(frappe.PermissionError):
				helpers._owned({"company": "Fixture", "item": "Fixture Item"}, "Material Request", "foreign")

	def test_prerequisites_must_be_in_server_manifest(self):
		with patch.object(helpers.frappe, "get_doc", return_value=Mock()):
			with self.assertRaises(frappe.PermissionError):
				helpers._owned({"prerequisites": {"Asset": ["owned"]}}, "Asset", "other")

	def test_blocked_preflight_cannot_create_fixtures(self):
		with (
			patch.object(helpers, "_authorize"),
			patch.object(helpers, "inventory", return_value={"blockers": ["Unattributed script"]}),
			patch.object(helpers.frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(helpers.frappe, "get_doc") as insert,
		):
			with self.assertRaises(frappe.ValidationError):
				inspect.unwrap(helpers.prepare)()
			insert.assert_not_called()

	def test_native_test_gate_blocks_endpoint_before_authorization_or_preflight(self):
		with (
			patch.object(frappe, "in_test", False),
			patch.object(frappe, "_dev_server", False),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
			patch.object(helpers, "_authorize") as authorize,
			patch.object(helpers, "inventory") as preflight,
		):
			with self.assertRaises(frappe.ValidationError):
				helpers.prepare.__wrapped__()
			authorize.assert_not_called()
			preflight.assert_not_called()

	def test_manifest_is_persistent_private_and_retains_cleanup_errors(self):
		with tempfile.TemporaryDirectory() as directory:
			path = Path(directory) / "run.json"
			manifest = {"run_id": "a" * 32, "owner": "Administrator", "cleanup": {"errors": ["dependency"]}}
			with patch.object(helpers, "_path", return_value=path):
				helpers._store(manifest)
			self.assertEqual(json.loads(path.read_text()), manifest)
			self.assertEqual(path.stat().st_mode & 0o777, 0o600)
			self.assertFalse(path.with_suffix(".tmp").exists())

	def test_gl_assertion_preserves_account_scope_and_native_reversals(self):
		rows = [
			frappe._dict(
				voucher_type="Stock Entry", voucher_no="owned", account="Inventory", debit=0, credit=100
			),
			frappe._dict(voucher_type="Stock Entry", voucher_no="owned", account="Loss", debit=100, credit=0),
			frappe._dict(
				voucher_type="Stock Entry", voucher_no="other", account="Inventory", debit=900, credit=0
			),
		]
		self.assertEqual(helpers._gl_net(rows, "Stock Entry", "owned"), {"Inventory": -100, "Loss": 100})
		rows += [
			frappe._dict(
				voucher_type="Stock Entry", voucher_no="owned", account="Inventory", debit=100, credit=0
			),
			frappe._dict(voucher_type="Stock Entry", voucher_no="owned", account="Loss", debit=0, credit=100),
		]
		self.assertEqual(helpers._gl_net(rows, "Stock Entry", "owned"), {})

	def test_preflight_serializes_tuple_hook_targets(self):
		self.assertEqual(
			_json_keys({("Stock Entry", "Delivery Note"): {"validate": ["handler"]}}),
			{"('Stock Entry', 'Delivery Note')": {"validate": ["handler"]}},
		)
		self.assertEqual(_normal(None), _normal(0))
		self.assertEqual(_normal("0"), _normal(0))
		self.assertNotEqual(_normal("eval:doc.flag"), _normal(""))

	def test_failed_cleanup_rolls_back_business_and_still_revokes_permissions(self):
		manifest = {"run_id": "a" * 32}
		with (
			patch.object(helpers, "_manifest", return_value=manifest),
			patch.object(
				helpers, "_cleanup_business", side_effect=frappe.ValidationError("dependent transaction")
			),
			patch.object(helpers, "_remove_permissions") as revoke,
			patch.object(helpers, "_store") as store,
			patch.object(frappe, "db", Mock()) as db,
		):
			result = inspect.unwrap(helpers.cleanup)(manifest["run_id"])
			db.rollback.assert_called_once()
			revoke.assert_called_once_with(manifest, result)
			self.assertIn("dependent transaction", result["errors"][0]["error"])
			store.assert_called_once_with(manifest)
			self.assertEqual(manifest["cleanup"], result)

	def test_terminal_retention_also_revokes_temporary_permissions(self):
		manifest = {"run_id": "a" * 32}
		with (
			patch.object(helpers, "_manifest", return_value=manifest),
			patch.object(helpers, "_cleanup_business"),
			patch.object(helpers, "_remove_permissions") as revoke,
			patch.object(helpers, "_store"),
			patch.object(frappe, "db", Mock()),
		):
			result = inspect.unwrap(helpers.cleanup)(manifest["run_id"])
			revoke.assert_called_once_with(manifest, result)
			self.assertEqual(result["errors"], [])

	def test_permission_cleanup_failure_is_persisted(self):
		manifest = {"run_id": "a" * 32}
		with (
			patch.object(helpers, "_manifest", return_value=manifest),
			patch.object(helpers, "_cleanup_business"),
			patch.object(helpers, "_remove_permissions", side_effect=RuntimeError("cache unavailable")),
			patch.object(helpers, "_store") as store,
			patch.object(frappe, "db", Mock()),
		):
			result = inspect.unwrap(helpers.cleanup)(manifest["run_id"])
			self.assertIn("cache unavailable", result["errors"][0]["error"])
			store.assert_called_once_with(manifest)

	def test_register_cannot_report_requested_stock_as_physically_issued(self):
		loan = SimpleNamespace(
			name="owned",
			material_request="request",
			docstatus=1,
			status="Pending Pick",
			loan_type="Stock",
			items=[SimpleNamespace(issued_qty=0, returned_qty=0, outstanding_qty=0)],
		)
		register = frappe._dict(loan="owned", status="Pending Pick", issued_qty=2, returned_qty=0)
		result = {
			"documents": {
				"Material Request": [
					{
						"name": "request",
						"request_progress_status": "Pending",
						"items": [{"qty": 2, "fulfilled_qty": 0, "returned_qty": 0, "outstanding_qty": 0}],
					}
				]
			},
			"ledgers": {"Stock Ledger Entry": [], "GL Entry": []},
			"bundles": [],
			"reports": {
				"custody_loan_stock_balance": [],
				"custody_loan_register": [register],
				"custody_loan_movement_history": [],
			},
		}
		with self.assertRaisesRegex(AssertionError, "physically issued"):
			helpers._assert_effects({"scenario": "stock", "qty": 2}, loan, result)
		register.issued_qty = 0
		helpers._assert_effects({"scenario": "stock", "qty": 2}, loan, result)

	def test_request_closed_by_adjustment_must_reopen_after_reversal(self):
		request = {
			"name": "request",
			"request_progress_status": "Closed",
			"items": [{"qty": 2, "fulfilled_qty": 2, "returned_qty": 1, "outstanding_qty": 1}],
		}
		documents = {
			"Material Request": [request],
			"Delivery Note": [
				{"docstatus": 1, "is_return": 0, "requires_shipment": 0, "items": [{"qty": 2}]},
				{"docstatus": 1, "is_return": 1, "items": [{"qty": -1}]},
			],
			"Custody Loan Adjustment": [{"docstatus": 2, "items": [{"qty": 1}]}],
		}
		loan = SimpleNamespace(material_request="request", docstatus=1)
		with self.assertRaisesRegex(AssertionError, "MR progress status"):
			helpers._assert_request({"qty": 2}, loan, documents)
		request["request_progress_status"] = "Partially Returned"
		helpers._assert_request({"qty": 2}, loan, documents)
