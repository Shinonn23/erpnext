import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function setup(doctype, values = {}, can_create = true) {
	const handlers = [];
	const buttons = [];
	const mapped = [];
	const folder = doctype.toLowerCase().replaceAll(" ", "_");
	const context = {
		__: (text) => text,
		flt: (value) => Number(value) || 0,
		frappe: {
			ui: { form: { on(name, events) { if (name === doctype) handlers.push(events); } } },
			model: {
				can_write: () => false,
				can_create: (name) => { assert.equal(name, "Payment Entry"); return can_create; },
				open_mapped_doc: (options) => mapped.push(options),
			},
		},
	};
	vm.createContext(context);
	vm.runInContext(fs.readFileSync(new URL(
		`../../regional/doctype/${folder}/${folder}.js`, import.meta.url
	), "utf8"), context);
	const frm = {
		doctype,
		doc: { name: "BILL-1", docstatus: 1, closed: 0, total_outstanding_amount: 130, ...values },
		fields_dict: { [`${folder}_line`]: { grid: { get_field: () => ({}) } } },
		add_custom_button(label, action, group) {
			buttons.push({ label, action, group });
			return { addClass() {} };
		},
	};
	for (const events of handlers) events.refresh?.(frm);
	return { frm, buttons, mapped, folder };
}

for (const doctype of ["Sales Billing", "Purchase Billing"]) {
	test(`${doctype} opens an unsaved native Payment Entry under Create`, () => {
		const { frm, buttons, mapped, folder } = setup(doctype);
		const button = buttons.find((button) => button.label === "Payment Entry");
		assert.equal(button.group, "Create");
		button.action();
		assert.equal(mapped.length, 1);
		assert.equal(mapped[0].frm, frm);
		assert.equal(mapped[0].method,
			`erpnext.regional.doctype.${folder}.${folder}.make_payment_entry`);
	});

	test(`${doctype} hides Payment Entry for ineligible billing or missing create permission`, () => {
		for (const values of [
			{ docstatus: 0 }, { docstatus: 2 }, { closed: 1 },
			{ total_outstanding_amount: 0 }, { total_outstanding_amount: -30 },
		]) {
			assert.ok(!setup(doctype, values).buttons.some((button) => button.label === "Payment Entry"));
		}
		assert.ok(!setup(doctype, {}, false).buttons.some((button) => button.label === "Payment Entry"));
	});
}

test("Sales Billing retains its separate Draft Multi-Payments action with create permission", () => {
	assert.ok(setup("Sales Billing").buttons.some((button) => button.label === "Create Multi-Payments"));
	assert.ok(!setup("Sales Billing", {}, false).buttons.some((button) => button.label === "Create Multi-Payments"));
});
