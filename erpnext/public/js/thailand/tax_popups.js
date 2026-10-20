// Thai tax popups (accounting team, 2026-10-19):
// - input VAT: a document that posts the input-VAT account asks for the supplier's tax invoice (number and date)
//   before it saves, so the Purchase Tax Invoice and the input-VAT report get them;
// - withholding: ticking "Consider for Tax Withholding" asks what is withheld (advertising 2%, services 3%, rent 5%,
//   hire of work 3%, transport 1% ...), which decides the rate and the PND3/PND53 line.
frappe.provide("erpnext.regional.thailand.tax_popups");

erpnext.regional.thailand.tax_popups.get_tax_accounts = async function (company) {
	if (!company) return {};
	const r = await frappe.call({
		method: "erpnext.regional.thailand.custom.custom_api.get_thai_tax_accounts",
		args: { company },
	});
	return r.message || {};
};

erpnext.regional.thailand.tax_popups.get_withholding_choices = async function (company, date) {
	const r = await frappe.call({
		method: "erpnext.regional.thailand.custom.custom_api.get_withholding_category_choices",
		args: { company, date },
	});
	return r.message || [];
};

// Ask for the supplier tax invoice, then save again. `values` are the defaults; `apply(values)` writes them.
erpnext.regional.thailand.tax_popups.ask_tax_invoice = function (frm, { vat_amount, party, ask_supplier, defaults, apply }) {
	const dialog = new frappe.ui.Dialog({
		title: __("Purchase Tax Invoice"),
		fields: [
			{
				fieldtype: "HTML",
				fieldname: "info",
				options: `<p>${__("This document records input VAT {0}. Enter the supplier's tax invoice for the input VAT report.", [
					format_currency(vat_amount, frm.doc.currency),
				])}</p>${party ? `<p>${__("Supplier")}: <b>${frappe.utils.escape_html(party)}</b></p>` : ""}`,
			},
			...(ask_supplier
				? [{ fieldtype: "Link", fieldname: "supplier", label: __("Supplier"), options: "Supplier", reqd: 1, default: defaults.supplier }]
				: []),
			{ fieldtype: "Data", fieldname: "tax_invoice_number", label: __("Tax Invoice Number"), reqd: 1, default: defaults.tax_invoice_number },
			{ fieldtype: "Date", fieldname: "tax_invoice_date", label: __("Tax Invoice Date"), reqd: 1, default: defaults.tax_invoice_date },
		],
		primary_action_label: __("Save"),
		primary_action(values) {
			dialog.hide();
			Promise.resolve(apply(values)).then(() => frm.save());
		},
	});
	dialog.show();
};

// Ask which withholding category applies; `on_pick(choice)` gets {name, rate, type_of_income, label}.
erpnext.regional.thailand.tax_popups.ask_withholding_category = async function (frm, { date, current, on_pick, on_cancel }) {
	const choices = await erpnext.regional.thailand.tax_popups.get_withholding_choices(frm.doc.company, date);
	if (!choices.length) {
		frappe.msgprint(__("No Tax Withholding Category has a rate and an account for {0}.", [frm.doc.company]));
		return on_cancel && on_cancel();
	}
	let picked = false;
	const dialog = new frappe.ui.Dialog({
		title: __("Withholding Tax"),
		fields: [
			{
				fieldtype: "Select",
				fieldname: "category",
				label: __("Type of Income Withheld"),
				reqd: 1,
				options: choices.map((c) => ({ value: c.name, label: c.label })),
				default: current || choices[0].name,
				description: __("The rate and the PND3/PND53 line follow the type of income."),
			},
		],
		primary_action_label: __("Apply"),
		primary_action(values) {
			picked = true;
			dialog.hide();
			on_pick(choices.find((c) => c.name === values.category));
		},
	});
	dialog.onhide = () => !picked && on_cancel && on_cancel();
	dialog.show();
};

// "Create Withholding Tax Cert" from a Payment Entry or a Journal Entry that withheld: date (the payment's posting
// date by default), PND3 for an individual supplier / PND53 for a company, and the company address.
erpnext.regional.thailand.tax_popups.create_withholding_tax_cert = async function (frm, supplier) {
	let income_tax_form = "";
	if (supplier) {
		const { message } = await frappe.db.get_value("Supplier", supplier, "supplier_type");
		income_tax_form = message && message.supplier_type == "Individual" ? "PND3" : "PND53";
	}
	frappe.prompt(
		[
			{ fieldtype: "Date", label: __("Date"), fieldname: "date", reqd: 1, default: frm.doc.posting_date },
			{
				fieldtype: "Select",
				label: __("Income Tax Form"),
				fieldname: "income_tax_form",
				options: "PND3\nPND53",
				default: income_tax_form,
			},
			{
				fieldtype: "Link",
				label: __("Company Address"),
				fieldname: "company_address",
				options: "Address",
				get_query: () => ({ filters: { is_your_company_address: 1 } }),
			},
		],
		(filters) =>
			frappe.call({
				method: "erpnext.regional.thailand.custom.payment_entry.make_withholding_tax_cert",
				args: { filters, doc: frm.doc },
				callback: (r) => {
					const doclist = frappe.model.sync(r.message);
					frappe.set_route("Form", doclist[0].doctype, doclist[0].name);
				},
			}),
		__("Withholding Tax Cert"),
		__("Create Withholding Tax Cert")
	);
};
