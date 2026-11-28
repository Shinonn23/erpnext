// The Service Engineer confirms where the lent equipment was placed (Siwat, 5 Nov 2026): the file is optional (10 Nov 2026), no typing of
// names. A Maintenance User cannot write a submitted Borrowing, so the button attaches the file and names
// them as the one who confirmed through workflows/loan_automation.confirm_placement.
frappe.ui.form.on("Borrowing", {
	async refresh(frm) {
		if (
			frm.doc.docstatus !== 1 ||
			frm.doc.borrower_type !== "Customer" ||
			!["Active", "Partially Returned"].includes(frm.doc.status) ||
			frm.doc.placement_confirmation ||
			frm.doc.placement_confirmed_by
		)
			return;
		const name = frm.doc.name;
		const { message: eligible } = await frappe.call({
			method: "erpnext.borrowing.placement.can_confirm_placement",
			args: { loan: name },
		});
		if (frm.doc.name !== name || !eligible) return;
		frm.add_custom_button(__("Confirm Placement"), () => {
			const dialog = new frappe.ui.Dialog({
				title: __("Placement Confirmation"),
				fields: [
					{
						fieldname: "file",
						fieldtype: "Attach",
						label: __("Photo or PDF of where the equipment was placed (optional)"),
					},
				],
				primary_action_label: __("Confirm"),
				primary_action(values) {
					frappe
						.call({
							method: "erpnext.borrowing.placement.confirm_placement",
							args: { loan: frm.doc.name, file_url: values.file },
							freeze: true,
						})
						.then(() => {
							dialog.hide();
							frm.reload_doc();
						});
				},
			});
			dialog.show();
		});
	},
});
