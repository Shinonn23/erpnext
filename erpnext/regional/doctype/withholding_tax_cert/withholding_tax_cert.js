// Copyright (c) 2023, Kitti U. and contributors
// For license information, please see license.txt

frappe.ui.form.on("Withholding Tax Cert", {
	refresh(frm) {
		frm.set_df_property("income_tax_form", "options", frm.doc.employee ? "PND3" : "\nPND3\nPND53");
		frm.set_query("employee", () => ({ filters: { company: frm.doc.company } }));
		frm.set_query("supplier_address", function () {
			return {
				query: "frappe.contacts.doctype.address.address.address_query",
				filters: {
					link_doctype: "Supplier",
					link_name: frm.doc.supplier,
				},
			};
		});
		frm.set_query("company_address", function (doc) {
			return {
				query: "frappe.contacts.doctype.address.address.address_query",
				filters: {
					link_doctype: "Company",
					link_name: doc.company,
				},
			};
		});
		frm.set_query("voucher_type", function () {
			return {
				filters: {
					name: ["in", ["Payment Entry", "Journal Entry"]],
				},
			};
		});
	},
	employee(frm) {
		if (frm.doc.employee) frm.set_value("income_tax_form", "PND3");
		frm.set_df_property("income_tax_form", "options", frm.doc.employee ? "PND3" : "\nPND3\nPND53");
	},
	is_agent(frm) {
		if (!frm.doc.is_agent) {
			frm.set_value("withholding_agent_for", "");
		}
	},
});

frappe.ui.form.on("Withholding Tax Items", {
	// Helper to calculate tax amount from given rate
	tax_rate: function (frm, cdt, cdn) {
		var row = locals[cdt][cdn];
		frappe.model.set_value(
			cdt,
			cdn,
			"tax_amount",
			flt((row.tax_base * row.tax_rate) / 100, precision("tax_amount", row))
		);
	},
});
