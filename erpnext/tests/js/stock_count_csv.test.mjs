import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const countSource = fs.readFileSync(
  new URL("../../stock/doctype/stock_count/stock_count.js", import.meta.url), "utf8"
);
const gridSource = fs.readFileSync(
  new URL("../../../../frappe/frappe/public/js/frappe/form/grid.js", import.meta.url), "utf8"
);
const uploadSource = gridSource.slice(
  gridSource.indexOf("\tsetup_allow_bulk_edit() {"), gridSource.indexOf("\n\tsetup_download() {")
);
const setupFieldsSource = gridSource.slice(
  gridSource.indexOf("\tsetup_fields() {"), gridSource.indexOf("\n\t_apply_mask_overrides() {")
);
const itemMeta = JSON.parse(fs.readFileSync(
  new URL("../../stock/doctype/stock_count_item/stock_count_item.json", import.meta.url), "utf8"
));
const numberSource = fs.readFileSync(
  new URL("../../../../frappe/frappe/public/js/frappe/utils/number_format.js", import.meta.url), "utf8"
).replace('import "./datatype";', "");

function upload(fieldnames, rows, stockCount = true, numberFormat = "#,###.##", resetDocfield = false) {
  const handlers = {};
  let click;
  let uploader;
  const button = {
    removeClass() { return this; },
    on(event, callback) { assert.equal(event, "click"); click = callback; return this; },
  };
  const jquery = () => ({ find: () => button });
  jquery.each = (list, callback) => {
    for (let i = 0; i < list.length; i++) if (callback(i, list[i]) === false) break;
  };
  const grid = {
    df: { fieldname: "items", options: "Stock Count Item", allow_bulk_edit: 1 },
    wrapper: {},
    setup_download() {},
    fields_map: {},
    _apply_column_disp_overrides() {},
    _apply_mask_overrides() {},
  };
  const canonicalDocfield = { ...grid.df, fields: itemMeta.fields };
  let refreshCount = 0;
  const frm = {
    doctype: "Stock Count",
    docname: "STOCK-COUNT-TEST",
    doc: { items: [], status: "In Progress", docstatus: 0 },
    fields_dict: { items: { grid } },
    set_query() {},
    get_docfield: () => grid.df,
    clear_table() { this.doc.items = []; },
    add_child() {
      const row = { doctype: "Stock Count Item", name: `new-${this.doc.items.length}`, idx: this.doc.items.length + 1,
        counted_qty: 0, is_counted: 0, difference_qty: 0 };
      this.doc.items.push(row);
      return row;
    },
    refresh_field() { refreshCount++; },
  };
  grid.frm = frm;
  const context = {
    $: jquery, __: (s) => s, cint: (v) => parseInt(v, 10) || 0, window: {},
    frappe: {
      flags: {},
      boot: { sysdefaults: { number_format: numberFormat } },
      defaults: { is_enabled: () => false },
      ui: { form: { on: (doctype, events) => { handlers[doctype] = events; } },
        FileUploader: class { constructor(options) { uploader = options; } } },
      meta: { get_docfield: (doctype, fieldname) => doctype === "Stock Count" ? canonicalDocfield : itemMeta.fields.find((field) => field.fieldname === fieldname) },
      utils: { csv_to_array: JSON.parse, get_decoded_string: (s) => s },
      msgprint() {}, throw(message) { throw Error(message); },
    },
  };
  vm.runInNewContext(numberSource, context);
  vm.runInNewContext(countSource, context);
  if (stockCount) handlers["Stock Count"].setup(frm);
  if (resetDocfield) {
    const initial = grid.df;
    vm.runInNewContext(`({${setupFieldsSource}}).setup_fields`, context).call(grid);
    assert.notEqual(grid.df, initial);
    assert.equal(grid.df, canonicalDocfield);
  }
  vm.runInNewContext(`({${uploadSource}}).setup_allow_bulk_edit`, context).call(grid);
  click();
  assert.equal(uploader.as_dataurl, true);
  const csv = [["Bulk Edit"], [], fieldnames, [], [], [], ["------"], ...rows];
  try {
    uploader.on_success({ dataurl: JSON.stringify(csv) });
  } catch (error) {
    error.rows = JSON.parse(JSON.stringify(frm.doc.items));
    throw error;
  }
  assert.equal(refreshCount, 1);
  return JSON.parse(JSON.stringify(frm.doc.items));
}

test("native grid CSV upload updates count status and difference before refresh", () => {
  const [row] = upload(["item_code", "system_qty", "counted_qty", "is_counted", "difference_qty"],
    [["ITEM-1", "4", "3", "0", "0"]]);
  assert.equal(row.counted_qty, 3);
  assert.equal(row.is_counted, 1);
  assert.equal(row.difference_qty, -1);
});

test("explicit CSV zero is counted while blank or whitespace cells stay uncounted", () => {
  const rows = upload(["item_code", "system_qty", "counted_qty", "is_counted", "difference_qty"], [
    ["ZERO", "4", "0", "0", "0"],
    ["BLANK", "4", "", "0", "99"],
    ["SPACE", "4", "  ", "0", "99"],
  ]);
  assert.deepEqual(rows.map((r) => [r.is_counted, r.difference_qty]), [[1, -4], [0, 0], [0, 0]]);
});

test("round-tripped zero retains its explicit Counted flag", () => {
  const [row] = upload(["item_code", "system_qty", "counted_qty", "is_counted"], [["ZERO", "4", "", "1"]]);
  assert.equal(row.is_counted, 1);
  assert.equal(row.difference_qty, -4);
});

test("missing Counted Qty column does not invent a completed count", () => {
  const [row] = upload(["item_code", "system_qty", "is_counted", "difference_qty"], [["ITEM", "4", "0", "7"]]);
  assert.equal(row.is_counted, 0);
  assert.equal(row.difference_qty, 7);
});

test("count recalculation preserves uploaded item, batch, serial, location and row order", () => {
  const rows = upload(["item_code", "batch_no", "serial_no", "storage_location", "system_qty", "counted_qty"], [
    ["ITEM-A", "BATCH-A", "", "STAGING", "4", "2"],
    ["ITEM-B", "", "SERIAL-1", "SHELF", "1", "1"],
  ]);
  assert.deepEqual(rows.map((r) => [r.idx, r.item_code, r.batch_no, r.serial_no, r.storage_location]),
    [[1, "ITEM-A", "BATCH-A", "", "STAGING"], [2, "ITEM-B", "", "SERIAL-1", "SHELF"]]);
  assert.deepEqual(rows.map((r) => [r.is_counted, r.difference_qty]), [[1, -2], [1, 0]]);
});

test("other grids keep their native CSV import behavior without a row callback", () => {
  const [row] = upload(["item_code", "system_qty", "counted_qty", "is_counted", "difference_qty"],
    [["OTHER", "4", "3", "0", "7"]], false);
  assert.equal(row.counted_qty, 3);
  assert.equal(row.is_counted, 0);
  assert.equal(row.difference_qty, 7);
});

test("invalid CSV quantities cannot become completed zero counts, even with Counted set", () => {
  for (const invalid of ["abc", "3abc", "NaN", "Infinity", "-Infinity", "0x10"]) {
    assert.throws(() => upload(["item_code", "system_qty", "counted_qty", "is_counted"],
      [["INVALID", "4", invalid, "1"]]), (error) => {
      assert.match(error.message, /Counted Qty must be a valid finite number/);
      assert.equal(error.rows[0].is_counted, 0, invalid);
      assert.equal(error.rows[0].counted_qty, null, invalid);
      return true;
    });
  }
});

test("decimal and grouped quantities retain native locale conversion", () => {
  const [standard] = upload(["item_code", "system_qty", "counted_qty"], [["ITEM", "1,000", "1,234.50"]]);
  const [localized] = upload(["item_code", "system_qty", "counted_qty"],
    [["ITEM", "1.000,00", "1.234,50"]], true, "#.###,##");
  for (const row of [standard, localized]) {
    assert.equal(row.counted_qty, 1234.5);
    assert.equal(row.is_counted, 1);
    assert.equal(row.difference_qty, 234.5);
  }
});

test("count upload hook survives native grid setup_fields replacing its DocField", () => {
  const [row] = upload(["item_code", "system_qty", "counted_qty", "is_counted"],
    [["ITEM", "4", "3", "0"]], true, "#,###.##", true);
  assert.equal(row.is_counted, 1);
  assert.equal(row.difference_qty, -1);
});
