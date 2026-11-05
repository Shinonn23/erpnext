frappe.query_reports["Custody Loan Register"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "loan_type", label: __("Loan Type"), fieldtype: "Select", options: "\nStock\nAsset" },
		{
			fieldname: "borrower_type",
			label: __("Borrower Type"),
			fieldtype: "Select",
			options: "\nEmployee\nCustomer\nSupplier\nContact",
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
			options: "\nDraft\nPending Shipment\nActive\nPartially Returned\nReturned\nClosed\nCancelled",
		},
		{ fieldname: "from_date", label: __("Expected Return From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Expected Return To"), fieldtype: "Date" },
		{ fieldname: "requested_by", label: __("Requested By"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "summary_by_requester", label: __("Summary by Requester"), fieldtype: "Check" },
	],
};
