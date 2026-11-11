import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../pick_list.js", import.meta.url), "utf8").split('frappe.ui.form.on("Pick List"')[0];
class Scanner {
	constructor(opts) { Object.assign(this, opts); this.demand_ref_fields = ["sales_order_item"]; }
	update_table(data) { return Promise.resolve(this.get_row_to_modify_on_scan(data.item_code, data.batch_no)); }
	get_row_to_modify_on_scan(item) {
		return this.frm.doc.locations.find((row) => row.item_code === item && row.picked_qty < row.qty)
			|| this.frm.doc.locations.find((row) => !row.item_code);
	}
	show_alert() {}
	clean_up() {}
}
const context = { erpnext: { utils: { BarcodeScanner: Scanner } }, flt: Number, __: (s) => s };
vm.runInNewContext(`${source}\nglobalThis.PickScanner = PickListBarcodeScanner;`, context);

test("serial scans match the location and reject duplicates or exhausted demand", async () => {
	const rows = [
		{ item_code: "I", warehouse: "A", storage_location: "Shelf A", picked_qty: 1, qty: 2, serial_no: "SN1", sales_order_item: "SO1" },
		{ item_code: "I", warehouse: "B", storage_location: "Shelf B", picked_qty: 1, qty: 2, serial_no: "SN2", sales_order_item: "SO2" },
	];
	const scanner = new context.PickScanner({ frm: { doc: { locations: rows } } });
	const data = { item_code: "I", serial_no: "SN3", serial_warehouse: "B", serial_storage_location: "Shelf B" };
	assert.equal(await scanner.update_table(data), rows[1]);
	await assert.rejects(scanner.update_table({ ...data, serial_no: "SN1" }));
	await assert.rejects(scanner.update_table({ ...data, serial_warehouse: "C" }));
});

test("barcode scans use the existing SO row and never create another when it is full", async () => {
	const row = { item_code: "I", picked_qty: 0, qty: 2, sales_order_item: "SO1" };
	const scanner = new context.PickScanner({ frm: { doc: { locations: [row, {}] } } });
	assert.equal(await scanner.update_table({ item_code: "I", barcode: "123" }), row);
	row.picked_qty = 2;
	await assert.rejects(scanner.update_table({ item_code: "I", barcode: "123" }));
});
