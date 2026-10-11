// Copyright (c) 2016, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.query_reports["Item Variant Details"] = {
	filters: [
		{fieldname: "show_location_wise_stock", label: __("Show Storage Location Wise Stock"), fieldtype: "Check", default: 1},
		{
			reqd: 1,
			default: "",
			options: "Item",
			label: __("Item"),
			fieldname: "item",
			fieldtype: "Link",
			get_query: () => {
				return {
					filters: { has_variants: 1 },
				};
			},
		},
	],
};
