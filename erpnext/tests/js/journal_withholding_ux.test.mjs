import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function setup(doc) {
	const handlers = {};
	const changes = [];
	const dialogs = [];
	const popups = {};
	const frm = {
		doc: { doctype: "Journal Entry", docstatus: 0, posting_date: "2026-11-09", ...doc },
		async set_value(key, value) { this.doc[key] = value; changes.push([key, value]); },
		trigger(event) { return handlers[event](this); },
		save() { this.saved = true; },
		refresh_field() {},
	};
	const context = {
		__: (text) => text,
		flt: (value, decimals) => {
			const number = Number(value) || 0;
			return decimals === undefined ? number : Number(number.toFixed(decimals));
		},
		format_currency: (value) => String(value),
		precision: () => 6,
		erpnext: { regional: { thailand: { tax_popups: popups } } },
		frappe: {
			provide() {},
			throw(message) { throw new Error(message); },
			ui: { form: { on(doctype, events) {
				if (doctype === "Journal Entry") Object.assign(handlers, events);
			} }, Dialog: function (options) {
				dialogs.push(options); this.show = () => {}; this.hide = () => {};
			} },
			utils: { escape_html: (value) => value },
			model: { async set_value(doctype, name, values, value) {
				const row = [...(frm.doc.accounts || []), ...(frm.doc.tax_invoice_details || [])]
					.find((row) => row.name === name);
				Object.assign(row, typeof values === "string" ? { [values]: value } : values);
			}, clear_doc(doctype, name) {
				frm.doc.accounts = frm.doc.accounts.filter((row) => row.name !== name);
			} },
		},
	};
	vm.createContext(context);
	for (const file of ["tax_popups.js", "journal_entry.js"]) {
		vm.runInContext(fs.readFileSync(new URL(`../../public/js/thailand/${file}`, import.meta.url), "utf8"), context);
	}
	popups.get_tax_accounts = async () => ({ purchase_tax_account: "VAT" });
	popups.get_withholding_choices = async () => [
		{ name: "Services", rate: 3, label: "Services 3%", account: "WHT" },
	];
	return { frm, popups, context, dialogs, changes };
}

const vat = (extra = {}) => ({
	doctype: "Journal Entry Account", name: "vat", account: "VAT", debit: 700,
	debit_in_account_currency: 700, tax_invoice_number: "INV1", tax_invoice_date: "2026-11-09", ...extra,
});
const bank = () => ({ account: "Bank", account_type: "Bank", credit_in_account_currency: 10700 });

test("automatic WHT requests missing VAT Supplier even when invoice number and date exist", async () => {
	const { frm, dialogs, popups } = setup({ accounts: [vat(), bank()], apply_tds: 1 });
	assert.equal(await popups.withholding_direction(frm), "Pay");
	await frm.trigger("apply_tds");
	assert.equal(dialogs[0].title, "Purchase Tax Invoice");
	assert.ok(dialogs[0].fields.find((field) => field.fieldname === "supplier" && field.reqd));
	assert.ok(!dialogs[0].fields.some((field) => field.fieldname === "party_type"));
	assert.equal(frm.doc.create_tax_invoice, 1);
	assert.equal(frm.doc.accounts[1].party_type, undefined);
	assert.equal(frm.saved, undefined);
});

test("complete input VAT proceeds to category and preserves flags without needless invoice popup", async () => {
	const { frm, dialogs } = setup({ accounts: [vat({ supplier: "B031" }), bank()], apply_tds: 1,
		tax_withholding_category: "Services" });
	await frm.trigger("apply_tds");
	await new Promise((resolve) => setImmediate(resolve));
	assert.deepEqual(dialogs.map((dialog) => dialog.title), ["Withholding Tax"]);
	dialogs[0].primary_action({ category: "Services" });
	assert.equal(frm.doc.tax_withholding_category, "Services");
	assert.equal(frm.doc.apply_tds, 1);
	assert.equal(frm.doc.create_tax_invoice, 1);
	assert.equal(frm.doc.accounts[1].party, undefined);
});

test("invoice popup synchronizes linked detail and VAT row before continuing to category", async () => {
	const row = vat({ reference_detail_no: "detail" });
	const detail = { doctype: "Journal Entry Tax Invoice Detail", name: "detail" };
	const { frm, dialogs } = setup({ accounts: [row, bank()], tax_invoice_details: [detail], apply_tds: 1 });
	await frm.trigger("apply_tds");
	dialogs[0].primary_action({ supplier: "B031", tax_invoice_number: "INV2", tax_invoice_date: "2026-11-09" });
	await new Promise((resolve) => setImmediate(resolve));
	assert.equal(row.supplier, "B031");
	assert.equal(detail.supplier, "B031");
	assert.equal(row.tax_invoice_number, "INV2");
	assert.equal(dialogs[1].title, "Withholding Tax");
	assert.equal(frm.saved, undefined);
});

test("save prompts for missing Supplier and normal invoice completion saves again", async () => {
	const { frm, dialogs, context } = setup({ accounts: [vat(), bank()] });
	await frm.trigger("validate");
	assert.equal(context.frappe.validated, false);
	dialogs[0].primary_action({ supplier: "B031", tax_invoice_number: "INV1", tax_invoice_date: "2026-11-09" });
	await new Promise((resolve) => setImmediate(resolve));
	assert.equal(frm.saved, true);
});

test("customer receipt and existing supplier/payment direction stay unchanged", async () => {
	const { frm, popups } = setup({ accounts: [{ party_type: "Customer", party: "C1", credit_in_account_currency: 500 }] });
	assert.equal(await popups.withholding_direction(frm), "Receive");
	frm.doc.accounts = [{ party_type: "Supplier", party: "S1" }];
	assert.equal(await popups.withholding_direction(frm), "Pay");
	frm.doc = { doctype: "Payment Entry", party_type: "Customer", payment_type: "Receive" };
	assert.equal(await popups.withholding_direction(frm), "Receive");
	frm.doc = { doctype: "Payment Entry", party_type: "Supplier", payment_type: "Pay" };
	assert.equal(await popups.withholding_direction(frm), "Pay");
});


test("certificate fallback uses recorded nonzero Supplier and rejects conflicting parties", () => {
	const { frm, popups } = setup({ docstatus: 1, accounts: [bank()], tax_withholding_entries: [
		{ party_type: "Supplier", party: "B031", withholding_amount: 300 },
		{ party_type: "Supplier", party: "OTHER", withholding_amount: 0 },
	] });
	const found = [];
	popups.add_withhold_tax_buttons = () => {};
	popups.add_withholding_tax_cert_fallback = (form, supplier) => found.push(supplier);
	frm.trigger("refresh");
	assert.deepEqual(found, ["B031"]);
	frm.doc.accounts.push({ party_type: "Supplier", party: "OTHER" });
	frm.trigger("refresh");
	assert.deepEqual(found, ["B031"]);
});

test("manual customer receipt still routes to its amount popup", async () => {
	const { frm, popups } = setup({ accounts: [{ party_type: "Customer", party: "C1", credit_in_account_currency: 500 }] });
	let opened = false;
	popups.ask_receive_withholding = () => { opened = true; };
	await popups.withhold_manual(frm);
	assert.equal(opened, true);
});

function manualJournal(bankCredit = 10400, taxCredit = 300) {
	const row = (name, account, debit, credit, extra = {}) => ({
		doctype: "Journal Entry Account", name, account, exchange_rate: 1,
		debit, credit, debit_in_account_currency: debit, credit_in_account_currency: credit, ...extra,
	});
	return {
		voucher_type: "Journal Entry", workflow_state: "Journal Entry Draft",
		apply_tds: 1, tax_withholding_category: "Services",
		accounts: [
			row("expense", "Expense", 10000, 0),
			row("vat", "VAT", 700, 0, {
				supplier: "B031", tax_invoice_number: "INV1", tax_invoice_date: "2026-11-09",
			}),
			row("tax", "WHT", 0, taxCredit),
			row("bank", "Bank", 0, bankCredit, { account_type: "Bank" }),
		],
	};
}

test("manual WHT with automatic checkbox prompts before changing any entered amount", async () => {
	const { frm, dialogs, context } = setup(manualJournal());
	const before = JSON.stringify(frm.doc.accounts);
	await frm.trigger("validate");
	assert.equal(dialogs[0].title, "Withholding Calculation");
	assert.equal(context.frappe.validated, false);
	assert.equal(JSON.stringify(frm.doc.accounts), before);
	assert.equal(frm.saved, undefined);
});

test("Manual keeps ledger amounts and disables automatic withholding explicitly", async () => {
	const { frm, dialogs } = setup(manualJournal());
	const before = JSON.stringify(frm.doc.accounts);
	await frm.trigger("validate");
	assert.match(dialogs[0].fields[1].options, /certificate must be prepared separately/);
	await dialogs[0].primary_action({ mode: "Manual" });
	assert.equal(frm.doc.apply_tds, 0);
	assert.equal(JSON.stringify(frm.doc.accounts), before);
	assert.equal(frm.saved, true);
});

test("Automatic adopts entered tax and normalizes either gross or net bank before native recalculation", async () => {
	for (const input of [10400, 10700]) {
		const { frm, dialogs, popups } = setup(manualJournal(input));
		await frm.trigger("validate");
		await dialogs[0].primary_action({ mode: "Automatic" });
		assert.equal(frm.doc.accounts[2].is_tax_withholding_account, 1);
		assert.equal(frm.doc.accounts[3].credit_in_account_currency, 10400);
		assert.equal(frm.doc.accounts[1].supplier, "B031");
		assert.equal(frm.saved, true);
		assert.equal(await popups.ask_journal_withholding_mode(frm), false);
	}
});

test("Automatic removes an empty WHT template row before native zero-row validation", async () => {
	const { frm, dialogs } = setup(manualJournal(10700, 0));
	await frm.trigger("validate");
	await dialogs[0].primary_action({ mode: "Automatic" });
	assert.equal(frm.doc.accounts.length, 3);
	assert.equal(frm.doc.accounts[2].credit_in_account_currency, 10700);
});

test("Automatic converts company net into the bank account currency", async () => {
	const doc = manualJournal();
	doc.accounts[3].exchange_rate = 40;
	doc.accounts[3].credit_in_account_currency = 260;
	const { frm, dialogs } = setup(doc);
	await frm.trigger("validate");
	await dialogs[0].primary_action({ mode: "Automatic" });
	assert.equal(frm.doc.accounts[3].credit_in_account_currency, 260);
});

test("Automatic rounds each company amount before balancing, matching native JE calculation", async () => {
	const doc = manualJournal(1.06, 0.03);
	doc.accounts[0].debit_in_account_currency = 0.01;
	doc.accounts[0].exchange_rate = 33.4;
	doc.accounts[0].account_currency = "USD";
	doc.accounts[1].debit_in_account_currency = 0.07;
	for (let i = 0; i < 2; i++) {
		doc.accounts.push({ ...doc.accounts[0], name: `expense-${i}` });
	}
	const { frm, dialogs, context } = setup(doc);
	context.precision = (field) => ["debit", "credit"].includes(field) ? 2 : 6;
	await frm.trigger("validate");
	await dialogs[0].primary_action({ mode: "Automatic" });
	assert.equal(frm.doc.accounts[3].credit_in_account_currency, 1.03);
});

test("Automatic rejects a negative bank credit before adopting tax rows", async () => {
	const { frm, dialogs } = setup(manualJournal(-10400));
	await frm.trigger("validate");
	await assert.rejects(dialogs[0].primary_action({ mode: "Automatic" }), /credit withholding rows/);
	assert.equal(frm.doc.accounts[2].is_tax_withholding_account, undefined);
	assert.equal(frm.saved, undefined);
});

test("Automatic rejects debit withholding without mutating the rows", async () => {
	const doc = manualJournal();
	doc.accounts[2].debit_in_account_currency = 300;
	const { frm, dialogs } = setup(doc);
	const before = JSON.stringify(frm.doc.accounts);
	await frm.trigger("validate");
	await assert.rejects(dialogs[0].primary_action({ mode: "Automatic" }), /credit withholding rows/);
	assert.equal(JSON.stringify(frm.doc.accounts), before);
	assert.equal(frm.saved, undefined);
});

test("Pending approval, unrelated accounts and ambiguous banks are not converted", async () => {
	for (const change of [
		(doc) => { doc.workflow_state = "Journal Entry Pending Review"; },
		(doc) => { doc.accounts[2].account = "Other tax account"; },
		(doc) => { doc.accounts.push({ account_type: "Bank" }); },
		(doc) => { doc.accounts[0].party_type = "Supplier"; doc.accounts[0].party = "B031"; },
	]) {
		const doc = manualJournal();
		change(doc);
		const { frm, dialogs, popups } = setup(doc);
		assert.equal(await popups.ask_journal_withholding_mode(frm), false);
		assert.equal(dialogs.length, 0);
	}
});

test("Mixed managed and manual WHT is refused before offering a misleading Manual option", async () => {
	const doc = manualJournal();
	doc.accounts.push({ is_tax_withholding_account: 1 });
	const { frm, popups, dialogs } = setup(doc);
	await assert.rejects(popups.ask_journal_withholding_mode(frm), /already has automatic/);
	assert.equal(dialogs.length, 0);
});
