import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(
  new URL("../../stock/doctype/stock_count/stock_count.js", import.meta.url),
  "utf8"
);

function setup(rows, scanned, location) {
  const handlers = {};
  const messages = [];
  const queries = {};
  const displays = {};
  const properties = {};
  const frm = {
    doc: {
      items: rows,
      scan_barcode: "barcode",
      scan_storage_location: location,
      warehouse: "Count Warehouse",
      status: "In Progress",
      docstatus: 0,
    },
    set_value(fieldname, value) {
      this.doc[fieldname] = value;
    },
    refresh_field() {},
    set_query(fieldname, ...args) {
      queries[fieldname] = args.at(-1);
    },
    toggle_display(fieldname, visible) {
      displays[fieldname] = visible;
    },
    set_df_property(fieldname, property, value) {
      properties[`${fieldname}.${property}`] = value;
    },
  };
  const context = {
    __: (message) => message,
    flt: (value) => Number(value) || 0,
    frappe: {
      ui: {
        form: {
          on: (doctype, events) => {
            handlers[doctype] = events;
          },
        },
      },
      call: () => Promise.resolve({ message: scanned }),
      show_alert: (message) => messages.push(message),
      msgprint: (message) => messages.push(message),
      model: {
        set_value: (doctype, name, fieldname, value) => {
          rows.find((row) => row.name === name)[fieldname] = value;
        },
      },
    },
  };
  vm.runInNewContext(source, context);
  return {
    frm,
    handlers: handlers["Stock Count"],
    messages,
    queries,
    displays,
    properties,
  };
}

function row(location, values = {}) {
  return {
    doctype: "Stock Count Item",
    name: location,
    item_code: "Count Item",
    storage_location: location,
    counted_qty: 0,
    ...values,
  };
}

async function scan(fixture) {
  fixture.handlers.scan_barcode(fixture.frm);
  await Promise.resolve();
}

test("item barcode increments only the selected location", async () => {
  const rows = [row("STAGING"), row("SHELF")];
  const fixture = setup(rows, { item_code: "Count Item" }, "SHELF");
  await scan(fixture);
  assert.deepEqual(
    rows.map((r) => r.counted_qty),
    [0, 1]
  );
  assert.equal(rows[1].is_counted, 1);
  assert.equal(fixture.frm.doc.scan_barcode, "");
});

test("ambiguous item barcode requires a location without counting any row", async () => {
  const rows = [row("STAGING"), row("SHELF")];
  const fixture = setup(rows, { item_code: "Count Item" });
  await scan(fixture);
  assert.deepEqual(
    rows.map((r) => r.counted_qty),
    [0, 0]
  );
  assert.match(fixture.messages[0], /Select a Scan Storage Location/);
});

test("unique row can be scanned without selecting a location", async () => {
  const rows = [row("STAGING")];
  await scan(setup(rows, { item_code: "Count Item" }));
  assert.equal(rows[0].counted_qty, 1);
});

test("selected location never falls back to another location", async () => {
  const rows = [row("STAGING")];
  const fixture = setup(rows, { item_code: "Count Item" }, "SHELF");
  await scan(fixture);
  assert.equal(rows[0].counted_qty, 0);
  assert.equal(fixture.messages[0].indicator, "orange");
});

test("distinct batches require batch barcode even in a selected location", async () => {
  const rows = [
    row("STAGING", { name: "A", batch_no: "A" }),
    row("STAGING", { name: "B", batch_no: "B" }),
  ];
  const fixture = setup(rows, { item_code: "Count Item" }, "STAGING");
  await scan(fixture);
  assert.deepEqual(
    rows.map((r) => r.counted_qty),
    [0, 0]
  );
  assert.match(fixture.messages[0], /scan the Batch No/);
  await scan(
    setup(rows, { item_code: "Count Item", batch_no: "B" }, "STAGING")
  );
  assert.deepEqual(
    rows.map((r) => r.counted_qty),
    [0, 1]
  );
});

test("batch barcode also requires a location when the batch has multiple locations", async () => {
  const rows = [
    row("STAGING", { batch_no: "A" }),
    row("SHELF", { batch_no: "A" }),
  ];
  const fixture = setup(rows, { item_code: "Count Item", batch_no: "A" });
  await scan(fixture);
  assert.deepEqual(
    rows.map((r) => r.counted_qty),
    [0, 0]
  );
  assert.match(fixture.messages[0], /Multiple count rows/);
});

test("serial scans locate the serial globally and cannot count it twice", async () => {
  const rows = [
    row("STAGING", { serial_no: "S-1" }),
    row("SHELF", { serial_no: "S-2" }),
  ];
  const fixture = setup(
    rows,
    { item_code: "Count Item", serial_no: "S-2" },
    "STAGING"
  );
  await scan(fixture);
  fixture.frm.doc.scan_barcode = "barcode";
  await scan(fixture);
  assert.deepEqual(
    rows.map((r) => r.counted_qty),
    [0, 1]
  );
  assert.match(fixture.messages[0].message, /already counted/);
});

test("duplicate serial rows are ambiguous and do not receive a count", async () => {
  const rows = [
    row("STAGING", { serial_no: "S-1" }),
    row("SHELF", { serial_no: "S-1" }),
  ];
  await scan(setup(rows, { item_code: "Count Item", serial_no: "S-1" }));
  assert.deepEqual(
    rows.map((r) => r.counted_qty),
    [0, 0]
  );
});

test("scan location uses the current warehouse and active locations and is visible only during counting", () => {
  const fixture = setup([], {});
  fixture.handlers.setup(fixture.frm);
  assert.equal(
    fixture.queries.scan_storage_location().filters.warehouse,
    "Count Warehouse"
  );
  assert.equal(fixture.queries.scan_storage_location().filters.disabled, 0);
  fixture.handlers.refresh(fixture.frm);
  assert.equal(fixture.displays.scan_storage_location, true);
  fixture.frm.doc.status = "Completed";
  fixture.handlers.refresh(fixture.frm);
  assert.equal(fixture.displays.scan_storage_location, false);
});

test("requesting approval locks counting until the document returns for recount", () => {
  const fixture = setup([], {});
  for (const state of [
    "Stock Count Pending Stock Approval",
    "Stock Count Pending Accounts Approval",
    "Stock Count Pending Final Approval",
  ]) {
    fixture.frm.doc.workflow_state = state;
    fixture.handlers.refresh(fixture.frm);
    assert.equal(fixture.displays.scan_barcode, false);
    assert.equal(fixture.displays.scan_storage_location, false);
    assert.equal(fixture.properties["items.read_only"], true);
  }
  fixture.frm.doc.workflow_state = "Stock Count Draft";
  fixture.handlers.refresh(fixture.frm);
  assert.equal(fixture.displays.scan_barcode, true);
  assert.equal(fixture.properties["items.read_only"], false);
});

test("native layout keeps counting controls outside Monthly Count Setup", () => {
  const layout = JSON.parse(
    fs.readFileSync(
      new URL("../../stock/doctype/stock_count/stock_count.json", import.meta.url),
      "utf8"
    )
  );
  const fields = Object.fromEntries(layout.fields.map((field) => [field.fieldname, field]));
  const ancestorSections = {};
  let section;
  for (const fieldname of layout.field_order) {
    const field = fields[fieldname];
    if (field.fieldtype === "Section Break") section = field;
    ancestorSections[fieldname] = section ? [section] : [];
  }
  const visible = (fieldname, doc) =>
    [...ancestorSections[fieldname], fields[fieldname]].every(
      (field) =>
        !field.hidden &&
        (!field.depends_on ||
          vm.runInNewContext(field.depends_on.replace(/^eval:/, ""), { doc }))
    );

  for (const count_type of ["Monthly", "Annual"]) {
    for (const status of ["Draft", "In Progress", "Completed"]) {
      for (const docstatus of [0, 1, 2]) {
        const doc = { count_type, status, docstatus };
        for (const fieldname of ["scan_storage_location", "scan_barcode"]) {
          assert.equal(
            visible(fieldname, doc),
            status === "In Progress" && docstatus === 0,
            `${fieldname}: ${JSON.stringify(doc)}`
          );
        }
        for (const fieldname of ["snapshot_datetime", "status"]) {
          assert.equal(visible(fieldname, doc), true, `${fieldname}: ${JSON.stringify(doc)}`);
        }
        for (const fieldname of ["item_count", "quarantine_warehouse", "quarantine_storage_location"]) {
          assert.equal(
            visible(fieldname, doc),
            count_type === "Monthly",
            `${fieldname}: ${JSON.stringify(doc)}`
          );
        }
      }
    }
  }
  assert.equal(ancestorSections.scan_storage_location[0].label, "Scan Items");
  assert.equal(ancestorSections.scan_barcode[0].label, "Scan Items");
  assert.equal(ancestorSections.snapshot_datetime[0].label, "Count Status");
  assert.equal(ancestorSections.status[0].label, "Count Status");
});
