import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = readFileSync(new URL("../../assets/doctype/material_request/material_request.js", import.meta.url), "utf8");
const start = source.indexOf("function configure_material_request_item_columns(");
const end = source.indexOf("\n// The Responsible Employee", start);
const rows = { "Material Request Item": { ROW: { warehouse: "SOURCE", item_code: "ITEM", batch_no: "BATCH" } } };
const configure = vm.runInNewContext(`(${source.slice(start, end)})`, {
	__: (text) => text, locals: rows,
	erpnext: { queries: { storage_location: (warehouse, item_code) => ({ filters: { warehouse, item_code } }) } },
});

function columns(purpose, schedule_date = null) {
	const visibility = {}, required = {}, queries = {}, labels = {};
	const grid = { set_column_disp_in_list_view(fields, show, force) {
		for (const field of fields) { visibility[field] = show; required[field] = Boolean(force); }
	}, update_docfield_property(field, property, value) { labels[field] = value; } };
	configure({ doc: { material_request_type: purpose, schedule_date }, fields_dict: { items: { grid } },
		set_query(field, table, query) { queries[field] = query; } });
	return { visibility, required, queries, labels };
}

test("each purpose displays its relevant asset or quantity and warehouse columns", () => {
	for (const [purpose, wanted] of [
		["Asset Transfer", ["asset", "source_location", "target_location"]],
		["Purchase", ["qty", "warehouse"]],
		["Material Transfer", ["qty", "warehouse", "from_warehouse"]],
		["Material Issue", ["qty", "warehouse"]],
		["Customer Demo", ["qty"]],
		["Customer Borrow / Loan", ["qty"]],
	]) {
		const { visibility, required } = columns(purpose);
		assert.equal(visibility.item_code, true, `${purpose}: item_code`);
		assert.equal(required.item_code, true, `${purpose}: item_code is required`);
		for (const field of ["asset", "source_location", "target_location", "qty", "warehouse", "from_warehouse"]) {
			assert.equal(visibility[field], wanted.includes(field), `${purpose}: ${field}`);
			if (wanted.includes(field)) assert.equal(required[field], true);
		}
	}
});

test("asset transfers hide cost and unit and row dates follow the header", () => {
	assert.equal(columns("Asset Transfer").visibility.rate, false);
	assert.equal(columns("Asset Transfer").visibility.uom, false);
	assert.equal(columns("Purchase").visibility.schedule_date, true);
	assert.equal(columns("Purchase", "2026-11-12").visibility.schedule_date, false);
	assert.equal(columns("Customer Demo").visibility.schedule_date, false);
	assert.equal(columns("Customer Demo").visibility.rate, false);
	assert.equal(columns("Customer Demo").visibility.uom, true);
	assert.equal(columns("Customer Demo").required.uom, true);
	assert.equal(columns("Customer Borrow / Loan").visibility.uom, true);
	assert.equal(columns("Customer Borrow / Loan").required.uom, true);
	assert.equal(columns("Purchase").required.uom, true);
});

test("Material Issue uses the selected source warehouse for the only storage location column", () => {
	const issue = columns("Material Issue");
	assert.equal(issue.visibility.storage_location, true);
	assert.equal(issue.visibility.from_storage_location, false);
	assert.equal(issue.labels.storage_location, "Source Storage Location");
	const query = issue.queries.storage_location({ material_request_type: "Material Issue" }, "Material Request Item", "ROW");
	assert.equal(query.filters.warehouse, "SOURCE");
	assert.equal(query.filters.only_stock, 1);
	assert.equal(query.filters.batch_no, "BATCH");
	const transfer = columns("Material Transfer");
	assert.equal(transfer.visibility.from_storage_location, true);
	assert.equal(transfer.labels.storage_location, "Target Storage Location");
});

test("switching to Material Issue labels warehouse as the issue source", () => {
	const native = readFileSync(new URL("../../stock/doctype/material_request/material_request.js", import.meta.url), "utf8");
	const eventStart = native.indexOf("\tmaterial_request_type: function (frm)");
	const eventEnd = native.indexOf("\n\tset_warehouse_label(frm)", eventStart);
	const labelEnd = native.indexOf("\n});", eventEnd);
	const events = vm.runInNewContext(`({${native.slice(eventStart, eventEnd)}${native.slice(eventEnd, labelEnd)}})`,
		{ __: (text) => text, refresh_field() {} });
	const labels = [];
	const frm = { doc: { material_request_type: "Material Issue" }, toggle_reqd() {},
		fields_dict: { items: { grid: { update_docfield_property: (...args) => labels.push(args) } } },
		set_df_property: (...args) => labels.push(args), trigger: (event) => events[event](frm) };
	events.material_request_type(frm);
	assert.deepEqual(labels, [["warehouse", "label", "From Warehouse"], ["set_warehouse", "label", "Set From Warehouse"]]);
});
