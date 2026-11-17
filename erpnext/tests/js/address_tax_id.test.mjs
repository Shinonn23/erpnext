import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(
	new URL("../../public/js/thailand/address.js", import.meta.url), "utf8"
);

test("Tax ID lookup works after app load and updates its originating Address form", () => {
	let handlers, submit, request;
	const buttons = {};
	const values = {};
	const context = vm.createContext({
		__: (text) => text,
		cur_frm: null,
		frappe: {
			ui: { form: { on: (_doctype, events) => { handlers = events; } } },
			prompt: (_fields, callback) => { submit = callback; },
			call: (options) => { request = options; },
		},
	});
	vm.runInContext(source, context);
	const frm = {
		events: {},
		add_custom_button: (label, callback) => { buttons[label] = callback; },
		set_value: (field, value) => { values[field] = value; },
	};
	handlers.refresh(frm);
	buttons["By Tax ID"]();
	submit({ tax_id: "0105550000001", branch: "00000" });
	assert.equal(request.method, "erpnext.regional.thailand.utils.get_address_by_tax_id");
	assert.equal(request.args.tax_id, "0105550000001");
	assert.equal(request.args.branch, "00000");
	context.cur_frm = { set_value: () => assert.fail("Updated another form") };
	request.callback({ message: {
		name: "Company", address_line1: "123 Road", address_line2: "Tambon", city: "Amphur",
		state: "Province", pincode: "10100",
	} });
	assert.deepEqual(values, {
		address_title: "Company", address_line1: "123 Road", address_line2: "Tambon", city: "Amphur",
		state: "Province", pincode: "10100", tax_id: "0105550000001",
		branch_code: "00000", update_tax_branch: 1,
	});
	for (const message of [undefined, null]) {
		request.callback({ message });
		assert.equal(values.address_title, "Company");
	}
});
