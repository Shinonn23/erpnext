from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

import frappe
from frappe.utils import getdate

from erpnext.assets.report.fixed_asset_register import fixed_asset_register as report


class TestFixedAssetRegisterChart(TestCase):
	def test_chart_can_be_copied_for_permission_hooks_with_labels_and_values_aligned(self):
		filters = frappe._dict(
			filter_based_on="Date Range",
			date_based_on="Purchase Date",
			from_date="2026-01-01",
			to_date="2026-03-31",
			company="Test Company",
		)
		periods = [frappe._dict(label=label) for label in ("Jan 2026", "Feb 2026", "Mar 2026")]
		data = [
			{"purchase_date": getdate("2026-03-02"), "asset_value": 300, "depreciated_amount": 30},
			{"purchase_date": getdate("2026-01-01"), "asset_value": 100, "depreciated_amount": 10},
			{"purchase_date": getdate("2026-01-15"), "asset_value": 50, "depreciated_amount": 5},
		]
		with (
			patch.object(frappe.local, "lang", "en", create=True),
			patch.object(frappe, "get_system_settings", return_value=None),
			patch.object(report, "get_period_list", return_value=periods),
			patch.object(report, "_", side_effect=lambda text: text),
		):
			chart = report.prepare_chart_data(data, filters)

		self.assertEqual(deepcopy(chart), chart)
		self.assertEqual(chart["data"]["labels"], ["Jan 2026", "Feb 2026", "Mar 2026"])
		self.assertEqual(chart["data"]["datasets"][0]["values"], [150, 0, 300])
		self.assertEqual(chart["data"]["datasets"][1]["values"], [15, 0, 30])
