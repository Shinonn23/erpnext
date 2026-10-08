frappe.query_reports["Customer Demo Fulfillment"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{
			fieldname: "request_progress_status",
			label: __("Request Progress"),
			fieldtype: "Select",
			options: "\nPending\nOn Hold\nPartially Fulfilled\nFulfilled\nPartially Returned\nReturned\nClosed\nCancelled",
		},
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
	],
};
