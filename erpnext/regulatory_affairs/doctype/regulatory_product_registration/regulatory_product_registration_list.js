// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.listview_settings["Regulatory Product Registration"] = {
	add_fields: ["official_status", "product_category", "days_to_expiry", "expiry_basis", "renewal_status"],
	get_indicator(doc) {
		const colors = {
			Draft: "gray",
			Active: "green",
			Suspended: "orange",
			Revoked: "red",
			Cancelled: "red",
			Expired: "red",
			Superseded: "blue",
		};
		if (
			doc.official_status === "Active" &&
			doc.expiry_basis === "Expiry Date Specified" &&
			doc.days_to_expiry != null &&
			doc.days_to_expiry <= 90
		) {
			return [__("Expiring Soon"), "orange", "official_status,=,Active"];
		}
		return [__(doc.official_status), colors[doc.official_status] || "gray", "official_status,=," + doc.official_status];
	},
};
