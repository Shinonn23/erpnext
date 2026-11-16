frappe.query_reports["Borrowing Movement History"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "loan_type", label: __("Borrowing Type"), fieldtype: "Select", options: "\nStock\nAsset" },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
		{ fieldname: "requested_by", label: __("Requested By"), fieldtype: "Link", options: "Employee" },
	],
};
