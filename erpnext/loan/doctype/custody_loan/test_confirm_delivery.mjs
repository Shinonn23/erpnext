import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

test("asset handover opens its unsaved movement for review and submission", async () => {
	const source = readFileSync(new URL("./custody_loan.js", import.meta.url), "utf8");
	const handlers = {}, buttons = {}, calls = [], synced = [], routes = [];
	const movement = { doctype: "Asset Movement", name: "new-asset-movement", __islocal: 1, docstatus: 0 };
	const frappe = {
		ui: { form: { on: (name, events) => { handlers[name] = events; } } },
		model: { can_read: () => false, can_create: (doctype) => doctype === "Asset Movement", sync: (doc) => synced.push(doc) },
		set_route: (...args) => routes.push(args),
	};
	const context = vm.createContext({ frappe, __: (s) => s });
	vm.runInContext(source, context);
	context.show_loan_summary = () => {};
	const frm = {
		doc: { docstatus: 1, status: "Pending Delivery", loan_type: "Asset" },
		set_intro() {},
		add_custom_button: (name, callback) => { buttons[name] = callback; return { addClass() {} }; },
		call: async (method) => { calls.push(method); return { message: movement }; },
		reload_doc: async () => calls.push("reload"),
	};
	handlers["Custody Loan"].refresh(frm);
	assert.equal(buttons["Confirm Delivery"], undefined);
	assert.deepEqual(calls, []);
	buttons["Asset Movement"]();
	await new Promise(resolve => setImmediate(resolve));
	assert.deepEqual(calls, ["prepare_asset_movement"]);
	assert.deepEqual(synced, [movement]);
	assert.deepEqual(routes, [["Form", "Asset Movement", movement.name]]);
	assert.equal(movement.__islocal, 1);
	assert.equal(movement.docstatus, 0);
});
