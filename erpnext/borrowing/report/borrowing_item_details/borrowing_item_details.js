frappe.query_reports["Borrowing Item Details"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "loan_type", label: __("Loan Type"), fieldtype: "Select", options: "\nStock\nAsset" },
		{ fieldname: "borrower_type", label: __("Borrower Type"), fieldtype: "Select", options: "\nEmployee\nCustomer" },
		{ fieldname: "borrower", label: __("Borrower"), fieldtype: "Dynamic Link", options: "borrower_type" },
		{ fieldname: "requested_by", label: __("Responsible Employee"), fieldtype: "Link", options: "Employee" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "batch_no", label: __("Batch"), fieldtype: "Link", options: "Batch" },
		{ fieldname: "serial_no", label: __("Serial No"), fieldtype: "Link", options: "Serial No" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options:
				"\nPending Pick\nPending Delivery\nActive\nPartially Returned\nReturned\nClosed",
		},
		{ fieldname: "from_date", label: __("Created From"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("Created To"), fieldtype: "Date" },
		{ fieldname: "only_outstanding", label: __("Only Not Returned"), fieldtype: "Check" },
	],
};
