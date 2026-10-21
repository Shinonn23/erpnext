frappe.ui.form.on("Journal Entry", {
	setup(frm) {
		frm.add_fetch("customer", "customer_name", "party_name");
		frm.add_fetch("supplier", "supplier_name", "party_name");
	},
	refresh(frm) {
		frm.set_query("company_tax_address", function () {
			return {
				filters: {
					is_your_company_address: true,
				},
			};
		});
		frm.set_query("company_tax_address", "tax_invoice_details", () => {
			return {
				filters: {
					is_your_company_address: true,
				},
			};
		});
	},
});

frappe.ui.form.on("Journal Entry Tax Invoice Detail", {
	customer(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.customer) {
			frappe.model.set_value(cdt, cdn, "supplier", "");
		}
	},
	supplier(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.supplier) {
			frappe.model.set_value(cdt, cdn, "customer", "");
		}
	},
});

frappe.ui.form.on("Journal Entry Account", {
	customer(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.customer) {
			frappe.model.set_value(cdt, cdn, "supplier", "");
		}
	},
	supplier(frm, cdt, cdn) {
		let row = locals[cdt][cdn];
		if (row.supplier) {
			frappe.model.set_value(cdt, cdn, "customer", "");
		}
	},
	account(frm, cdt, cdn) {
		frappe.model.set_value(cdt, cdn, "petty_cash_holder", "");
		frappe.model.set_value(cdt, cdn, "petty_cash_holder_name", "");
	},
});

frappe.ui.form.on("Journal Entry", {
	refresh(frm) {
		// Withhold Tax: a line crediting a customer (tax it withheld) or a supplier line (tax we withhold)
		erpnext.regional.thailand.tax_popups.add_withhold_tax_buttons(frm);
		// A Journal Entry that withheld (Consider for Tax Withholding) gets its certificate on submit, like a payment
		// (Connections); the button is the fallback for an entry without one
		if (frm.doc.docstatus == 1 && (frm.doc.tax_withholding_entries || []).length) {
			const supplier = (frm.doc.accounts || []).find(
				(row) => row.party_type == "Supplier" && row.party
			);
			erpnext.regional.thailand.tax_popups.add_withholding_tax_cert_fallback(
				frm,
				supplier && supplier.party
			);
		}
	},

	apply_tds(frm) {
		if (!frm.doc.apply_tds) return;
		// ERPNext would add the tax to the customer's debit (TCS): a customer's withholding is Withhold Tax's
		if (erpnext.regional.thailand.tax_popups.withholding_direction(frm) === "Receive") {
			frm.set_value("apply_tds", 0);
			frappe.msgprint(
				__(
					"This journal credits a customer: enter the tax the customer withheld with Withhold Tax → Based On Manual Selection. Consider for Tax Withholding is for the tax we withhold from a supplier."
				),
				__("Withhold Tax")
			);
			return;
		}
		frm.trigger("thai_ask_withholding");
	},

	thai_ask_withholding(frm) {
		// Ask what is withheld: the category sets the rate and the PND3/PND53 line
		erpnext.regional.thailand.tax_popups.ask_withholding_category(frm, {
			date: frm.doc.posting_date,
			current: frm.doc.tax_withholding_category,
			// closing the popup unticks the box, unless a type was chosen before (Withhold Tax opened it again)
			on_cancel: () => !frm.doc.tax_withholding_category && frm.set_value("apply_tds", 0),
			on_pick: (choice) => frm.set_value("tax_withholding_category", choice.name),
		});
	},

	validate(frm) {
		// Input VAT rows need the supplier's tax invoice: ask for it, tick Create Tax Invoice
		if (frm.doc.docstatus != 0) return;
		return erpnext.regional.thailand.tax_popups.get_tax_accounts(frm.doc.company).then((accounts) => {
			const rows = (frm.doc.accounts || []).filter(
				(row) =>
					accounts.purchase_tax_account &&
					row.account == accounts.purchase_tax_account &&
					flt(row.debit_in_account_currency) > 0 &&
					!(row.tax_invoice_number && row.tax_invoice_date)
			);
			if (!rows.length) return;
			const row = rows[0];
			const party = (frm.doc.accounts || []).find((r) => r.party_type == "Supplier" && r.party);
			frappe.validated = false;
			// a Journal Entry may have no supplier row: the popup asks who issued the tax invoice
			erpnext.regional.thailand.tax_popups.ask_tax_invoice(frm, {
				vat_amount: row.debit,
				ask_supplier: true,
				defaults: {
					supplier: row.supplier || (party && party.party),
					tax_invoice_number: row.tax_invoice_number,
					tax_invoice_date: row.tax_invoice_date || frm.doc.posting_date,
				},
				apply: async (values) => {
					await frappe.model.set_value(row.doctype, row.name, values);
					await frm.set_value("create_tax_invoice", 1);
				},
			});
		});
	},
});
