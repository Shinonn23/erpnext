frappe.listview_settings["Customer"] = {
	onload(listview) {
		// Thailand: "New from Tax ID" opens the Revenue Department preview (public/js/thailand/party_tax_id.js)
		if (erpnext.thailand && erpnext.thailand.add_list_button) erpnext.thailand.add_list_button(listview);
	},
	add_fields: ["customer_name", "territory", "customer_group", "customer_type", "image"],
};
