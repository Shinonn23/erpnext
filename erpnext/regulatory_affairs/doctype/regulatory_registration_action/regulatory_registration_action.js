// Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and contributors
// For license information, please see license.txt

const today_field = (fieldname, label, extra = {}) => ({
	fieldname,
	fieldtype: "Date",
	label: __(label),
	reqd: 1,
	...extra,
});

const STAGE_DIALOGS = {
	Preparing: {
		"Internal Review": { label: __("Send to Internal Review"), fields: () => [] },
		Submitted: {
			label: __("Submitted to FDA"),
			fields: (frm) => [
				today_field("submitted_date", "Submitted Date", { default: frappe.datetime.get_today() }),
				{
					fieldname: "fda_application_number",
					fieldtype: "Data",
					label: __("FDA Application Number"),
					default: frm.doc.fda_application_number,
				},
			],
		},
		Cancelled: { label: __("Cancel Action"), fields: () => [] },
	},
	"Internal Review": {},
	Submitted: {
		"FDA Query": {
			label: __("FDA Asked a Question"),
			fields: () => [
				today_field("fda_query_date", "FDA Query Date", { default: frappe.datetime.get_today() }),
				today_field("response_due_date", "Response Due Date"),
			],
		},
		Approved: {
			label: __("FDA Approved"),
			approver_only: true,
			fields: (frm) => approval_fields(frm),
		},
		Rejected: { label: __("FDA Rejected"), fields: () => [] },
		Cancelled: { label: __("Cancel Action"), fields: () => [] },
	},
	"FDA Query": {},
};
STAGE_DIALOGS["Internal Review"] = {
	Submitted: STAGE_DIALOGS.Preparing.Submitted,
	Cancelled: STAGE_DIALOGS.Preparing.Cancelled,
};
STAGE_DIALOGS["FDA Query"] = {
	Submitted: { label: __("Answer Sent to FDA"), fields: () => [] },
	Approved: STAGE_DIALOGS.Submitted.Approved,
	Rejected: STAGE_DIALOGS.Submitted.Rejected,
	Cancelled: STAGE_DIALOGS.Submitted.Cancelled,
};

function approval_fields(frm) {
	const fields = [today_field("approval_date", "Approval Date", { default: frappe.datetime.get_today() })];
	if (frm.doc.action_type === "Renewal") {
		fields.push(
			{
				fieldname: "new_expiry_date",
				fieldtype: "Date",
				label: __("New Expiry Date"),
				description: __(
					"Same registration number: the new expiry date. Leave blank for a new number."
				),
			},
			{
				fieldname: "resulting_registration",
				fieldtype: "Link",
				options: "Regulatory Product Registration",
				label: __("Resulting Registration"),
				description: __("New number: the Active registration that replaces this one."),
				get_query: () => ({
					filters: { official_status: "Active", name: ["!=", frm.doc.registration || ""] },
				}),
			}
		);
	}
	return fields;
}

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
		frm.trigger("add_stage_buttons");
		if (!frm.is_new()) {
			frm.add_custom_button(__("Registration"), () =>
				frappe.set_route("Form", "Regulatory Product Registration", frm.doc.registration)
			);
		}
	},

	// One button for each next stage; the dialog asks for the dates the stage needs and saves the form first.
	add_stage_buttons(frm) {
		if (frm.is_new() || frm.doc.applied_on) return;
		const next = STAGE_DIALOGS[frm.doc.current_stage || "Preparing"];
		if (!next) return;
		const can_approve =
			frappe.user.has_role("Regulatory Approver") || frappe.user.has_role("System Manager");
		for (const [stage, spec] of Object.entries(next)) {
			if (spec.approver_only && !can_approve) continue;
			if (spec.renewal_only && frm.doc.action_type !== "Renewal") continue;
			frm.add_custom_button(
				spec.label,
				() => {
					const move = (values) =>
						frm
							.call({
								doc: frm.doc,
								method: "advance_stage",
								args: { stage, values },
								freeze: true,
							})
							.then(() => frm.reload_doc());
					const ask = () => {
						const fields = spec.fields(frm);
						if (!fields.length) {
							frappe.confirm(__("Move this action to {0}?", [__(stage)]), () => move({}));
							return;
						}
						frappe.prompt(fields, (values) => move(values), spec.label, __("Confirm"));
					};
					if (frm.is_dirty()) frm.save().then(ask);
					else ask();
				},
				__("Move to")
			);
		}
	},

	current_stage(frm) {
		if (frm.doc.current_stage === "Approved" && !frm.doc.approval_date) {
			frm.set_value("approval_date", frappe.datetime.get_today());
		}
	},
});
