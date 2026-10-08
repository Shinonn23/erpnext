frappe.listview_settings["Asset Movement"] = {
	add_fields: ["is_custody_loan_transaction"],
	onload(listview) {
		const has_loan_filter = listview.filter_area.get().some((filter) => filter[1] === "is_custody_loan_transaction");
		if (!has_loan_filter) {
			listview.filter_area.add("Asset Movement", "is_custody_loan_transaction", "=", 0);
		}
	},
};
