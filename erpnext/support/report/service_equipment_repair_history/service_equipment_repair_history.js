// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Service Equipment Repair History"] = {
	filters: [
		{
			fieldname: "service_equipment",
			label: __("Service Equipment"),
			fieldtype: "Link",
			options: "Service Equipment",
		},
		{
			fieldname: "repair_status",
			label: __("Repair Status"),
			fieldtype: "Select",
			options: "\nPending\nCompleted\nCancelled",
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
	],
};
