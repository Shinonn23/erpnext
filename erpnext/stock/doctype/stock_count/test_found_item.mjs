import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./stock_count.js", import.meta.url), "utf8");
const code = source.slice(source.indexOf("erpnext.stock_count.add_found_item ="), source.indexOf("erpnext.stock_count.add_storage_locations ="));
let dialog, messages = [], item;
const context = {
	erpnext: { stock_count: {} }, __: (s) => s, flt: Number,
	frappe: {
		ui: { Dialog: class { constructor(options) { this.options = options; dialog = this; } show() {} hide() {} } },
		db: { get_value: async () => ({ message: item }) },
		msgprint: (message) => messages.push(message),
	},
};
vm.runInNewContext(code, context);

test("found items require batch and serial when tracked, then add a zero-stock count", async () => {
	item = { item_name: "Item", is_stock_item: 1, has_batch_no: 1, has_serial_no: 1 };
	messages = [];
	const rows = [];
	const frm = { doc: { warehouse: "WH", items: rows }, add_child: (_table, row) => rows.push(row), refresh_field() {}, dirty() {} };
	context.erpnext.stock_count.add_found_item(frm);
	await dialog.options.primary_action({ item_code: "I", counted_qty: 1 });
	assert.match(messages.pop(), /Batch No/);
	await dialog.options.primary_action({ item_code: "I", batch_no: "NEW-BATCH", counted_qty: 1 });
	assert.match(messages.pop(), /Serial No/);
	await dialog.options.primary_action({ item_code: "I", batch_no: "NEW-BATCH", serial_no: "NEW-SN", counted_qty: 1 });
	assert.equal(rows.length, 1);
	assert.equal(rows[0].system_qty, 0);
	assert.equal(rows[0].difference_qty, 1);
	await dialog.options.primary_action({ item_code: "I", batch_no: "NEW-BATCH", serial_no: "NEW-SN", counted_qty: 1 });
	assert.match(messages.pop(), /already in the count/);
});
