import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(
	new URL("../../assets/doctype/material_request/material_request.js", import.meta.url),
	"utf8"
);
const plain = (value) => JSON.parse(JSON.stringify(value));

function fixture(reply) {
	const handlers = {};
	const calls = [];
	const changed = [];
	const queries = {};
	const row = { asset: "ASSET-A", item_code: "MODEL", demo_acquisition_method: "Wait Return" };
	const frm = {
		doc: {
			company: "CD",
			material_request_type: "Customer Demo",
			customer_location: "Customer Site",
		},
		fields_dict: { items: { grid: { set_column_disp_in_list_view() {}, update_docfield_property() {} } } },
		set_query: (...args) => {
			queries[args[0]] = args.at(-1);
		},
	};
	const frappe = {
		ui: {
			form: {
				on: (name, events) => {
					handlers[name] = events;
				},
			},
		},
		call: (options) => {
			calls.push(options);
			return reply;
		},
		db: {
			get_value: () => {
				throw new Error("Raw Asset/Item lookup must not be used");
			},
		},
		model: {
			set_value: (dt, name, values) => {
				changed.push(plain(values));
			},
		},
	};
	vm.runInNewContext(source, {
		frappe,
		locals: { "Material Request Item": { ROW: row } },
		__: (value) => value,
	});
	return { handlers, frm, row, calls, changed, queries };
}

const metadata = {
	name: "ASSET-A",
	asset_name: "Demo A",
	asset_serial_number: "SERIAL-A",
	item_code: "MODEL",
	company: "CD",
	location: "Stock Room",
	stock_uom: "Unit",
};

test("select-only Asset autofill uses the bounded RPC and carries request scope", async () => {
	const ui = fixture(Promise.resolve({ message: metadata }));
	await ui.handlers["Material Request Item"].asset(ui.frm, "Material Request Item", "ROW");
	assert.equal(ui.calls.length, 1);
	assert.equal(
		ui.calls[0].method,
		"erpnext.assets.doctype.material_request.material_request.get_asset_request_details"
	);
	assert.deepEqual(plain(ui.calls[0].args), {
		asset_name: "ASSET-A",
		company: "CD",
		material_request_type: "Customer Demo",
		item_code: "MODEL",
		acquisition_method: "Wait Return",
	});
	assert.equal(ui.changed[0].source_location, "Stock Room");
	assert.equal(ui.changed[0].target_location, "Customer Site");
	assert.equal(ui.changed[0].stock_uom, "Unit");
	assert.equal(ui.changed[0].qty, 1);
});

test("autofill does not copy unexpected valuation or depreciation data", async () => {
	const ui = fixture(
		Promise.resolve({
			message: {
				...metadata,
				valuation_rate: 9999,
				gross_purchase_amount: 9999,
				finance_books: ["PRIVATE"],
			},
		})
	);
	await ui.handlers["Material Request Item"].asset(ui.frm, "Material Request Item", "ROW");
	for (const field of [
		"valuation_rate",
		"gross_purchase_amount",
		"finance_books",
		"rate",
		"amount",
	]) {
		assert.equal(field in ui.changed[0], false);
	}
});

test("a denied select/company RPC never changes the row", async () => {
	const ui = fixture(Promise.reject(new Error("Permission denied")));
	await assert.rejects(
		ui.handlers["Material Request Item"].asset(ui.frm, "Material Request Item", "ROW")
	);
	assert.deepEqual(ui.changed, []);
});

test("late metadata cannot cross Asset, company or purpose changes", async () => {
	for (const change of [
		(ui) => {
			ui.row.asset = "OTHER";
		},
		(ui) => {
			ui.frm.doc.company = "UB";
		},
		(ui) => {
			ui.frm.doc.material_request_type = "Asset Transfer";
		},
	]) {
		let resolve;
		const ui = fixture(
			new Promise((done) => {
				resolve = done;
			})
		);
		const pending = ui.handlers["Material Request Item"].asset(
			ui.frm,
			"Material Request Item",
			"ROW"
		);
		change(ui);
		resolve({ message: metadata });
		await pending;
		assert.deepEqual(ui.changed, []);
	}
});

test("native Asset picker keeps the scoped selector interface", () => {
	const ui = fixture(Promise.resolve({ message: metadata }));
	ui.frm.doc.material_request_type = "Asset Transfer";
	ui.frm.doc.asset_source_location = "Stock Room";
	ui.handlers["Material Request"].setup(ui.frm);
	const query = ui.queries.asset(ui.frm.doc, "Material Request Item", "ROW");
	assert.equal(
		query.query,
		"erpnext.assets.doctype.material_request.material_request.asset_query"
	);
	assert.deepEqual(plain(query.filters), {
		company: "CD",
		material_request_type: "Asset Transfer",
		item_code: "MODEL",
		location: "Stock Room",
	});
});
