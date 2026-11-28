// Lost Stock filters.
frappe.query_reports["Lost Stock"] = {
	filters: [
		{
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			default: frappe.defaults.get_user_default("Company"),
		},
		{
			fieldname: "warehouse",
			label: __("Warehouse Holding Lost Stock"),
			fieldtype: "Link",
			options: "Warehouse",
			get_query: () => ({
				query: "erpnext.stock.lost_inventory.lost_warehouse_query",
				filters: { company: frappe.query_report.get_filter_value("company") },
			}),
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{ fieldname: "min_days", label: __("Held at Least (days)"), fieldtype: "Int", default: 0 },
	],
};
