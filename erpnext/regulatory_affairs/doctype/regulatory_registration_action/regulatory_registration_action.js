// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Regulatory Registration Action", {
	setup(frm) {
		frm.set_query("resulting_registration", () => ({
			filters: { official_status: "Active", name: ["!=", frm.doc.registration || ""] },
		}));
	},

	refresh(frm) {
		if (frm.doc.applied_on) {
			frm.set_intro(__("This outcome has been applied to the registration."), "green");
		} else if (frm.doc.current_stage === "FDA Query" && frm.doc.response_due_date) {
			const days = frappe.datetime.get_diff(frm.doc.response_due_date, frappe.datetime.get_today());
			if (days < 0) {
				frm.set_intro(__("The FDA query response is {0} days overdue.", [Math.abs(days)]), "red");
			} else if (days <= 7) {
				frm.set_intro(__("The FDA query response is due in {0} days.", [days]), "orange");
			}
		}
		if (!frm.is_new()) {
			frm.add_custom_button(__("Registration"), () =>
				frappe.set_route("Form", "Regulatory Product Registration", frm.doc.registration)
			);
		}
	},

	current_stage(frm) {
		if (frm.doc.current_stage === "Approved" && !frm.doc.approval_date) {
			frm.set_value("approval_date", frappe.datetime.get_today());
		}
	},
});
