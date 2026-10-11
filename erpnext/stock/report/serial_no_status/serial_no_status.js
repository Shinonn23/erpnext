frappe.query_reports["Serial No Status"] = {
	filters: [
		{fieldname: "show_location_wise_stock", label: __("Show Storage Location Wise Stock"), fieldtype: "Check", default: 1},
		{fieldname: "company", label: __("Company"), fieldtype: "Link", options: "Company"},
		{fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item"},
		{fieldname: "warehouse", label: __("Warehouse"), fieldtype: "Link", options: "Warehouse"},
		{fieldname: "status", label: __("Status"), fieldtype: "Select", options: "\nActive\nInactive\nConsumed\nDelivered\nExpired"},
	],
};
