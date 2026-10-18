import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(
	new URL("../../stock/doctype/material_request/material_request.js", import.meta.url),
	"utf8"
);
const start = source.indexOf("\tonload: function (frm) {");
const end = source.indexOf("\n\tcompany: function (frm) {", start);
assert.ok(start >= 0 && end > start, "Use the actual Material Request onload handler");

function fixture({ isNew = false, priceList, defaultPriceList = "Standard Buying" } = {}) {
	const calls = [];
	const changes = [];
	const queries = [];
	const preserved = { addItem: 0, scheduleDate: 0, dimensions: 0 };
	const context = {
		erpnext: {
			utils: { add_item: () => preserved.addItem++ },
			accounts: { dimensions: { setup_dimension_filters: () => preserved.dimensions++ } },
		},
		set_schedule_date: () => preserved.scheduleDate++,
		frappe: {
			defaults: { get_default: () => defaultPriceList },
			call: (args) => calls.push(args),
		},
	};
	const handler = vm.runInNewContext("({" + source.slice(start, end) + "}).onload", context);
	const frm = {
		doc: { name: isNew ? "new-material-request-1" : "MAT-MR-1", buying_price_list: priceList },
		doctype: "Material Request",
		is_new: () => isNew,
		set_query: (...args) => queries.push(args[0]),
		set_value: (fieldname, value) => {
			changes.push([fieldname, value]);
			frm.doc[fieldname] = value;
		},
	};
	handler(frm);
	assert.deepEqual(preserved, { addItem: 1, scheduleDate: 1, dimensions: 1 });
	assert.deepEqual(queries, ["warehouse", "set_warehouse", "set_from_warehouse"]);
	return { frm, calls, changes, saved: () => (isNew = false) };
}

test("an existing MR with a redacted price list remains clean on every load", () => {
	for (let load = 0; load < 2; load++) {
		const ui = fixture();
		assert.equal(ui.calls.length, 0);
		assert.deepEqual(ui.changes, []);
		assert.equal(ui.frm.doc.buying_price_list, undefined);
	}
});

test("a new MR receives its default only after the existing read-permission check", () => {
	const ui = fixture({ isNew: true });
	assert.equal(ui.calls.length, 1);
	assert.equal(ui.calls[0].method, "frappe.client.has_permission");
	assert.equal(JSON.stringify(ui.calls[0].args), JSON.stringify({
		doctype: "Price List", docname: "Standard Buying", perm_type: "read",
	}));
	assert.deepEqual(ui.changes, []);
	ui.calls[0].callback({ message: { has_permission: true } });
	assert.deepEqual(ui.changes, [["buying_price_list", "Standard Buying"]]);
});

test("a new MR without permission or a configured default is not changed", () => {
	const ui = fixture({ isNew: true });
	ui.calls[0].callback({ message: { has_permission: false } });
	assert.deepEqual(ui.changes, []);
	const withoutDefault = fixture({ isNew: true, defaultPriceList: "" });
	assert.equal(withoutDefault.calls.length, 0);
	assert.deepEqual(withoutDefault.changes, []);
});

test("a delayed default cannot dirty the MR after it has been saved", () => {
	const ui = fixture({ isNew: true });
	ui.saved();
	ui.calls[0].callback({ message: { has_permission: true } });
	assert.deepEqual(ui.changes, []);
});

test("a delayed default cannot update another document after navigation", () => {
	const ui = fixture({ isNew: true });
	ui.frm.doc = { name: "new-material-request-2" };
	ui.calls[0].callback({ message: { has_permission: true } });
	assert.deepEqual(ui.changes, []);
});

test("an existing selection is kept before and during the permission request", () => {
	const selected = fixture({ isNew: true, priceList: "Chosen Buying" });
	assert.equal(selected.calls.length, 0);
	assert.deepEqual(selected.changes, []);
	const pending = fixture({ isNew: true });
	pending.frm.doc.buying_price_list = "Chosen Buying";
	pending.calls[0].callback({ message: { has_permission: true } });
	assert.deepEqual(pending.changes, []);
	assert.equal(pending.frm.doc.buying_price_list, "Chosen Buying");
});
