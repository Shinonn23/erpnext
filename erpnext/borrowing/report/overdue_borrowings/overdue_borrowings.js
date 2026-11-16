frappe.query_reports["Overdue Borrowings"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "loan_type", label: __("Borrowing Type"), fieldtype: "Select", options: "\nStock\nAsset" },
		{
			fieldname: "borrower_type",
			label: __("Borrower Type"),
			fieldtype: "Select",
			options: "\nEmployee\nCustomer",
		},
		{
			fieldname: "borrower",
			label: __("Borrower"),
			fieldtype: "Dynamic Link",
			options: "borrower_type",
		},
		{ fieldname: "requested_by", label: __("Requested By"), fieldtype: "Link", options: "Employee" },
	],
};
