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
			frm.set_intro(__("The expiry date of this registration is unconfirmed, so no expiry alerts are sent."), "orange");
			return;
		}
		if (frm.doc.expiry_basis !== "Expiry Date Specified" || frm.doc.days_to_expiry == null) return;
		const days = frm.doc.days_to_expiry;
		if (days < 0) {
			frm.set_intro(__("Expired {0} days ago. Approve a renewal to reactivate it.", [Math.abs(days)]), "red");
		} else if (days <= 90) {
			frm.set_intro(__("Expires in {0} days.", [days]), "orange");
		}
	},

	add_status_buttons(frm) {
		const can_approve =
			frappe.user.has_role("Regulatory Approver") || frappe.user.has_role("System Manager");
		if (!can_approve) return;

		const status = frm.doc.official_status;
		const group = __("Status");
		const change = (target, label, needs_reason) => {
			frm.add_custom_button(
				label,
				() => {
					if (frm.is_dirty()) {
						frappe.msgprint(__("Save the registration before changing its status."));
						return;
					}
					const apply = (reason) =>
						frm
							.call({
								doc: frm.doc,
								method: "set_official_status",
								args: { status: target, reason: reason },
								freeze: true,
							})
							.then(() => frm.reload_doc());
					if (!needs_reason) {
						frappe.confirm(__("Set the status to {0}?", [__(target)]), () => apply());
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
			change("Active", __("Activate"), false);
			frm.call("get_activation_problems").then((r) => {
				if (r.message && r.message.length) {
					frm.set_intro(__("To activate: {0}", [r.message.join(" ")]), "orange");
				}
			});
		}
		if (status === "Suspended") change("Active", __("Reinstate"), false);
		if (["Active", "Suspended"].includes(status)) {
			if (status === "Active") change("Suspended", __("Suspend"), true);
			change("Revoked", __("Revoke"), true);
		}
		if (["Active", "Suspended", "Expired"].includes(status)) change("Cancelled", __("Cancel"), true);
	},
});
