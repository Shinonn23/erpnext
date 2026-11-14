frappe.listview_settings["Asset Repair"] = {
	onload(listview) {
		const value = new URLSearchParams(window.location.search).get("service_equipment");
		if (value === '["is","set"]' || value === '["is", "set"]' || JSON.stringify(frappe.route_options?.service_equipment) === '["is","set"]') {
			listview.page.set_title(__("Equipment Repairs"));
		}
	},
	add_fields: ["repair_status"],
	get_indicator: function (doc) {
		if (doc.repair_status == "Pending") {
			return [__("Pending"), "orange"];
		} else if (doc.repair_status == "Completed") {
			return [__("Completed"), "green"];
		} else if (doc.repair_status == "Cancelled") {
			return [__("Cancelled"), "red"];
		}
	},
};
