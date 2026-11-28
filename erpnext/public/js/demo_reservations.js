(() => {
	if (frappe.__connect_diagnostics_demo_reservations) return;
	frappe.__connect_diagnostics_demo_reservations = true;
	const api = "erpnext.borrowing.demo_reservations.";
	// The Stock team picks the Assets of an approved request in one dialog: loan_release.js (Siwat, 5 Nov 2026).
	async function mayUse(frm) {
		const name = frm.doc.name;
		const { message } = await frappe.call({
			method: api + "can_view_demo_status",
			args: { material_request: frm.doc.material_request || name },
		});
		return frm.doc.name === name && message;
	}

	async function showStatus(frm) {
		const { message: status } = await frappe.call({
			method: api + "get_demo_status",
			args: { material_request: frm.doc.material_request || frm.doc.name },
		});
		if (!status.loans.length) {
			frappe.msgprint(__("There is no submitted Borrowing for this request yet."));
			return;
		}
		const dialog = new frappe.ui.Dialog({
			title: __("Demo Return and Extension"),
			fields: [
				{
					fieldname: "loan",
					label: __("Borrowing"),
					fieldtype: "Select",
					options: status.loans.map((loan) => loan.name),
					reqd: 1,
				},
				{
					fieldname: "current_date",
					label: __("Current Return Date"),
					fieldtype: "Date",
					read_only: 1,
				},
				{
					fieldname: "extension_state",
					label: __("Extension State"),
					fieldtype: "Data",
					read_only: 1,
				},
				{
					fieldname: "waiting",
					label: __("Waiting Requesters"),
					fieldtype: "Small Text",
					read_only: 1,
				},
				{
					fieldname: "expected_return_date",
					label: __("Requested Return Date"),
					fieldtype: "Date",
					read_only: !status.can_request,
				},
				{
					fieldname: "reason",
					label: __("Extension Reason"),
					fieldtype: "Small Text",
					read_only: !status.can_request,
				},
			],
			primary_action_label: status.can_request ? __("Request Extension") : __("Close"),
			async primary_action(values) {
				if (!status.can_request) return dialog.hide();
				await frappe.call({
					method: api + "request_extension",
					args: {
						borrowing: values.loan,
						expected_return_date: values.expected_return_date,
						reason: values.reason,
					},
					freeze: true,
				});
				dialog.hide();
				await frm.reload_doc();
			},
		});
		const selected = () => status.loans.find((loan) => loan.name === dialog.get_value("loan"));
		dialog.fields_dict.loan.df.onchange = () => {
			const loan = selected();
			if (!loan) return;
			dialog.set_values({
				current_date: loan.expected_return_date,
				extension_state: loan.demo_extension_state || "",
				expected_return_date: loan.demo_extension_date || "",
				reason: loan.demo_extension_reason || "",
				waiting: (loan.waiting || [])
					.map(
						(wait) => `${wait.asset}: ${wait.requesters.join(", ")} (${wait.request})`
					)
					.join("\n"),
			});
		};
		if (status.can_decide) {
			for (const [label, approve] of [
				["Approve Extension", 1],
				["Reject Extension", 0],
			]) {
				dialog.add_custom_action(__(label), async () => {
					if (selected()?.demo_extension_state !== "Pending") {
						frappe.msgprint(__("There is no extension request awaiting a decision."));
						return;
					}
					await frappe.call({
						method: api + "decide_extension",
						args: {
							borrowing: dialog.get_value("loan"),
							approve,
						},
						freeze: true,
					});
					dialog.hide();
					await frm.reload_doc();
				});
			}
		}
		dialog.show();
		await dialog.set_value(
			"loan",
			frm.doc.doctype === "Borrowing" ? frm.doc.name : status.loans[0].name
		);
		dialog.fields_dict.loan.df.onchange();
	}

	frappe.ui.form.on("Material Request", {
		setup(frm) {
			const field = frm.fields_dict.items.grid.get_field("asset");
			const original = field.get_query;
			frm.set_query("asset", "items", (doc, cdt, cdn) => {
				const row = locals[cdt][cdn];
				if (
					doc.material_request_type === "Customer Demo" &&
					row.demo_acquisition_method === "Wait Return"
				) {
					return {
						filters: {
							company: doc.company,
							item_code: row.item_code || doc.asset_item_filter,
							docstatus: 1,
						},
					};
				}
				return original ? original(doc, cdt, cdn) : { filters: { company: doc.company } };
			});
		},
		async refresh(frm) {
			if (
				frm.doc.material_request_type !== "Customer Demo" ||
				frm.is_new() ||
				frm.doc.docstatus === 2
			)
				return;
			if (!(await mayUse(frm))) return;
			frm.add_custom_button(__("Demo Borrowing Status"), () => showStatus(frm));
		},
	});
	frappe.ui.form.on("Material Request Item", {
		demo_acquisition_method(frm, cdt, cdn) {
			if (
				frm.doc.material_request_type === "Customer Demo" &&
				locals[cdt][cdn].demo_acquisition_method === "Convert Stock"
			) {
				return frappe.model.set_value(cdt, cdn, { asset: null, source_location: null });
			}
		},
	});
	frappe.ui.form.on("Borrowing", {
		async refresh(frm) {
			if (frm.doc.docstatus !== 1 || !frm.doc.material_request) return;
			frm.toggle_enable("expected_return_date", false);
			if (!(await mayUse(frm))) return;
			frm.add_custom_button(__("Demo Extension"), () => showStatus(frm));
		},
	});
})();
