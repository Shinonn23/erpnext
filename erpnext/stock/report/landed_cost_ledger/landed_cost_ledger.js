// Landed Cost Ledger filters.
frappe.query_reports["Landed Cost Ledger"] = {
	filters: [
		{
			fieldname: "landed_cost_voucher",
			label: __("Landed Cost Voucher"),
			fieldtype: "Link",
			options: "Landed Cost Voucher",
			reqd: 1,
		},
	],
	formatter(value, row, column, data, default_formatter) {
		if (["Currency", "Float"].includes(column.fieldtype) && data && data[column.fieldname] == null) return "";
		return default_formatter(value, row, column, data);
	},
};
