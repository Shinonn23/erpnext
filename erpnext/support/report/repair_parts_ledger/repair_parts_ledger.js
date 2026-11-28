// Repair Parts Ledger filters.
frappe.query_reports["Repair Parts Ledger"] = {
	filters: [
		{
			fieldname: "repair_type",
			label: __("Repair Type"),
			fieldtype: "Select",
			options: "Asset Repair",
			default: "Asset Repair",
			reqd: 1,
		},
		{ fieldname: "repair", label: __("Repair"), fieldtype: "Data", reqd: 1 },
	],
};
