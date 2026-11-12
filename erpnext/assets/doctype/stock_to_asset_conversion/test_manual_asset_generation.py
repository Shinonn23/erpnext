"""Offline proof that approval is separate from explicit, authorized bulk generation."""

from unittest import TestCase
from unittest.mock import Mock, patch

import frappe

from erpnext.assets.doctype.stock_to_asset_conversion.stock_to_asset_conversion import StocktoAssetConversion


class TestManualAssetGeneration(TestCase):
	def test_generation_requires_a_post_request(self):
		self.assertEqual(
			frappe.allowed_http_methods_for_whitelisted_func[StocktoAssetConversion.create_assets], ["POST"]
		)

	def setUp(self):
		self.doc = Mock(doctype="Stock to Asset Conversion", docstatus=0, stock_items=[])
		self.doc.name = "CONV-1"
		self.doc.assets = [frappe._dict(asset="ASSET-1"), frappe._dict(asset="ASSET-2")]
		self.doc.is_new.return_value = False
		self.doc.get.return_value = "Stock to Asset Conversion Approved"
		self.db = Mock()
		self.db.exists.return_value = True
		self.transition = frappe._dict(next_state="Stock to Asset Conversion Submitted")
		self.workflow = frappe._dict(states=[frappe._dict(state=self.transition.next_state, doc_status=1)])
		for patcher in (
			patch.object(frappe, "db", self.db),
			patch("frappe.model.workflow.get_workflow", return_value=self.workflow),
			patch("frappe.model.workflow.get_transitions", return_value=[self.transition]),
			patch("frappe.model.workflow.has_approval_access", return_value=True),
			patch.object(frappe, "session", frappe._dict(user="accounting@example.com")),
			patch.object(frappe, "has_permission"),
			patch.object(frappe, "get_installed_apps", return_value=[]),
			patch.object(frappe, "throw", side_effect=frappe.ValidationError),
		):
			patcher.start()
			self.addCleanup(patcher.stop)

	def test_saving_a_conversion_does_not_generate_assets(self):
		self.doc.assets = [frappe._dict(asset=None)]
		StocktoAssetConversion.on_update(self.doc)
		self.doc.sync_draft_assets.assert_not_called()

	def test_bulk_generation_locks_and_reloads_the_saved_approved_table(self):
		self.assertEqual(StocktoAssetConversion.create_assets(self.doc), ["ASSET-1", "ASSET-2"])
		self.db.get_value.assert_called_once_with(self.doc.doctype, self.doc.name, "name", for_update=True)
		self.doc.reload.assert_called_once()
		self.doc.save.assert_called_once()
		self.doc.sync_draft_assets.assert_called_once()
		self.assertEqual(self.doc.check_permission.call_count, 2)
		frappe.has_permission.assert_called_once_with("Asset", "create", throw=True)

	def test_unapproved_or_posted_conversions_cannot_generate_assets(self):
		for state in ("Stock to Asset Conversion Draft", "Stock to Asset Conversion Pending Level 1 Approval"):
			with self.subTest(state=state):
				self.doc.get.return_value = state
				with patch("frappe.model.workflow.get_transitions", return_value=[]):
					with self.assertRaises(frappe.ValidationError):
						StocktoAssetConversion.create_assets(self.doc)
		self.doc.docstatus = 1
		with self.assertRaises(frappe.ValidationError):
			StocktoAssetConversion.create_assets(self.doc)
		self.doc.sync_draft_assets.assert_not_called()

	def test_asset_create_permission_is_required(self):
		frappe.has_permission.side_effect = frappe.PermissionError
		with self.assertRaises(frappe.PermissionError):
			StocktoAssetConversion.create_assets(self.doc)
		self.doc.sync_draft_assets.assert_not_called()

	def test_accounting_grant_is_required_when_app_is_installed(self):
		with (
			patch.object(frappe, "get_installed_apps", return_value=["connect_diagnostics_setup"]),
			patch("connect_diagnostics_setup.workflows.step_authority.filter_transitions", return_value=[]),
			patch.object(frappe, "session", frappe._dict(user="stock@example.com")),
		):
			with self.assertRaises(frappe.ValidationError):
				StocktoAssetConversion.create_assets(self.doc)
		self.doc.sync_draft_assets.assert_not_called()

	def test_delete_refuses_a_submitted_generated_receipt(self):
		with (
			patch.object(frappe, "get_all", return_value=[frappe._dict(name="MOVE-1", docstatus=1)]),
			patch.object(frappe, "delete_doc") as delete,
		):
			with self.assertRaises(frappe.ValidationError):
				StocktoAssetConversion.on_trash(self.doc)
		delete.assert_not_called()

	def test_delete_removes_own_cancelled_generated_records_with_normal_link_checks(self):
		self.doc.docstatus = 2
		self.doc.assets = [Mock(asset="ASSET-1")]
		with (
			patch.object(frappe, "get_all", side_effect=[
				[frappe._dict(name="MOVE-1", docstatus=2)],
				[frappe._dict(name="ASSET-1", docstatus=2)], ["SCHEDULE-1"],
			]) as get_all,
			patch.object(frappe, "delete_doc") as delete,
		):
			StocktoAssetConversion.on_trash(self.doc)
		self.assertEqual(get_all.call_args_list[0].kwargs["filters"], {
			"reference_doctype": self.doc.doctype, "reference_name": "CONV-1", "purpose": "Receipt",
		})
		self.assertEqual(get_all.call_args_list[1].kwargs["filters"], {"stock_to_asset_conversion": "CONV-1"})
		self.doc.assets[0].db_set.assert_called_once_with("asset", None)
		self.assertEqual([call.args for call in delete.call_args_list], [
			("Asset Movement", "MOVE-1"), ("Asset Depreciation Schedule", "SCHEDULE-1"), ("Asset", "ASSET-1"),
		])
		self.assertTrue(all(call.kwargs == {"ignore_permissions": True} for call in delete.call_args_list))

	def test_custom_workflow_uses_its_own_approved_state_and_submit_transition(self):
		self.doc.get.return_value = "Ready for Accounts"
		self.transition.next_state = "Conversion Posted"
		self.workflow.states = [frappe._dict(state="Conversion Posted", doc_status=1)]
		self.assertEqual(StocktoAssetConversion.create_assets(self.doc), ["ASSET-1", "ASSET-2"])

	def test_custom_workflow_cannot_generate_before_its_final_approval(self):
		self.transition.next_state = "Waiting for Executive"
		with self.assertRaises(frappe.ValidationError):
			StocktoAssetConversion.create_assets(self.doc)
		self.doc.sync_draft_assets.assert_not_called()

	def test_custom_workflow_keeps_its_self_approval_restriction(self):
		with patch("frappe.model.workflow.has_approval_access", return_value=False):
			with self.assertRaises(frappe.ValidationError):
				StocktoAssetConversion.create_assets(self.doc)
		self.doc.sync_draft_assets.assert_not_called()
