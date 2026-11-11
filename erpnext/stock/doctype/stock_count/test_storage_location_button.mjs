import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("./stock_count.js", import.meta.url), "utf8");
const handler = source.match(/\n\twarehouse\(frm\) \{([\s\S]*?)\n\t\},/)[1];
const context = { erpnext: { stock_count: {} }, __: (s) => s };
const warehouse = vm.runInNewContext(`(frm) => {${handler}\n}`, context);

test("selecting a warehouse shows the location selector only on a draft count", () => {
	let buttons = [];
	const frm = {
		doc: { docstatus: 0, status: "Draft", warehouse: "WH" },
		remove_custom_button: (name) => { buttons = buttons.filter((b) => b !== name); },
		add_custom_button: (name) => buttons.push(name),
	};
	warehouse(frm);
	warehouse(frm);
	assert.deepEqual(buttons, ["Add Storage Locations"]);
	frm.doc.warehouse = "";
	warehouse(frm);
	assert.deepEqual(buttons, []);
	frm.doc.warehouse = "WH";
	frm.doc.status = "In Progress";
	warehouse(frm);
	assert.deepEqual(buttons, []);
});
