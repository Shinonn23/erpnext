// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.listview_settings["Regulatory Registration Action"] = {
	add_fields: ["current_stage", "response_due_date"],
	get_indicator(doc) {
		const colors = {
			Preparing: "gray",
			"Internal Review": "blue",
			Submitted: "purple",
			"FDA Query": "orange",
			Approved: "green",
			Rejected: "red",
			Cancelled: "gray",
		};
		if (
			doc.current_stage === "FDA Query" &&
			doc.response_due_date &&
			frappe.datetime.get_diff(doc.response_due_date, frappe.datetime.get_today()) < 0
		) {
			return [__("Query Overdue"), "red", "current_stage,=,FDA Query"];
		}
		return [__(doc.current_stage), colors[doc.current_stage] || "gray", "current_stage,=," + doc.current_stage];
	},
};
