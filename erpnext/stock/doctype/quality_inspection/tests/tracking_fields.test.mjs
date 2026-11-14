import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../quality_inspection.js', import.meta.url), 'utf8');
const events = {};
const frappe = { ui: { form: { on: (doctype, handlers) => { events[doctype] = handlers; } } },
 db: { get_value: async () => ({ message: { has_serial_no: 1 } }) } };
vm.runInNewContext(source, { frappe, cur_frm: { cscript: {} }, __: (value) => value });

async function visibility(doc, hasSerial) {
 frappe.db.get_value = async () => ({ message: { has_serial_no: hasSerial } });
 const fields = {};
 await events['Quality Inspection'].show_purchase_tracking_fields({ doc, is_new: () => true, set_df_property: (field, _property, hidden) => { fields[field] = hidden; } });
 return fields;
}

test('purchase inspection shows tracking fields for its actual unit', async () => {
 const batch = await visibility({ purchase_unit_qty: 5, item_code: 'A', batch_no: 'B' }, 0);
 assert.equal(batch.item_serial_no, true); assert.equal(batch.batch_no, false);
 assert.equal(batch.purchase_rejected_serials, 1); assert.equal(batch.purchase_serials_section, true);
 const batchSerial = await visibility({ purchase_unit_qty: 5, item_code: 'A', batch_no: 'B' }, 1);
 assert.equal(batchSerial.purchase_rejected_serials, 1); assert.equal(batchSerial.purchase_serials_section, false);
 const serial = await visibility({ purchase_unit_qty: 1, item_serial_no: 'S', batch_no: 'B' }, 1);
 assert.equal(serial.item_serial_no, false); assert.equal(serial.batch_no, true);
 assert.equal(serial.purchase_rejected_serials, 1); assert.equal(serial.purchase_serials_section, true);
 const row = await visibility({ purchase_unit_qty: 5, item_code: 'A' }, 1);
 assert.equal(row.batch_no, true); assert.equal(row.item_serial_no, true); assert.equal(row.purchase_rejected_serials, 1);
 const legacy = await visibility({ purchase_unit_qty: 0 }, 0);
 assert.equal(legacy.item_serial_no, false); assert.equal(legacy.batch_no, false);
});

test('expected criteria follow numeric, value and formula template fields', () => {
 const rows = [
  { numeric: 1, min_value: 1.4, max_value: 2, status: 'Rejected' },
  { numeric: 0, value: 'Pass' },
  { formula_based_criteria: 1, acceptance_formula: 'reading_1 > 5' },
  { numeric: 1, min_value: 0, max_value: 0 },
 ];
 const frm = { doc: { readings: rows }, refresh_field: () => {} };
 events['Quality Inspection Reading'].min_value(frm);
 assert.deepEqual(rows.map((row) => row.acceptance_display), ['1.4 – 2', 'Pass', 'reading_1 > 5', 'Manual assessment']);
 assert.equal(rows[0].status, 'Rejected');
});

test('all-manual switch and individual choices stay in sync', async () => {
 const rows = [
  { numeric: 0, value: 'Present', manual_inspection: 0 },
  { numeric: 1, min_value: 1, max_value: 2, manual_inspection: 0 },
  { numeric: 0, value: '', manual_inspection: 1 },
 ];
 const frm = { doc: { purchase_unit_qty: 3, readings: rows, manual_inspection: 1 },
  dirty: () => {}, refresh_field: () => {},
  set_value: async (name, value) => { frm.doc[name] = value; events['Quality Inspection'].manual_inspection(frm); } };
 events['Quality Inspection'].manual_inspection(frm);
 assert.deepEqual(rows.map((r) => r.manual_inspection), [1, 1, 1]);
 rows[1].manual_inspection = 0;
 await events['Quality Inspection Reading'].manual_inspection(frm);
 assert.equal(frm.doc.manual_inspection, 0);
 assert.deepEqual(rows.map((r) => r.manual_inspection), [1, 0, 1]);
 events['Quality Inspection'].manual_inspection(frm);
 assert.deepEqual(rows.map((r) => r.manual_inspection), [0, 0, 1]);
});
