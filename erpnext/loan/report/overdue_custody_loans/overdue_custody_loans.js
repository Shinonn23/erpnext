frappe.query_reports["Overdue Custody Loans"] = {
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
	],
};
