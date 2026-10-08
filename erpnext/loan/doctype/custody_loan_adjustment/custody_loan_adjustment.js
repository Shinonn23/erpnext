frappe.ui.form.on("Custody Loan Adjustment", {
	setup(frm) {
		frm.set_query("custody_loan", () => ({
			filters: {
				docstatus: 1,
				loan_type: "Stock",
				status: ["in", ["Active", "Partially Returned"]],
			},
		}));
		frm.set_query("custody_loan_item", "items", () => ({
			filters: { parent: frm.doc.custody_loan },
		}));
		frm.set_query("batch_no", "items", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return { filters: { item_code: row.item_code } };
		});
	},
});

frappe.ui.form.on("Custody Loan Adjustment Item", {
	custody_loan_item(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.custody_loan_item) return;
		frappe.db
			.get_value("Custody Loan Item", row.custody_loan_item, ["item_code", "batch_no", "serial_no"])
			.then(({ message }) => {
				if (!message) return;
				frappe.model.set_value(cdt, cdn, {
					item_code: message.item_code,
					batch_no: message.batch_no,
					serial_no: message.serial_no,
				});
			});
	},
});
