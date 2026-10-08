frappe.query_reports["Custody Loan Stock Balance"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
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
		{ fieldname: "from_date", label: __("Expected Return From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Expected Return To"), fieldtype: "Date" },
	],
};
