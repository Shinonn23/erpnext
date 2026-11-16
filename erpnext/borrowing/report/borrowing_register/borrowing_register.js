frappe.query_reports["Borrowing Register"] = {
	filters: [
		{ fieldname: "purpose", label: __("Purpose"), fieldtype: "Select",
			options: "\nCustomer Demo\nBorrow\nCustomer Borrow" },
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
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: "\nDraft\nPending Pick\nPending Delivery\nActive\nPartially Returned\nReturned\nClosed\nCancelled",
		},
		{ fieldname: "from_date", label: __("Expected Return From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Expected Return To"), fieldtype: "Date" },
		{ fieldname: "requested_by", label: __("Requested By"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "summary_by_requester", label: __("Summary by Employee and Customer"), fieldtype: "Check" },
	],
};
