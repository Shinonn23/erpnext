frappe.ui.form.on("Journal Entry", {
	before_load(frm) {
		erpnext.regional.thailand.clear_tax_invoice_links(frm);
	},
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
			const parties = [
				...(frm.doc.accounts || []).filter((row) => row.party_type && row.party),
				...(frm.doc.tax_withholding_entries || []).filter(
					(row) => flt(row.withholding_amount) && row.party_type && row.party
				),
			];
			const identities = new Set(parties.map((row) => `${row.party_type}:${row.party}`));
			const supplier = parties.find((row) => row.party_type === "Supplier" && row.party);
			if (identities.size === 1 && supplier) {
				erpnext.regional.thailand.tax_popups.add_withholding_tax_cert_fallback(frm, supplier.party);
			}
		}
	},

	async apply_tds(frm) {
		if (!frm.doc.apply_tds) return;
		// ERPNext would add the tax to the customer's debit (TCS): a customer's withholding is Withhold Tax's
		if ((await erpnext.regional.thailand.tax_popups.withholding_direction(frm)) === "Receive") {
			frm.set_value("apply_tds", 0);
			frappe.msgprint(
				__(
					"This journal credits a customer: enter the tax the customer withheld with Withhold Tax → Enter type and amount. Consider for Tax Withholding is for the tax we withhold from a supplier."
				),
				__("Withhold Tax")
			);
			return;
		}
		return frm.trigger("thai_ask_withholding");
	},

	async thai_ask_withholding(frm) {
		const popups = erpnext.regional.thailand.tax_popups;
		const ask_category = () => frm.trigger("thai_pick_withholding_category");
		if (await popups.ask_journal_tax_invoice(frm, ask_category)) return;
		return ask_category();
	},

	thai_pick_withholding_category(frm) {
		// Ask what is withheld: the category sets the rate and the PND3/PND53 line
		erpnext.regional.thailand.tax_popups.ask_withholding_category(frm, {
			date: frm.doc.posting_date,
			current: frm.doc.tax_withholding_category,
			// closing the popup unticks the box, unless a type was chosen before (Withhold Tax opened it again)
			on_cancel: () => !frm.doc.tax_withholding_category && frm.set_value("apply_tds", 0),
			on_pick: async (choice) => {
				await frm.set_value("tax_withholding_category", choice.name);
				erpnext.regional.thailand.ask_journal_withholding_base(frm, choice);
			},
		});
	},

	async validate(frm) {
		// Input VAT rows need the supplier's tax invoice, either way (a supplier's credit note reduces it): ask
		// for it, tick Create Tax Invoice. The month-end VAT Closing entry makes none.
		if (frm.doc.docstatus != 0 || frm.doc.voucher_type == "VAT Closing") return;
		const popups = erpnext.regional.thailand.tax_popups;
		if (
			await popups.ask_journal_tax_invoice(frm) ||
			await popups.ask_journal_withholding_mode(frm)
		) frappe.validated = false;
	},
});

// A journal that only withholds from a supplier's payable (a debit on the supplier, no expense line, e.g. the 3% on
// a clearing agent's fee before its advance is refunded): ask the base, the server computes the tax, the supplier's
// debit and the withholding row (Withholding Base, JournalTaxWithholding._is_withholding_only)
erpnext.regional.thailand.journal_withholding_only_row = function (doc) {
	const rows = (doc.accounts || []).filter((row) => !row.is_tax_withholding_account);
	if (doc.voucher_type !== "Journal Entry" || rows.length !== 1) return null;
	const row = rows[0];
	return row.party_type === "Supplier" && row.party && !flt(row.credit_in_account_currency) ? row : null;
};

erpnext.regional.thailand.ask_journal_withholding_base = function (frm, choice) {
	const row = erpnext.regional.thailand.journal_withholding_only_row(frm.doc);
	if (!row) return;
	frappe.prompt(
		[
			{
				fieldtype: "Currency",
				fieldname: "base",
				label: __("Withholding Base"),
				description: __("The amount the tax is withheld on, before VAT."),
				default: frm.doc.thai_withholding_base,
				reqd: 1,
			},
		],
		async (values) => {
			const tax = erpnext.regional.thailand.tax_popups.withholding_amount(choice, values.base);
			await frm.set_value("thai_withholding_base", values.base);
			await frappe.model.set_value(row.doctype, row.name, {
				debit_in_account_currency: flt(tax / (flt(row.exchange_rate) || 1)),
				credit_in_account_currency: 0,
			});
		},
		__("Withhold Tax"),
		__("Set")
	);
};
