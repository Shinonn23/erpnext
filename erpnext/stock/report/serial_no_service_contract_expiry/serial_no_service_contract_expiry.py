# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# For license information, please see license.txt

from erpnext.stock.report.storage_location_utils import execute_serial_report


def execute(filters=None):
	return execute_serial_report(
		filters,
		[
			"name",
			"item_code",
			"amc_expiry_date",
			"maintenance_status",
			"item_name",
			"description",
			"item_group",
			"brand",
		],
	)
