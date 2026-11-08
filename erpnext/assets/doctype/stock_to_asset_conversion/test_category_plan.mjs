import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

function load() {
	const handlers = {};
	const calls = [];
	const frappe = {
		provide() {},
		ui: { form: { on: (doctype, events) => (handlers[doctype] = events) } },
		call: (args) => calls.push(args),
	};
	const erpnext = { asset: {} };
	const source = readFileSync(new URL("../asset/asset.js", import.meta.url), "utf8");
	vm.runInNewContext(source, { frappe, erpnext });
	return { handlers, calls };
}

test("choosing a category refreshes the native plan only on a Draft Asset", () => {
	const { handlers } = load();
	const triggered = [];
	const frm = { doc: { docstatus: 0 }, trigger: (event) => triggered.push(event) };
	handlers.Asset.asset_category(frm);
	assert.deepEqual(triggered, ["item_code"]);
	frm.doc.docstatus = 1;
	handlers.Asset.asset_category(frm);
	assert.deepEqual(triggered, ["item_code"]);
});

test("a late response for another category cannot overwrite the chosen plan", () => {
	const { handlers, calls } = load();
	const updates = [];
	const frm = {
		doc: { item_code: "ITEM", net_purchase_amount: 10000, asset_category: "Normal" },
		set_value: (field, value) => updates.push([field, value]),
	};
	handlers.Asset.set_finance_book(frm);
	assert.equal(calls[0].args.asset_category, "Normal");
	frm.doc.asset_category = "One Period";
	calls[0].callback({ message: [{ total_number_of_depreciations: 36 }] });
	assert.deepEqual(updates, []);
	handlers.Asset.set_finance_book(frm);
	calls[1].callback({ message: [{ total_number_of_depreciations: 1 }] });
	assert.equal(updates[0][0], "finance_books");
	assert.equal(updates[0][1][0].total_number_of_depreciations, 1);
});
