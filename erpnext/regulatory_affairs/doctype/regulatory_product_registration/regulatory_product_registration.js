// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

const RA_STATUS_COLORS = {
	Draft: "gray",
	Active: "green",
	Suspended: "orange",
	Revoked: "red",
	Cancelled: "red",
	Expired: "red",
	Superseded: "blue",
};

frappe.ui.form.on("Regulatory Product Registration", {
	refresh(frm) {
		frm.trigger("set_status_indicator");
		if (frm.is_new()) return;

		frm.trigger("add_status_buttons");
		frm.trigger("set_validity_intro");
		frm.trigger("show_activation_hint");

		frm.add_custom_button(
			__("Renewal / Variation Action"),
			() =>
				frappe.new_doc("Regulatory Registration Action", {
					registration: frm.doc.name,
					action_type: "Renewal",
				}),
			__("Create"),
			// the label is not the doctype name, so name the target for the Create-permission check
			"Regulatory Registration Action"
		);
		frm.add_custom_button(
			__("Regulatory Document"),
			() => frappe.new_doc("Regulatory Document", { registration: frm.doc.name }),
			__("Create"),
			"Regulatory Document"
		);
		frm.add_custom_button(
			__("Item Coverage"),
			() =>
				frappe.set_route("query-report", "Regulatory Item Coverage Matrix", {
					registration: frm.doc.name,
				}),
			__("View")
		);
	},

	set_status_indicator(frm) {
		const status = frm.doc.official_status || "Draft";
		frm.page.set_indicator(__(status), RA_STATUS_COLORS[status] || "gray");
	},

	set_validity_intro(frm) {
		if (!["Active", "Expired"].includes(frm.doc.official_status)) return;
		if (frm.doc.expiry_basis === "Unconfirmed") {
			frm.set_intro(
				__("The expiry date of this registration is unconfirmed, so no expiry alerts are sent."),
				"orange"
			);
			return;
		}
		if (frm.doc.expiry_basis !== "Expiry Date Specified" || frm.doc.days_to_expiry == null) return;
		const days = frm.doc.days_to_expiry;
		if (days < 0) {
			frm.set_intro(
				__("Expired {0} days ago. Approve a renewal to reactivate it.", [Math.abs(days)]),
				"red"
			);
		} else if (days <= 90) {
			frm.set_intro(__("Expires in {0} days.", [days]), "orange");
		}
	},

	// What is missing before the registration can be activated; every Regulatory user sees it, not only the Approver.
	show_activation_hint(frm) {
		if (frm.doc.official_status !== "Draft") return;
		frm.call("get_activation_problems").then((r) => {
			if (!r.message || !r.message.length) {
				frm.set_intro(
					__("Ready: a Regulatory Approver can press Status > Verify and Activate."),
					"green"
				);
				return;
			}
			frm.set_intro(__("To activate: {0}", [r.message.join(" ")]), "orange");
		});
	},

	add_status_buttons(frm) {
		const can_approve =
			frappe.user.has_role("Regulatory Approver") || frappe.user.has_role("System Manager");
		if (!can_approve) return;

		const status = frm.doc.official_status;
		const group = __("Status");
		const change = (target, label, needs_reason, verify) => {
			frm.add_custom_button(
				label,
				() => {
					// the form is saved first, so the user never has to
					const save_first = () => (frm.is_dirty() ? frm.save() : Promise.resolve());
					const apply = (reason) =>
						save_first().then(() =>
							frm
								.call({
									doc: frm.doc,
									method: verify ? "verify_and_activate" : "set_official_status",
									args: verify ? {} : { status: target, reason: reason },
									freeze: true,
								})
								.then(() => frm.reload_doc())
						);
					if (!needs_reason) {
						frappe.confirm(
							verify
								? __("Verify the attached Certificate and activate this registration?")
								: __("Set the status to {0}?", [__(target)]),
							() => apply()
						);
						return;
					}
					frappe.prompt(
						{ fieldname: "reason", fieldtype: "Small Text", label: __("Reason"), reqd: 1 },
						(values) => apply(values.reason),
						__("Set status to {0}", [__(target)]),
						__("Confirm")
					);
				},
				group
			);
		};

		if (status === "Draft") {
			change("Active", __("Verify and Activate"), false, true);
		}
		if (status === "Suspended") change("Active", __("Reinstate"), false);
		if (["Active", "Suspended"].includes(status)) {
			if (status === "Active") change("Suspended", __("Suspend"), true);
			change("Revoked", __("Revoke"), true);
		}
		if (["Active", "Suspended", "Expired"].includes(status)) change("Cancelled", __("Cancel"), true);
	},
});
