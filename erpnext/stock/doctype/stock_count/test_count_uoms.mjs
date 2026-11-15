import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./stock_count.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("erpnext.stock_count.count_in_uom ="));
let dialog, messages = [], row;
const context = {
	erpnext: { stock_count: {} }, __: (s) => s, flt: Number,
	frappe: {
		ui: { Dialog: class { constructor(options) { this.options = options; dialog = this; } show() {} hide() {} } },
		model: { set_value: async (_dt, _name, field, value) => { row[field] = value; } },
		msgprint: (message) => messages.push(message),
	},
};
vm.runInNewContext(code, context);
function open() {
	row = { idx: 1, doctype: "Stock Count Item", name: "ROW", item_code: "LYSE", counted_qty: 0 };
	messages = [];
	context.erpnext.stock_count.count_in_uom({ doc: { items: [row] }, refresh_field() {} });
	dialog.count_units = { row: "1: LYSE —  — ", stock_uom: "Test", whole_stock: true,
		uoms: [{ uom: "Bottle", factor: 720 }, { uom: "Test", factor: 1 }] };
}

test("mixed packages convert to one canonical stock count", async () => {
	open();
	await dialog.options.primary_action({ row: "1: LYSE —  — ", uom: "Bottle", qty: 2 });
	assert.equal(row.counted_qty, 1440);
	await dialog.options.primary_action({ row: "1: LYSE —  — ", uom: "Test", qty: 3, add: 1 });
	assert.equal(row.counted_qty, 1443);
	assert.equal(row.stock_uom, "Test");
});

test("fractional stock units, serial overcounts and stale row conversions are rejected", async () => {
	open();
	await dialog.options.primary_action({ row: "1: LYSE —  — ", uom: "Test", qty: 0.5 });
	assert.equal(row.counted_qty, 0);
	assert.match(messages.pop(), /whole number/);
	row.serial_no = "SERIAL";
	await dialog.options.primary_action({ row: "1: LYSE —  — ", uom: "Test", qty: 2 });
	assert.equal(row.counted_qty, 0);
	assert.match(messages.pop(), /serial number/);
	dialog.count_units.row = "2: OTHER";
	await dialog.options.primary_action({ row: "1: LYSE —  — ", uom: "Test", qty: 1 });
	assert.equal(row.counted_qty, 0);
});
