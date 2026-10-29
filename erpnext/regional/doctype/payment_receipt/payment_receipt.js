// Copyright (c) 2024, FLO and contributors
// For license information, please see license.txt

frappe.ui.form.on("Payment Receipt", {
	setup(frm) {
		// Only submitted receipts from the customer, in the company, that no other receipt lists
		frm.set_query("payment_entry", "payment_references", function (doc) {
			const filters = {
				docstatus: 1,
				payment_type: "Receive",
				party_type: "Customer",
				party: doc.customer,
			};
			if (doc.company) {
				filters.company = doc.company;
			}
			return { filters };
		});
	},
});
