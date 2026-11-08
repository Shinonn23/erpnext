import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(
	new URL("../../public/js/controllers/transaction.js", import.meta.url),
	"utf8"
);
const ORDER_BASE = "Day(s) after order date";
const INVOICE_BASE = "Day(s) after invoice date";

function controller(doc, { basis = ORDER_BASE, dates = {}, lookupError = null } = {}) {
	const calls = [];
	const reads = [];
	const updates = [];
	const frappe = {
		call: async (options) => {
			calls.push(options);
			const row = { due_date_based_on: basis, due_date: options.args.order_date || null };
			const result = {
				message: options.method.endsWith("get_payment_terms") ? [row] : row,
			};
			options.callback?.(result);
			return result;
		},
		db: {
			get_value: async (doctype, name, field) => {
				reads.push([doctype, name, field]);
				if (lookupError) throw lookupError;
				return { message: { transaction_date: dates[name] || null } };
			},
		},
		ui: { form: { trigger() {} } },
	};
	const erpnext = { taxes_and_totals: class {} };
	const locals = { "Payment Schedule": {} };
	vm.runInNewContext(source, { erpnext, frappe, locals, refresh_field() {} });
	const object = new erpnext.TransactionController();
	object.frm = {
		doc,
		set_value: (field, value) => updates.push([field, value]),
		refresh_field() {},
	};
	object.get_company_currency = () => "THB";
	object.update_payment_schedule_grid_labels = () => {};
	object.recalculate_terms = () => {};
	return { object, calls, reads, updates, locals };
}

test("an order uses its date immediately when choosing an order-based template", async () => {
	const { object, calls, reads, updates } = controller({
		doctype: "Purchase Order", transaction_date: "2026-11-09", payment_terms_template: "T/T",
	});
	await object.payment_terms_template();
	assert.equal(calls.length, 1);
	assert.equal(calls[0].args.order_date, "2026-11-09");
	assert.equal(updates[0][1][0].due_date, "2026-11-09");
	assert.deepEqual(reads, []);
});

test("an invoice resolves all linked orders only for an order-based response", async () => {
	const { object, calls, reads, updates } = controller(
		{
			doctype: "Purchase Invoice", posting_date: "2026-12-01", payment_terms_template: "T/T",
			items: [{ purchase_order: "PO-1" }, { purchase_order: "PO-2" }],
		},
		{ dates: { "PO-1": "2026-11-09", "PO-2": "2026-11-09" } }
	);
	await object.payment_terms_template();
	assert.equal(calls.length, 2);
	assert.equal(calls[1].args.order_date, "2026-11-09");
	assert.equal(updates[0][1][0].due_date, "2026-11-09");
	assert.equal(reads.length, 2);
});

test("invoice-based credit does not add linked-order reads", async () => {
	const { object, reads, calls } = controller(
		{
			doctype: "Purchase Invoice", payment_terms_template: "Net 30",
			items: [{ purchase_order: "PO-1" }],
		},
		{ basis: INVOICE_BASE, lookupError: new Error("must not read") }
	);
	await object.payment_terms_template();
	assert.deepEqual(reads, []);
	assert.equal(calls.length, 1);
});

test("missing or conflicting order dates stay unresolved", async () => {
	for (const items of [
		[],
		[{ purchase_order: "PO-1" }, {}],
		[{ purchase_order: "PO-1" }, { purchase_order: "PO-2" }],
	]) {
		const { object, updates } = controller(
			{
				doctype: "Purchase Invoice", payment_terms_template: "T/T",
				posting_date: "2026-12-01", items,
			},
			{ dates: { "PO-1": "2026-11-09", "PO-2": "2026-11-10" } }
		);
		await object.payment_terms_template();
		assert.equal(updates[0][1][0].due_date, null);
	}
});

test("linked order permission errors are not replaced with a guessed anchor", async () => {
	const { object, updates } = controller(
		{
			doctype: "Purchase Invoice", payment_terms_template: "T/T",
			items: [{ purchase_order: "PO-1" }],
		},
		{ lookupError: new Error("PermissionError") }
	);
	await assert.rejects(object.payment_terms_template(), /PermissionError/);
	assert.deepEqual(updates, []);
});

test("selecting an individual term uses the same linked-order anchor", async () => {
	const { object, locals, reads } = controller(
		{ doctype: "Sales Invoice", posting_date: "2026-12-01", items: [{ sales_order: "SO-1" }] },
		{ dates: { "SO-1": "2026-11-09" } }
	);
	const row = { payment_term: "T/T" };
	locals["Payment Schedule"]["TERM-1"] = row;
	await object.payment_term(object.frm.doc, "Payment Schedule", "TERM-1");
	assert.equal(row.due_date, "2026-11-09");
	assert.deepEqual(reads, [["Sales Order", "SO-1", "transaction_date"]]);
});

test("invoice posting-date changes honor the selected template and order date", async () => {
	const { object, calls } = controller(
		{
			doctype: "Purchase Invoice", supplier: "SUP-1", posting_date: "2026-12-01",
			payment_terms_template: "Selected T/T", payment_schedule: [{ due_date_based_on: ORDER_BASE }],
			items: [{ purchase_order: "PO-1" }],
		},
		{ dates: { "PO-1": "2026-11-09" } }
	);
	await object.posting_date();
	assert.equal(calls[0].args.template_name, "Selected T/T");
	assert.equal(calls[0].args.order_date, "2026-11-09");
});
