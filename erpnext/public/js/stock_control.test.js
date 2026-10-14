const assert = require("node:assert/strict");
const fs = require("node:fs");
const test = require("node:test");
const vm = require("node:vm");

function load() {
	const handlers = {};
	const context = {
		erpnext: { stock_control: {} },
		frappe: {
			provide() {},
			ui: { form: { on(doctype, events) { handlers[doctype] = events; } } },
			get_meta() { return { fields: [] }; },
		},
	};
	vm.createContext(context);
	vm.runInContext(fs.readFileSync(`${__dirname}/stock_control.js`, "utf8"), context);
	return { ...context, handlers };
}
const plain = (value) => JSON.parse(JSON.stringify(value));

test("preserves native Bin conditions, stock sorting query and existing filters", () => {
	const { erpnext } = load();
	const query = {
		query: "erpnext.controllers.queries.warehouse_query",
		filters: [["Warehouse", "company", "=", "Company"], ["Bin", "item_code", "=", "Item"]],
	};
	const before = plain(query);
	const result = erpnext.stock_control.add_filters(query, "Warehouse", "warehouse_type", ["Sales"]);
	assert.deepEqual(plain(query), before);
	assert.deepEqual(plain(result), {
		...before,
		filters: [...before.filters, ["Warehouse", "warehouse_type", "in", ["Sales"]]],
	});
});

test("keeps an existing Asset location constraint and denies empty allowed sources", () => {
	const { erpnext } = load();
	const result = erpnext.stock_control.add_filters(
		{ filters: { location: "Main", company: "Company" } }, "Asset", "location", []
	);
	assert.deepEqual(plain(result.filters), [
		["Asset", "location", "=", "Main"],
		["Asset", "company", "=", "Company"],
		["Asset", "location", "in", [""]],
	]);
});

test("feature off preserves query; later native set_query still receives policy", () => {
	const { erpnext, handlers } = load();
	const field = { get_query: () => ({ filters: { company: "Company", is_group: 0 } }) };
	const frm = {
		meta: { fields: [{ fieldname: "set_warehouse", fieldtype: "Link", options: "Warehouse" }] },
		fields_dict: { set_warehouse: field },
		set_query(name, query) { this.fields_dict[name].get_query = query; },
	};
	handlers["Sales Order"].setup(frm);
	assert.deepEqual(plain(field.get_query()), { filters: { company: "Company", is_group: 0 } });
	frm.__stock_control_options = { operation: "sales", warehouse_types: ["Sales"] };
	frm.set_query("set_warehouse", () => ({ filters: { company: "New Company", disabled: 0 } }));
	assert.deepEqual(plain(field.get_query().filters), {
		company: "New Company", disabled: 0, warehouse_type: ["in", ["Sales"]],
	});
	const wrapper = field.get_query;
	erpnext.stock_control.wrap_queries(frm);
	assert.equal(field.get_query, wrapper);
});


test("Material Request filters source warehouses but preserves loan and transfer destinations", () => {
	const { erpnext } = load();
	const names = ["warehouse", "set_warehouse", "from_warehouse", "set_from_warehouse"];
	const fields_dict = Object.fromEntries(names.map((name) => [name, {
		get_query: () => ({ filters: { company: "Company", is_group: 0 } }),
	}]));
	const frm = {
		doc: { doctype: "Material Request", material_request_type: "Customer Borrow / Loan" },
		meta: { fields: names.map((fieldname) => ({ fieldname, fieldtype: "Link", options: "Warehouse" })) },
		fields_dict,
		__stock_control_options: { operation: "loan", warehouse_types: ["Loan Source"] },
	};
	erpnext.stock_control.wrap_queries(frm);
	for (const name of ["warehouse", "set_warehouse"]) {
		assert.deepEqual(plain(fields_dict[name].get_query().filters), { company: "Company", is_group: 0 });
	}
	for (const name of ["from_warehouse", "set_from_warehouse"]) {
		assert.deepEqual(plain(fields_dict[name].get_query().filters), {
			company: "Company", is_group: 0, warehouse_type: ["in", ["Loan Source"]],
		});
	}
	frm.doc.material_request_type = "Material Transfer";
	assert.equal(fields_dict.warehouse.get_query().filters.warehouse_type, undefined);
	frm.doc.material_request_type = "Material Issue";
	assert.deepEqual(plain(fields_dict.warehouse.get_query().filters.warehouse_type), ["in", ["Loan Source"]]);
});
