// Monthly Shipments (UX WP8, 19 Nov 2026): what each submitted Shipment carried, why and what it cost, by month.
frappe.query_reports["Monthly Shipments"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_start(frappe.datetime.add_months(frappe.datetime.get_today(), -2)),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.month_end(frappe.datetime.get_today()),
			reqd: 1,
		},
		{
			fieldname: "shipment_purpose",
			label: __("Shipment Type"),
			fieldtype: "Select",
			options: [
				"",
				"Sale Delivery",
				"Borrowing",
				"Demo",
				"Stock Transfer",
				"Purchase",
				"Return",
				"Miscellaneous",
			],
		},
		{ fieldname: "carrier", label: __("Carrier"), fieldtype: "Data" },
		{
			fieldname: "sent_by_employee",
			label: __("Sent by Employee"),
			fieldtype: "Link",
			options: "Employee",
		},
		{
			fieldname: "view",
			label: __("View"),
			fieldtype: "Select",
			options: ["Shipments", "Month and Type", "Carrier"],
			default: "Shipments",
		},
	],
};
