import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

test("delivery is posted only after confirming physical handover", async () => {
	const source = readFileSync(new URL("./custody_loan.js", import.meta.url), "utf8");
	const handlers = {}, buttons = {}, calls = [];
	let confirm;
	const frappe = {
		ui: { form: { on: (name, events) => { handlers[name] = events; } } },
		model: { can_submit: () => true, can_read: () => false, can_create: () => false },
		confirm: (_message, yes) => { confirm = yes; },
	};
	const context = vm.createContext({ frappe, __: (s) => s });
	vm.runInContext(source, context);
	context.show_loan_summary = () => {};
	const frm = {
		doc: { docstatus: 1, status: "Pending Delivery", loan_type: "Asset" },
		set_intro() {},
		add_custom_button: (name, callback) => { buttons[name] = callback; return { addClass() {} }; },
		call: async (method) => calls.push(method),
		reload_doc: async () => calls.push("reload"),
	};
	handlers["Custody Loan"].refresh(frm);
	buttons["Confirm Delivery"]();
	assert.deepEqual(calls, []);
	await confirm();
	assert.deepEqual(calls, ["confirm_delivery", "reload"]);
});
