from unittest import TestCase
from unittest.mock import Mock, call, patch

import frappe

from erpnext.assets.doctype.asset import asset as asset_module
from erpnext.assets.doctype.asset_depreciation_schedule import (
	asset_depreciation_schedule as schedule_module,
)


class TestAssetSchedulePermissions(TestCase):
	def test_automatic_schedules_are_saved_as_part_of_the_authorized_asset_update(self):
		for existing in (False, True):
			for permission_flag in (None, False, True):
				with self.subTest(existing=existing, ignore_permissions=permission_flag):
					asset = Mock(spec=asset_module.Asset)
					asset.name = "ASSET-1"
					asset.flags = frappe._dict()
					if permission_flag is not None:
						asset.flags.ignore_permissions = permission_flag
					asset.split_from = None
					asset.calculate_depreciation = True
					finance_book = frappe._dict(rate_of_depreciation=10, finance_book="BOOK-1")
					asset.get.return_value = [finance_book]
					asset.should_regenerate_depreciation_schedule.return_value = True
					asset.evaluate_and_recreate_depreciation_schedule.side_effect = lambda schedule, row: (
						asset_module.Asset.evaluate_and_recreate_depreciation_schedule(asset, schedule, row)
					)
					schedule = Mock()
					schedule.name = "SCHEDULE-1"
					with (
						patch.object(
							asset_module,
							"get_asset_depr_schedule_doc",
							return_value=schedule if existing else None,
						) as get_schedule,
						patch.object(frappe, "new_doc", return_value=schedule) as new_doc,
					):
						asset_module.Asset.create_asset_depreciation_schedule(asset)

					schedule.create_depreciation_schedule.assert_called_once_with(finance_book)
					get_schedule.assert_called_once_with(asset.name, "Draft", finance_book.finance_book)
					schedule.save.assert_called_once_with(ignore_permissions=True)
					asset.show_schedule_creation_message.assert_called_once_with([schedule.name])
					if existing:
						new_doc.assert_not_called()
					else:
						new_doc.assert_called_once_with("Asset Depreciation Schedule")
						self.assertEqual(schedule.asset, asset.name)

	def test_asset_submit_and_cancel_only_act_on_its_finance_book_schedules(self):
		for action, status in (("submit", "Draft"), ("cancel", "Active")):
			with self.subTest(action=action):
				asset = Mock(name="asset")
				asset.name = "ASSET-1"
				asset.get.return_value = [
					frappe._dict(finance_book="BOOK-1"),
					frappe._dict(finance_book=None),
				]
				schedule = Mock()
				schedule.flags = frappe._dict()
				getattr(schedule, action).side_effect = lambda: self.assertTrue(
					schedule.flags.ignore_permissions
				)
				with patch.object(
					schedule_module, "get_asset_depr_schedule_doc", side_effect=[schedule, None]
				) as get_schedule:
					if action == "submit":
						schedule_module.convert_draft_asset_depr_schedules_into_active(asset)
					else:
						schedule_module.cancel_asset_depr_schedules(asset)
				self.assertEqual(
					get_schedule.call_args_list,
					[call(asset.name, status, "BOOK-1"), call(asset.name, status, None)],
				)
				getattr(schedule, action).assert_called_once_with()

	def test_manual_schedule_operations_still_check_permissions(self):
		schedule = object.__new__(schedule_module.AssetDepreciationSchedule)
		schedule.doctype = "Asset Depreciation Schedule"
		schedule.flags = frappe._dict()
		with patch("frappe.permissions.has_permission", return_value=False) as has_permission:
			for action in ("create", "write", "submit", "cancel"):
				with self.subTest(action=action):
					self.assertFalse(schedule.has_permission(action))
					has_permission.assert_called_with(
						schedule.doctype, action, schedule, debug=False, user=None
					)
		self.assertFalse(schedule.flags.ignore_permissions)
