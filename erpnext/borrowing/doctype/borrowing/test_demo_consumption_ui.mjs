import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");
const api = "erpnext.borrowing.demo_consumption.";
const asset = "/assets/erpnext/js/loan_demo_consumption.js";
const translate = (message, args) => (args ? message.replace("{0}", args[0]) : message);
const plain = (value) => JSON.parse(JSON.stringify(value));

test("the ERPNext dialog retries with one token and groups selected serials by loan item and batch", async () => {
	const calls = [];
	const notices = [];
	const erpnext = { borrowing: { demo_consumption: {} } };
	let dialog;
	let hideCount = 0;
	let doneCount = 0;
	let tokens = 0;
	const frappe = {
		provide() {},
		ui: {
			Dialog: class {
				constructor(config) {
					dialog = config;
				}
				show() {}
				hide() {
					hideCount++;
				}
			},
		},
		datetime: { get_today: () => "2026-11-13" },
		utils: { escape_html: (value) => value.replace("<", "&lt;") },
		msgprint: (value) => notices.push(value),
		async call(call) {
			calls.push(plain(call));
			if (call.method === api + "options") {
				return {
					message: [
						{
							loan_item: "ROW",
							item_code: "ITEM",
							batch_no: "LOT",
							qty: 1,
							serial_per_unit: 2,
							serial_nos: ["S1", "S2"],
						},
						{ loan_item: "ROW", item_code: "ITEM", batch_no: "LOT-2", qty: 3, serial_nos: [] },
					],
				};
			}
			if (calls.length === 2) throw new Error("transient failure");
			return { message: { stock_entry: "SE-<1" } };
		},
	};
	vm.runInNewContext(read("../../../public/js/loan_demo_consumption.js"), {
		frappe,
		erpnext,
		__: translate,
		crypto: { randomUUID: () => `token-${++tokens}` },
	});
	await erpnext.borrowing.demo_consumption.open("LOAN-1", () => doneCount++);
	const table = dialog.fields.find(({ fieldname }) => fieldname === "items");
	assert.deepEqual(plain(table.data.map(({ serial_no, qty }) => [serial_no, qty])), [
		["S1", 0.5],
		["S2", 0.5],
		["", 0],
	]);
	assert.equal(dialog.fields.find(({ fieldname }) => fieldname === "customer").reqd, 1);
	assert.equal(
		dialog.fields.find(({ fieldname }) => fieldname === "demo_date").default,
		"2026-11-13"
	);
	const values = {
		customer: "C1",
		demo_date: "2026-11-13",
		items: table.data.map((row) => ({ ...row, selected: 1 })),
	};
	await assert.rejects(dialog.primary_action(values), /transient failure/);
	assert.equal(hideCount, 0);
	await dialog.primary_action(values);
	assert.deepEqual(calls, [
		{ method: api + "options", args: { name: "LOAN-1" } },
		...Array.from({ length: 2 }, () => ({
			method: api + "confirm",
			args: {
				name: "LOAN-1",
				customer: "C1",
				demo_date: "2026-11-13",
				selections: [{ loan_item: "ROW", batch_no: "LOT", qty: 1, serial_no: "S1\nS2" }],
				token: "token-1",
			},
			freeze: true,
		})),
	]);
	assert.equal(tokens, 1);
	assert.equal(hideCount, 1);
	assert.equal(doneCount, 1);
	assert.match(notices[0], /SE-&lt;1/);
});

test("native Borrowing checks confirmation eligibility and opens the dialog from the same record", async () => {
	const requests = [];
	const buttons = [];
	const handlers = {};
	const opened = [];
	const frm = {
		doc: {
			name: "LOAN-1",
			docstatus: 1,
			loan_type: "Stock",
			borrower_type: "Employee",
			status: "Active",
		},
		dashboard: { clear_headline() {}, add_indicator() {} },
		fields_dict: {},
		set_intro() {},
		add_custom_button: (...button) => buttons.push(button),
		reload_doc: () => opened.push("reload"),
	};
	let allowed = false;
	const frappe = {
		ui: {
			form: {
				on: (doctype, events) => {
					handlers[doctype] = events;
				},
			},
		},
		model: { can_create: () => false },
		async call(request) {
			requests.push(plain(request));
			return { message: allowed };
		},
		require: async (path) => {
			assert.equal(path, asset);
		},
	};
	const erpnext = {
		borrowing: {
			demo_consumption: {
				open: (name, done) => {
					opened.push(name);
					done();
				},
			},
		},
	};
	vm.runInNewContext(read("./borrowing.js"), { frappe, erpnext, __: translate });
	await handlers.Borrowing.refresh(frm);
	assert.equal(buttons.length, 0);
	allowed = true;
	await handlers.Borrowing.refresh(frm);
	assert.deepEqual(requests, [
		{ method: api + "can_confirm", args: { name: "LOAN-1" } },
		{ method: api + "can_confirm", args: { name: "LOAN-1" } },
	]);
	assert.deepEqual([buttons[0][0], ...buttons[0].slice(2)], ["Demo Consumption", "Create", false]);
	await buttons[0][1]();
	assert.deepEqual(opened, ["LOAN-1", "reload"]);
	frm.doc.borrower_type = "Customer";
	await handlers.Borrowing.refresh(frm);
	frm.doc.borrower_type = "Employee";
	for (const status of ["Pending Receipt", "Pending Quality Inspection"]) {
		frm.doc.status = status;
		await handlers.Borrowing.refresh(frm);
	}
	assert.equal(requests.length, 2);
});

test("the native Borrowing list opens the existing page and the page reloads after recording consumption", async () => {
	const route = "my-demo-consumption";
	const definition = JSON.parse(read("../../page/my_demo_consumption/my_demo_consumption.json"));
	assert.deepEqual(
		[definition.name, definition.module, definition.roles],
		[route, "Borrowing", [{ role: "Desk User" }]]
	);

	const requests = [];
	const buttons = [];
	const page = {
		body: {},
		set_primary_action: (label, reload) => {
			page.reload = reload;
		},
	};
	const messages = [];
	const frappe = {
		listview_settings: {},
		pages: { [route]: {} },
		set_route: (value) => {
			assert.equal(value, route);
		},
		ui: { make_app_page: () => page },
		call: async (request) => {
			requests.push(plain(request));
			return {
				message: {
					totals: [{ item_code: "ITEM", qty: 2, uom: "Box" }],
					loans: [
						{
							name: "LOAN-1",
							items: [{ item_code: "ITEM", qty: 2, uom: "Box", batch_no: "LOT" }],
						},
					],
				},
			};
		},
		require: async (path) => {
			assert.equal(path, asset);
		},
	};
	const erpnext = {
		borrowing: {
			demo_consumption: {
				open: (name, done) => {
					assert.equal(name, "LOAN-1");
					return done();
				},
			},
		},
	};
	const $ = () => {
		const element = {
			appendTo: () => element,
			empty() {},
			text: (value) => {
				messages.push(value);
				return element;
			},
			find: () => element,
			on: (_, action) => buttons.push(action),
		};
		return element;
	};
	vm.runInNewContext(read("./borrowing_list.js"), { frappe, __: translate });
	frappe.listview_settings.Borrowing.onload({
		page: {
			add_inner_button: (label, click) => {
				assert.equal(label, "My Demo Consumables");
				click();
			},
		},
	});
	vm.runInNewContext(read("../../page/my_demo_consumption/my_demo_consumption.js"), {
		frappe,
		erpnext,
		$,
		__: translate,
	});
	frappe.pages[route].on_page_load({});
	await new Promise(setImmediate);
	assert.ok(messages.includes("ITEM: 2 Box — LOT"));
	await buttons[0]();
	await page.reload();
	assert.deepEqual(
		requests,
		Array.from({ length: 3 }, () => ({ method: api + "my_outstanding", freeze: true }))
	);
});
