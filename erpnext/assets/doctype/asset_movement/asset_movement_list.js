frappe.listview_settings["Asset Movement"] = {
	add_fields: ["is_borrowing_transaction"],
	onload(listview) {
		const has_loan_filter = listview.filter_area.get().some((filter) => filter[1] === "is_borrowing_transaction");
		if (!has_loan_filter) {
			listview.filter_area.add("Asset Movement", "is_borrowing_transaction", "=", 0);
		}
	},
};
