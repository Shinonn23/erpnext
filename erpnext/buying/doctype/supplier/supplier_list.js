frappe.listview_settings["Supplier"] = {
	onload(listview) {
		// Thailand: "New from Tax ID" opens the Revenue Department preview (public/js/thailand/party_tax_id.js)
		if (erpnext.thailand && erpnext.thailand.add_list_button) erpnext.thailand.add_list_button(listview);
	},
	add_fields: ["supplier_name", "supplier_group", "image", "on_hold", "disabled", "is_frozen"],
	get_indicator: function (doc) {
		if (cint(doc.disabled)) {
			return [__("Disabled"), "gray", "disabled,=,1"];
		} else if (cint(doc.on_hold)) {
			return [__("On Hold"), "red", "on_hold,=,1"];
		} else if (cint(doc.is_frozen)) {
			return [__("Frozen"), "orange", "is_frozen,=,1"];
		} else {
			return [__("Active"), "green", "disabled,=,0|on_hold,=,0|is_frozen,=,0"];
		}
	},
};
