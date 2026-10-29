frappe.ui.form.on("Purchase Invoice", {
	before_load: function (frm) {
		erpnext.regional.thailand.clear_tax_invoice_links(frm);
		if (frm.doc.__islocal && frm.doc.amended_from) {
			frm.set_value("tax_invoice_number", "");
			frm.set_value("tax_invoice_date", "");
		}
	},

	onload: function (frm) {
		// Delay for 1 second and then trigger get_deposits
		if (frm.is_new() && !frm.doc.is_deposit_invoice) {
			setTimeout(function () {
				frm.events.get_deposits(frm, false);
			}, 1000);
		}
		// Disable add row on deposits
		frm.set_df_property("deposits", "cannot_add_rows", true); // Hide add row button
		frm.set_df_property("deposits", "cannot_delete_rows", true); // Hide delete button
		frm.set_df_property("deposits", "cannot_delete_all_rows", true); // Hide delete all button
	},

	is_deposit_invoice: function (frm) {
		erpnext.regional.thailand.deposit_utils.get_deposit_item(frm);
	},

	use_untied_deposit: function (frm) {
		frm.events.get_deposits(frm, true);
	},

	get_deposits: function (frm, is_button_clicked = true) {
		erpnext.regional.thailand.deposit_utils.get_deposits(frm, is_button_clicked);
	},

	validate: function (frm) {
		// Input VAT needs the supplier's tax invoice: ask for it instead of failing the save
		if (frm.doc.docstatus != 0 || frm.doc.split_tax_invoice || frm.doc.tax_invoice_number) return;
		return erpnext.regional.thailand.tax_popups.get_tax_accounts(frm.doc.company).then((accounts) => {
			const vat = (frm.doc.taxes || [])
				.filter((t) => accounts.purchase_tax_account && t.account_head == accounts.purchase_tax_account)
				.reduce((total, t) => total + flt(t.tax_amount_after_discount_amount || t.tax_amount), 0);
			if (!vat) return;
			frappe.validated = false;
			erpnext.regional.thailand.tax_popups.ask_tax_invoice(frm, {
				vat_amount: vat,
				party: frm.doc.supplier_name || frm.doc.supplier,
				defaults: {
					tax_invoice_number: frm.doc.bill_no,
					tax_invoice_date: frm.doc.bill_date || frm.doc.posting_date,
				},
				apply: (values) => frm.set_value(values),
			});
		});
	},

	apply_tds: function (frm) {
		// Ask what is withheld: the category (on every item row) sets the rate and the PND3/PND53 line
		if (!frm.doc.apply_tds) return;
		const current = (frm.doc.items || []).map((row) => row.tax_withholding_category).find(Boolean);
		erpnext.regional.thailand.tax_popups.ask_withholding_category(frm, {
			date: frm.doc.posting_date,
			current,
			on_cancel: () => frm.set_value("apply_tds", 0),
			on_pick: async (choice) => {
				for (const row of frm.doc.items || []) {
					await frappe.model.set_value(row.doctype, row.name, { apply_tds: 1, tax_withholding_category: choice.name });
				}
				frm.refresh_field("items");
			},
		});
	},
});
