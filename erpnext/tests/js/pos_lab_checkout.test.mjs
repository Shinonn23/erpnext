import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function controller(stored) {
	const context = {
		$: () => ({ find: () => ({}) }),
		__: (t) => t,
		erpnext: { PointOfSale: {} },
		frappe: {
			db: { get_doc: async () => stored },
			model: { sync() {} },
			get_doc: () => undefined,
		},
	};
	vm.createContext(context);
	vm.runInContext(
		fs.readFileSync(
			new URL("../../selling/page/point_of_sale/pos_controller.js", import.meta.url),
			"utf8"
		),
		context
	);
	return context.erpnext.PointOfSale.Controller;
}

test("the summary after checkout uses the document stored after the workflow", async () => {
	const stored = { doctype: "POS Invoice", name: "P-1", docstatus: 1, status: "Paid" };
	const Controller = controller(stored);
	const self = { frm: { doc: { doctype: "POS Invoice", name: "P-1", docstatus: 0, status: "Draft" } } };
	await Controller.prototype.reload_submitted_invoice.call(self, self.frm.doc);
	assert.equal(self.frm.doc.status, "Paid");
	assert.equal(self.frm.doc.docstatus, 1);
});

test("a failed reload keeps the saved document", async () => {
	const Controller = controller();
	const self = { frm: { doc: { name: "P-1", status: "Draft" } } };
	Controller.prototype.reload_submitted_invoice.call(self, self.frm.doc);
	await Controller.prototype.reload_submitted_invoice.call(self, self.frm.doc);
	assert.equal(self.frm.doc.status, "Draft");
});

test("a POS return does not trigger the profile tax template", () => {
	const source = fs.readFileSync(
		new URL("../../accounts/doctype/pos_invoice/pos_invoice.js", import.meta.url),
		"utf8"
	);
	assert.match(source, /taxes_and_charges && !this\.frm\.doc\.is_return/);
});
