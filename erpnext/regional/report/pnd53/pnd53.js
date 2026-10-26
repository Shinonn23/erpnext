// Copyright (c) 2023, Kitti U. and contributors
// For license information, please see license.txt
/* eslint-disable */

frappe.query_reports["PND53"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
			on_change: function () {
				frappe.query_report.set_filter_value("company_address", "");
				frappe.query_report.refresh();
			},
		},
		{
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Link",
			options: "Fiscal Year",
		},
		{
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			options: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "10", "11", "12"],
		},
		{
			fieldname: "company_address",
			label: __("Company Branch"),
			fieldtype: "Link",
			options: "Address",
			get_query: () => {
				return {
					query: "frappe.contacts.doctype.address.address.address_query",
					filters: {
						link_doctype: "Company",
						link_name: frappe.query_report.get_filter_value("company"),
						is_your_company_address: 1,
					},
				};
			},
		},
	],
};
