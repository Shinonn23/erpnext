// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Service Equipment Maintenance Due"] = {
	filters: [
		{
			fieldname: "service_equipment",
			label: __("Service Equipment"),
			fieldtype: "Link",
			options: "Service Equipment",
		},
		{
			fieldname: "maintenance_type",
			label: __("Maintenance Type"),
			fieldtype: "Select",
			options: "\nPreventive Maintenance\nCalibration",
		},
		{ fieldname: "to_date", label: __("Due Up To"), fieldtype: "Date" },
	],
};
