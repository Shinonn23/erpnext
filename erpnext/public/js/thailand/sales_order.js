frappe.ui.form.on("Sales Order", {
	refresh(frm) {
		if (frm.doc.docstatus !== 1 || !frm.doc.has_deposit || frm.doc.deposit_invoice ||
			!["Accounts User", "Accounts Manager"].some((role) => frappe.user.has_role(role))) return;
		const terms = [...(frm.doc.payment_schedule || [])].sort((a, b) => a.idx - b.idx);
		const portion = terms[0]?.invoice_portion || 0;
		frm.add_custom_button(__("Deposit Invoice"), () => {
			frappe.model.open_mapped_doc({
				method: "erpnext.regional.thailand.custom.deposit_utils.create_deposit_invoice",
				frm,
				args: { doctype: "Sales Order", deposit_amount: frm.doc.total * portion / 100 },
			});
		}, __("Create"), "Sales Invoice");
	},

	deposit_amount: function (frm) {
		let { total, deposit_amount } = frm.doc;
		if (total && deposit_amount != null)
			frm.set_value("percent_deposit", (deposit_amount / total) * 100);
	},

	percent_deposit: function (frm) {
		let { total, percent_deposit } = frm.doc;
		if (total && percent_deposit != null)
			frm.set_value("deposit_amount", (total * percent_deposit) / 100);
	},
});
