// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Item", {
	refresh(frm) {
		if (frm.is_new() || frm.doc.custom_thai_fda_applicability !== "Registration Required") return;

		frm.add_custom_button(
			__("Coverage Matrix"),
			() =>
				frappe.set_route("query-report", "Regulatory Item Coverage Matrix", {
					item: frm.doc.name,
				}),
			__("Regulatory")
		);

		if (!frm.doc.current_regulatory_registration) {
			frm.dashboard.set_headline_alert(
				__("No active FDA registration covers this Item today. Transactions may be blocked."),
				"red"
			);
		} else if (frm.doc.current_registration_expiry) {
			const days = frappe.datetime.get_diff(frm.doc.current_registration_expiry, frappe.datetime.get_today());
			if (days <= 90) {
				frm.dashboard.set_headline_alert(
					__("Registration {0} expires in {1} days.", [frm.doc.current_regulatory_registration, days]),
					"orange"
				);
			}
		}
	},
});
