import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../item.js", import.meta.url), "utf8");

function openClassification(roles, category = "") {
	const buttons = [];
	let dialog;
	const frm = {
		doc: { name: "ITEM-1", regulatory_product_category: category },
		is_new: () => false,
		fields_dict: {
			regulatory_product_category: {
				df: { options: "\nMedical Device\nDrug\nOther\nNot Regulated" },
			},
		},
		add_custom_button: (label, action) => buttons.push({ label, action }),
	};
	vm.runInNewContext(`${source}\nadd_category_button(frm);`, {
		__: (text) => text,
		frappe: {
			user: { has_role: (role) => roles.includes(role) },
			ui: {
				form: { on() {} },
				Dialog: class {
					constructor(options) { dialog = options; }
					show() {}
				},
			},
		},
		frm,
	});
	buttons[0]?.action();
	return { buttons, dialog };
}

test("both RA roles must choose a nonblank category and default to Not Regulated", () => {
	for (const role of ["Regulatory Operator", "Regulatory Approver"]) {
		const { dialog } = openClassification([role]);
		const field = dialog.fields.find((field) => field.fieldname === "category");
		assert.equal(field.reqd, 1);
		assert.equal(field.default, "Not Regulated");
		assert.ok(!field.options.includes(""));
		assert.match(field.description, /Choose Not Regulated/);
	}
});

test("ordinary Item editors get no regulatory classification action", () => {
	assert.equal(openClassification(["Item Manager"]).buttons.length, 0);
});

test("the dialog keeps an existing regulated classification", () => {
	const { dialog } = openClassification(["Regulatory Operator"], "Drug");
	assert.equal(dialog.fields[0].default, "Drug");
});
