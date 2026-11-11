import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

test('selecting stock serials uses the stock selector and records its outward bundle', async () => {
  const handlers = {};
  const row = { item_code: 'MACHINE', warehouse: 'MAIN', qty: 1, storage_location: 'A1' };
  let chosen;
  let updated;
  const frappe = {
    ui: { form: { on: (name, events) => handlers[name] = events } },
    db: { get_value: async () => ({ message: { has_serial_no: 1, has_batch_no: 1 } }) },
    model: { set_value: (...args) => updated = args },
  };
  const erpnext = { SerialBatchPackageSelector: class {
    constructor(frm, item, callback) { chosen = { frm, item }; callback({ name: 'BUNDLE', warehouse: 'MAIN', total_qty: -2 }); }
  } };
  vm.runInNewContext(readFileSync(new URL('../stock_to_asset_conversion.js', import.meta.url), 'utf8'), {
    frappe, erpnext, locals: { Row: { one: row } }, __: (s) => s,
  });
  const frm = {};
  handlers['Stock to Asset Conversion Item'].pick_serial_and_batch(frm, 'Row', 'one');
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(chosen.item.type_of_transaction, 'Outward');
  assert.equal(chosen.item.storage_location, 'A1');
  assert.equal(updated[2].serial_and_batch_bundle, 'BUNDLE');
  assert.equal(updated[2].qty, 2);
});
