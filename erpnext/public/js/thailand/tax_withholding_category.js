// Thai settings of a type of withholding: Applies To (Pay / Receive) and, per company in the Accounts table, the Pay
// Account (tax we withhold from suppliers) and the Receive Account (tax our customers withhold from us). Saving
// checks that each direction Applies To names has its account on every company row (custom/withholding.py).
frappe.ui.form.on("Tax Withholding Category", {
	setup(frm) {
		frm.set_query("customer_withholding_account", "accounts", (doc, cdt, cdn) => {
			const row = locals[cdt][cdn];
			return {
				filters: {
					company: row.company,
					root_type: "Asset",
					is_group: 0,
				},
			};
		});
	},
});
