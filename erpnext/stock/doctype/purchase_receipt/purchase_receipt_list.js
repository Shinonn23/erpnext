frappe.listview_settings["Purchase Receipt"] = {
	add_fields: [
		"supplier",
		"supplier_name",
		"base_grand_total",
		"is_subcontracted",
		"transporter_name",
		"is_return",
		"status",
		"per_billed",
		"currency",
	],
	// The server keeps `status` (set_status); a user who may not read money fields sees grand_total 0, so the
	// indicator follows the stored status instead of recomputing it from amounts.
	get_indicator: function (doc) {
		const colors = {
			"To Bill": "orange",
			"Partly Billed": "yellow",
			Completed: "green",
			Return: "gray",
			"Return Issued": "grey",
			Closed: "green",
		};
		if (colors[doc.status]) {
			return [__(doc.status), colors[doc.status], "status,=," + doc.status];
		}
	},

	onload: function (listview) {
		listview.page.add_action_item(__("Purchase Invoice"), () => {
			erpnext.bulk_transaction_processing.create(listview, "Purchase Receipt", "Purchase Invoice");
		});
	},
};
