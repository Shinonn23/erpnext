frappe.query_reports["Stock to Asset Conversion Audit"] = {
	filters: [
		{ fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company" },
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "request_progress_status", label: __("Request Progress"), fieldtype: "Select", options: "\nPending\nOn Hold\nPartially Fulfilled\nFulfilled\nPartially Returned\nReturned\nClosed\nCancelled" },
		{ fieldname: "conversion", label: __("Conversion"), fieldtype: "Link", options: "Stock to Asset Conversion" },
		{ fieldname: "accounting_treatment", label: __("Accounting Treatment"), fieldtype: "Select", options: "\nCapitalize Stock Value\nUse Asset Item Valuation" },
		{ fieldname: "from_date", label: __("From Date"), fieldtype: "Date" },
		{ fieldname: "to_date", label: __("To Date"), fieldtype: "Date" },
	],
};
