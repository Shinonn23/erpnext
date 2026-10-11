// Copyright (c) 2015, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Delivery Note Trends"] = $.extend({}, erpnext.sales_trends_filters);

frappe.query_reports["Delivery Note Trends"].filters = [
	{fieldname: "show_location_wise_stock", label: __("Show Storage Location Wise Stock"), fieldtype: "Check", default: 1},
	...frappe.query_reports["Delivery Note Trends"].filters,
];
