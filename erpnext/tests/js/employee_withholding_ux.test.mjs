import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function setup(doc) {
	const handlers = {};
	const queries = {};
	const properties = {};
	const dialogs = [];
	const popups = {};
	const choices = [
		{ name: "Company Services", income_tax_form: "PND53" },
		{ name: "Individual Services", income_tax_form: "PND3" },
		{ name: "Default Services", income_tax_form: "" },
		{ name: "Other Form", income_tax_form: "PND1" },
	].map((choice) => ({ ...choice, label: choice.name }));
	const frm = {
		doc: { doctype: "Payment Entry", docstatus: 0, company: "Company", ...doc },
		set_value(field, value) { this.doc[field] = value; },
		set_query(field, table, query) {
			queries[typeof table === "string" ? `${table}.${field}` : field] = query || table;
		},
		set_df_property(field, property, value) { properties[`${field}.${property}`] = value; },
	};
	const context = {
		__: (text) => text,
		erpnext: { regional: { thailand: { tax_popups: popups } } },
		frappe: {
			provide() {},
			ui: {
				form: { on(doctype, events) { handlers[doctype] = events; } },
				Dialog: function (options) {
					dialogs.push(options); this.show = () => {}; this.hide = () => {};
				},
			},
			prompt(fields) { dialogs.push({ fields }); },
			db: { async get_value() { return { message: { supplier_type: "Company" } }; } },
			model: { can_create: () => false },
		},
	};
	vm.createContext(context);
	for (const file of [
		"public/js/thailand/tax_popups.js",
		"public/js/thailand/payment_entry.js",
		"regional/doctype/withholding_tax_cert/withholding_tax_cert.js",
	]) {
		vm.runInContext(fs.readFileSync(new URL(`../../${file}`, import.meta.url), "utf8"), context);
	}
	popups.get_withholding_choices = async () => choices;
	popups.add_withhold_tax_buttons = () => {};
	return { frm, handlers, dialogs, popups, queries, properties };
}

test("Employee category popup excludes company/unsupported forms and replaces invalid current choice", async () => {
	const { frm, dialogs, popups } = setup({ party_type: "Employee" });
	await popups.ask_withholding_category(frm, { date: "2026-11-09", current: "Company Services" });
	const field = dialogs[0].fields.find((field) => field.fieldname === "category");
	assert.deepEqual(Array.from(field.options, (choice) => choice.value), [
		"Individual Services", "Default Services",
	]);
	assert.equal(field.default, "Individual Services");
});

test("Supplier category popup retains its category choices", async () => {
	const { frm, dialogs, popups } = setup({ party_type: "Supplier" });
	await popups.ask_withholding_category(frm, { current: "Company Services" });
	assert.equal(dialogs[0].fields[0].options.length, 4);
	assert.equal(dialogs[0].fields[0].default, "Company Services");
});

test("Employee fallback certificate permits PND3 only; Supplier keeps both forms", async () => {
	for (const party_type of ["Employee", "Supplier"]) {
		const { frm, dialogs, popups } = setup({ party_type, tax_withholding_category: "Company Services" });
		await popups.create_withholding_tax_cert(frm, "Supplier");
		const form = dialogs[0].fields.find((field) => field.fieldname === "income_tax_form");
		assert.equal(form.options, party_type === "Employee" ? "PND3" : "PND3\nPND53");
		assert.equal(form.default, party_type === "Employee" ? "PND3" : "PND53");
	}
});

test("native Employee category link filters follow party changes in header and table", () => {
	const { frm, handlers, queries } = setup({ party_type: "Employee" });
	handlers["Payment Entry"].refresh(frm);
	for (const field of ["tax_withholding_category", "tax_withholding_categories.tax_withholding_category"]) {
		assert.deepEqual(Array.from(queries[field]().filters.thai_income_tax_form[1]), ["", "PND3"]);
		frm.doc.party_type = "Supplier";
		assert.deepEqual(Object.keys(queries[field]().filters), []);
		frm.doc.party_type = "Employee";
	}
});

test("manual certificate selects PND3 for Employee and restores Supplier choices", () => {
	const { frm, handlers, properties } = setup({ employee: "EMP-1", income_tax_form: "PND53" });
	handlers["Withholding Tax Cert"].employee(frm);
	assert.equal(frm.doc.income_tax_form, "PND3");
	assert.equal(properties["income_tax_form.options"], "PND3");
	frm.doc.employee = null;
	handlers["Withholding Tax Cert"].employee(frm);
	assert.equal(properties["income_tax_form.options"], "\nPND3\nPND53");
});
