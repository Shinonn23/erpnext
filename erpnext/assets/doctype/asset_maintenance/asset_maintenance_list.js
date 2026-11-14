frappe.listview_settings["Asset Maintenance"] = {
	onload(listview) {
		const value = new URLSearchParams(window.location.search).get("service_equipment");
		if (value === '["is","set"]' || value === '["is", "set"]' || JSON.stringify(frappe.route_options?.service_equipment) === '["is","set"]') {
			listview.page.set_title(__("Equipment Maintenance"));
		}
	},
};
