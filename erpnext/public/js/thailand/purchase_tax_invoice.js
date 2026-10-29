frappe.ui.form.on("Purchase Tax Invoice", {
	setup(frm) {
		// cancelling a tax invoice does not offer to cancel the voucher it was made from
		frm.ignore_doctypes_on_cancel_all = [
			"Sales Invoice",
			"Purchase Invoice",
			"Payment Entry",
			"Journal Entry",
			"Expense Claim",
		];
	},
	refresh(frm) {
		frm.set_query("company_tax_address", function () {
			return {
				filters: {
					is_your_company_address: true,
				},
			};
		});
	},
});
