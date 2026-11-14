frappe.listview_settings["Asset Movement"] = {
	add_fields: ["is_custody_loan_transaction"],
	onload(listview) {
		const equipment_view = new URLSearchParams(window.location.search).has("Asset Movement Item.service_equipment") || Boolean(frappe.route_options?.["Asset Movement Item.service_equipment"]);
		if (equipment_view) listview.page.set_title(__("Equipment Movements"));
		const has_loan_filter = listview.filter_area.get().some((filter) => filter[1] === "is_custody_loan_transaction");
		if (!equipment_view && !has_loan_filter) {
			listview.filter_area.add("Asset Movement", "is_custody_loan_transaction", "=", 0);
		}
	},
};
