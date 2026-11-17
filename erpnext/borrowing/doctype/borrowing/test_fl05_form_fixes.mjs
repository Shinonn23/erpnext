// FL05 walk (17 Nov 2026): the Borrowing Delivery Note keeps its Borrowed warehouse on the form, the native
// Create > Quality Inspection(s) works on a return row, and the Receiving Delivery Note button names its DocType.
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

test("Create > Receiving Delivery Note / Asset Movement names the DocType it creates", () => {
	const source = read("../borrowing_return/borrowing_return.js");
	assert.match(
		source,
		/open_generated_doc\(r\.message\)\),\s*__\("Create"\),\s*receipt_type\s*\)/
	);
});

test("Borrowing Create buttons whose label is not a DocType name say which DocType they create", () => {
	const ret = read("../borrowing_return/borrowing_return.js");
	assert.match(ret, /__\("Batch Exchange"\)[\s\S]*?__\("Create"\), "Stock Entry"\)/);
	const adj = read("../borrowing_adjustment/borrowing_adjustment.js");
	assert.match(adj, /__\("Consumption Invoice"\)[\s\S]*?\}, __\("Create"\), "Sales Invoice"\);/);
});
