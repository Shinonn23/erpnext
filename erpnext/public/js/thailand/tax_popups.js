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

// Submitting a Payment Entry / Journal Entry that withheld from a supplier makes its Withholding Tax Cert (50 Tawi,
// listed in Connections); the manual button is added only for an entry that has none (made before 21-10-2026, or
// one whose certificate could not be made).
erpnext.regional.thailand.tax_popups.add_withholding_tax_cert_fallback = function (frm, supplier) {
	if (!frappe.model.can_create("Withholding Tax Cert")) return;
	frappe
		.xcall("erpnext.regional.thailand.custom.withholding_cert.get_voucher_certificates", {
			voucher_type: frm.doc.doctype,
			voucher_no: frm.doc.name,
		})
		.then((names) => {
			if ((names || []).length) return;
			frm.add_custom_button(
				__("Create Withholding Tax Cert"),
				() => erpnext.regional.thailand.tax_popups.create_withholding_tax_cert(frm, supplier),
				undefined,
				"Withholding Tax Cert"
			);
		});
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

// ------------------------------------------------------------------------------------------------ Withhold Tax
// The "Withhold Tax" buttons of a draft Payment Entry or Journal Entry (owner, 2026-10-21: as easy as before):
// - a receipt from a customer, or a journal crediting a customer: the type of income the customer withheld, its base
//   before VAT and its amount become a row on the type's Receive Account (withholding.py), replacing an earlier run;
// - a payment to a supplier, or a journal with a supplier line: ERPNext's own withholding (Consider for Tax
//   Withholding with the type of income popup; on a Payment Entry, or the types of the paid invoices' items).
erpnext.regional.thailand.tax_popups.WITHHOLD = "Withhold Tax";

// "Receive", "Pay" or null: what Withhold Tax does on this entry
erpnext.regional.thailand.tax_popups.withholding_direction = function (frm) {
	const doc = frm.doc;
	if (doc.doctype === "Payment Entry") {
		if (doc.payment_type === "Receive" && doc.party_type === "Customer") return "Receive";
		if (doc.payment_type === "Pay" && doc.party_type === "Supplier") return "Pay";
		return null;
	}
	const rows = doc.accounts || [];
	const credit = (row) => flt(row.credit_in_account_currency) - flt(row.debit_in_account_currency);
	const customer = (row) =>
		row.party_type === "Customer" && row.party && !row.thai_withholding_category && credit(row) > 0;
	if (rows.some(customer)) return "Receive";
	if (rows.some((row) => row.party_type === "Supplier" && row.party)) return "Pay";
	return null;
};

erpnext.regional.thailand.tax_popups.add_withhold_tax_buttons = function (frm) {
	if (frm.doc.docstatus !== 0 || frm.read_only || !(frm.perm && frm.perm[0] && frm.perm[0].write)) return;
	const popups = erpnext.regional.thailand.tax_popups;
	const group = __(popups.WITHHOLD);
	frm.add_custom_button(__("Based On Manual Selection"), () => popups.withhold_manual(frm), group);
	frm.add_custom_button(
		__("Based On Paying Document's Line Items"),
		() => popups.withhold_from_items(frm),
		group
	);
};

erpnext.regional.thailand.tax_popups.not_withholding = function () {
	frappe.msgprint(
		__(
			"Withhold Tax works on a receipt from a Customer or a payment to a Supplier (on a Journal Entry: a line that credits a Customer, or a Supplier line)."
		),
		__("Withhold Tax")
	);
};

erpnext.regional.thailand.tax_popups.withhold_manual = function (frm) {
	const popups = erpnext.regional.thailand.tax_popups;
	const direction = popups.withholding_direction(frm);
	if (direction === "Receive") return popups.ask_receive_withholding(frm);
	if (direction === "Pay") return popups.withhold_pay(frm);
	popups.not_withholding();
};

erpnext.regional.thailand.tax_popups.withhold_from_items = function (frm) {
	const popups = erpnext.regional.thailand.tax_popups;
	const direction = popups.withholding_direction(frm);
	if (direction === "Receive") return popups.apply_receive_withholding(frm, { based_on: "Line Items" });
	if (direction === "Pay" && frm.doc.doctype === "Payment Entry") return popups.load_pay_categories(frm);
	if (direction === "Pay") {
		frappe.msgprint(
			__(
				"A Journal Entry withholds one type of income for the whole entry: use Based On Manual Selection."
			),
			__("Withhold Tax")
		);
		return;
	}
	popups.not_withholding();
};

// Pay: ticking Consider for Tax Withholding opens the type of income popup; when it is ticked already, open it again
erpnext.regional.thailand.tax_popups.withhold_pay = function (frm) {
	if (
		frm.doc.doctype === "Journal Entry" &&
		!["Journal Entry", "Credit Note", "Debit Note"].includes(frm.doc.voucher_type)
	) {
		frappe.msgprint(
			__(
				"A Journal Entry withholds from a supplier when its Entry Type is Journal Entry, Credit Note or Debit Note."
			),
			__("Withhold Tax")
		);
		return;
	}
	if (!frm.doc.apply_tds) return frm.set_value("apply_tds", 1);
	return frm.trigger("thai_ask_withholding");
};

// Pay on a Payment Entry: the categories of the paid Purchase Invoices' items (Consider for Tax Withholding without
// its popup)
erpnext.regional.thailand.tax_popups.load_pay_categories = async function (frm) {
	if (!frm.doc.apply_tds) {
		frm.__thai_skip_withholding_popup = true;
		try {
			await frm.set_value("apply_tds", 1);
			await frappe.after_ajax();
		} finally {
			frm.__thai_skip_withholding_popup = false;
		}
	}
	await frm.trigger("load_tax_withholding_categories");
	if (!(frm.doc.tax_withholding_categories || []).length) {
		frappe.msgprint(
			__(
				"No item of the paid invoices has a Purchase Tax Withholding Category. Use Based On Manual Selection."
			),
			__("Withhold Tax")
		);
	}
};

erpnext.regional.thailand.tax_popups.withholding_amount = function (choice, base) {
	const amount = (flt(base) * flt(choice.rate)) / 100;
	return choice.round_off ? flt(amount, 0) : flt(amount, 2);
};

// Receive: which type of income the customer withheld, on what base before VAT, how much
erpnext.regional.thailand.tax_popups.ask_receive_withholding = async function (frm) {
	const popups = erpnext.regional.thailand.tax_popups;
	const { message: info } = await frappe.call({
		method: "erpnext.regional.thailand.custom.withholding.get_receive_withholding_dialog",
		args: { doc: frm.doc },
	});
	if (!info) return;
	if (!info.choices.length) {
		frappe.msgprint({
			title: __("Withhold Tax"),
			indicator: "orange",
			message: __(
				"No type of income is set up for receipts in {0}. In Tax Withholding Category set Applies To to Pay and Receive (or Receive) and fill the Receive Account (tax withheld by customers) for {0}.",
				[frappe.utils.escape_html(frm.doc.company)]
			),
		});
		return;
	}
	const choice = (name) => info.choices.find((c) => c.name === name);
	const about = (c) =>
		`<p class="text-muted small">${__("Type of income {0}", [
			frappe.utils.escape_html(c.type_of_income || "-"),
		])}${
			c.type_of_income_description ? ": " + frappe.utils.escape_html(c.type_of_income_description) : ""
		}<br>${__("Debits {0}", [frappe.utils.escape_html(c.account)])}</p>`;
	const first = choice(info.current) || info.choices[0];
	let dialog = null;
	const recompute = () => {
		if (!dialog) return;
		const c = choice(dialog.get_value("category"));
		if (!c) return;
		dialog.set_value("withholding_amount", popups.withholding_amount(c, dialog.get_value("base_amount")));
		dialog.fields_dict.about.$wrapper.html(about(c));
	};
	dialog = new frappe.ui.Dialog({
		title: __("Withholding Tax"),
		fields: [
			{
				fieldtype: "Select",
				fieldname: "category",
				label: __("Type of Income Withheld"),
				reqd: 1,
				options: info.choices.map((c) => ({ value: c.name, label: c.label })),
				default: first.name,
				description: __(
					"The tax the customer withheld from this receipt, at its rate on the posting date."
				),
				onchange: recompute,
			},
			{ fieldtype: "HTML", fieldname: "about" },
			{
				fieldtype: "Currency",
				fieldname: "base_amount",
				label: __("Withholding Tax Base"),
				reqd: 1,
				default: info.base,
				description: info.settles_documents
					? __(
							"Before VAT: the net total of the invoices this entry settles, for the part it settles."
					  )
					: __(
							"No invoice is settled: this is the amount received. Enter the amount before VAT the customer withheld on."
					  ),
				onchange: recompute,
			},
			{
				fieldtype: "Currency",
				fieldname: "withholding_amount",
				label: __("Withholding Tax Amount"),
				reqd: 1,
				default: popups.withholding_amount(first, info.base),
				description: __(
					"Change it to the amount on the customer's certificate (50 Tawi) if it differs."
				),
			},
		],
		primary_action_label: __("Apply"),
		primary_action(values) {
			dialog.hide();
			popups.apply_receive_withholding(frm, {
				tax_withholding_category: values.category,
				base_amount: values.base_amount,
				withholding_amount: values.withholding_amount,
			});
		},
	});
	dialog.show();
	dialog.fields_dict.about.$wrapper.html(about(first));
};

// Receive: the rows on the Receive Account (an earlier run's rows are replaced), then the entry balanced
erpnext.regional.thailand.tax_popups.apply_receive_withholding = function (frm, args) {
	return frm.call({ method: "apply_thai_withholding", doc: frm.doc, args, freeze: true }).then((r) => {
		const out = r && r.message;
		if (!out) return;
		frm.dirty();
		frm.refresh_fields();
		if (frm.doc.doctype === "Payment Entry" && frm.events.hide_unhide_fields) {
			frm.events.hide_unhide_fields(frm);
		}
		const withheld = out.rows
			.map((row) => `${frappe.utils.escape_html(row.title)} ${format_currency(row.amount)}`)
			.join(", ");
		let message = __("Withheld {0}", [withheld]);
		if (out.money_reduced && frm.doc.doctype === "Payment Entry") {
			message += ". " + __("Paid Amount is now {0}", [format_currency(out.paid_amount)]);
		} else if (out.money_reduced) {
			message += ". " + __("{0} is reduced by the tax", [frappe.utils.escape_html(out.bank_account)]);
		} else if (flt(out.difference)) {
			message += ". " + __("Difference left: {0}", [format_currency(out.difference)]);
		}
		frappe.show_alert({ message, indicator: flt(out.difference) ? "orange" : "green" }, 7);
	});
};
