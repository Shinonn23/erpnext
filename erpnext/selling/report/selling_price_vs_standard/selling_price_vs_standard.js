// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
// License: GNU General Public License v3. See license.txt

frappe.query_reports["Selling Price vs Standard"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
			reqd: 1,
		},
		{
			fieldname: "from_date",
			label: __("From Date"),
			fieldtype: "Date",
			default: frappe.datetime.add_months(frappe.datetime.get_today(), -1),
			reqd: 1,
		},
		{
			fieldname: "to_date",
			label: __("To Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
			reqd: 1,
		},
		{
			fieldname: "document_type",
			label: __("Document Type"),
			fieldtype: "Select",
			options: "Sales Order\nQuotation\nSales Invoice",
			default: "Sales Order",
			reqd: 1,
		},
		{ fieldname: "sales_user", label: __("Sales User"), fieldtype: "Link", options: "User" },
		{
			fieldname: "sales_person",
			label: __("Sales Person"),
			fieldtype: "Link",
			options: "Sales Person",
		},
		{
			fieldname: "territory",
			label: __("Territory"),
			fieldtype: "Link",
			options: "Territory",
		},
		{ fieldname: "customer", label: __("Customer"), fieldtype: "Link", options: "Customer" },
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "summary_by_sales", label: __("Summary by Sales"), fieldtype: "Check" },
	],
	formatter: function (value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (
			data &&
			[
				"selling_difference",
				"difference_percent",
				"average_percent_above_standard",
			].includes(column.fieldname)
		) {
			const number = flt(data[column.fieldname]);
			if (number > 0) {
				value = `<span style="color: var(--green-600)">${value}</span>`;
			} else if (number < 0) {
				value = `<span style="color: var(--red-600)">${value}</span>`;
			}
		}
		return value;
	},
};
