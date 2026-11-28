// Stock Age Limits filters.
frappe.query_reports["Stock Age Limits"] = {
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
			label: __("Warehouse"),
			fieldtype: "Link",
			options: "Warehouse",
		},
		{
			fieldname: "item_group",
			label: __("Item Group"),
			fieldtype: "Link",
			options: "Item Group",
		},
		{ fieldname: "item_code", label: __("Item"), fieldtype: "Link", options: "Item" },
		{
			fieldname: "status",
			label: __("Status"),
			fieldtype: "Select",
			options: [
				"Problems only",
				"All",
				"Over storage limit",
				"Below minimum shelf life",
				"Expired",
				"OK",
			].join("\n"),
			default: "Problems only",
		},
		{
			fieldname: "as_on_date",
			label: __("As on Date"),
			fieldtype: "Date",
			default: frappe.datetime.get_today(),
		},
		{
			fieldname: "sellable_only",
			label: __("Sellable Stock Only"),
			description: __(
				"Only warehouses whose Warehouse Type allows a sale or an issue (no Quarantine, Lost, Borrowed, Write-Off ...)"
			),
			fieldtype: "Check",
			default: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "status" && data && data.status && data.status !== "OK") {
			value = `<span style="color: var(--red-600); font-weight: 600">${value}</span>`;
		}
		return value;
	},
};
