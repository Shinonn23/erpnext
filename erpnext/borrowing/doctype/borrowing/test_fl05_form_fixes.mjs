// FL05 walk (17 Nov 2026): the Borrowing Delivery Note keeps its Borrowed warehouse on the form, the native
// Create > Quality Inspection(s) works on a return row, and the return documents chain from the issue documents (UX WP4).
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const transaction = read("../../../public/js/controllers/transaction.js");

function method_body(source, name) {
	const start = source.indexOf(`\t${name}() {`);
	assert.ok(start > 0, name);
	return source.slice(start, source.indexOf("\n\t}\n", start));
}

test("a Borrowing document never takes the Company's in-transit warehouse", () => {
	const body = method_body(transaction, "set_default_internal_warehouse");
	const guard = body.indexOf("if (this.frm.doc.is_borrowing_transaction) return;");
	assert.ok(guard > 0);
	assert.ok(guard < body.indexOf("default_in_transit_warehouse"));
});

test("the Quality Inspection dialog counts the units of a return row", () => {
	assert.match(transaction, /qty: Math\.abs\(item\.qty\),/);
	assert.match(transaction, /sample_size: item\.sample_quantity \|\| \(item\.qty < 0 \? Math\.abs\(item\.qty\) : undefined\),/);
	const controller = read("../../../controllers/stock_controller.py");
	assert.match(controller, /flt\(item\.get\("sample_size"\)\) > abs\(flt\(item\.get\("qty"\)\)\)/);
});

test("a Borrowing Return inspection is never refused for the Manual Inspection tick", () => {
	const core = read("./borrowing_core.py");
	assert.doesNotMatch(core, /not quality_inspection\.manual_inspection/);
	assert.match(core, /self\.manual_inspection = 1/);
});

test("the return documents chain from the issue documents (UX WP4)", () => {
	const borrowing = read("./borrowing.js");
	assert.match(borrowing, /add_custom_button\(__\("Return"\), \(\) => make_borrowing_return\(frm\), __\("Create"\)\)/);
	assert.doesNotMatch(borrowing, /Borrowing Return/);
	const note = read("../../../stock/doctype/delivery_note/delivery_note.js");
	assert.match(note, /borrowing_core\.make_borrowing_return_delivery_note/);
	const movement = read("../../../assets/doctype/asset_movement/asset_movement.js");
	assert.match(movement, /__\("Return Movement"\)[\s\S]*?borrowing_core\.make_borrowing_return_movement/);
});

test("the Items grid shows the columns of the borrowing type only", () => {
	const borrowing = read("./borrowing.js");
	assert.match(borrowing, /function configure_item_columns\(frm\)/);
	assert.match(borrowing, /update_docfield_property\(fieldname, "in_list_view"/);
	assert.match(borrowing, /loan_type\(frm\) \{\s*configure_item_columns\(frm\);/);
});

test("Borrowing Create buttons whose label is not a DocType name say which DocType they create", () => {
	const adj = read("../borrowing_adjustment/borrowing_adjustment.js");
	assert.match(adj, /__\("Consumption Invoice"\)[\s\S]*?\}, __\("Create"\), "Sales Invoice"\);/);
});
