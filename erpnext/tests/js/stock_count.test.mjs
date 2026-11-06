import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(
  new URL("../../stock/doctype/stock_count/stock_count.js", import.meta.url),
  "utf8"
);

function setup(rows, scanned, location, resolved) {
  const handlers = {};
  const messages = [];
  const queries = {};
  const displays = {};
  const properties = {};
  const frm = {
    fields_dict: { items: { grid: { df: {} } } },
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
    trigger(event) {
      return (
        handlers["Stock Count"][event] && handlers["Stock Count"][event](this)
      );
    },
    add_custom_button(label) {
      (this.buttons = this.buttons || []).push(label);
    },
    is_new: () => false,
    is_dirty: () => false,
    dirty() {},
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
    __: (message, args = []) =>
      message.replace(/\{(\d+)\}/g, (_, index) => args[index]),
    erpnext: { stock_count: {}, queries: {} },
    flt: (value) => Number(value) || 0,
    frappe: {
      ui: {
        form: {
          on: (doctype, events) => {
            handlers[doctype] = events;
          },
        },
      },
      provide: () => {},
      session: { user: "first@example.com" },
      db: {
        get_value: async () => ({
          message: { counted_by: "first@example.com" },
        }),
      },
      call: (args) =>
        Promise.resolve({
          message:
            args &&
            args.method &&
            args.method.endsWith("resolve_storage_location")
              ? resolved
              : scanned,
        }),
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

test("scan location is visible only during counting", () => {
  const fixture = setup([], {});
  fixture.handlers.setup(fixture.frm);
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

test("preparing a count opens the counting tab only after the snapshot reloads", async () => {
  const fixture = setup([], {});
  fixture.frm.doc.status = "Draft";
  const events = [];
  let prepare;
  Object.assign(fixture.frm, {
    is_new: () => false,
    is_dirty: () => false,
    add_custom_button: (label, callback) => {
      prepare = callback;
    },
    call: async (method) => {
      events.push(method);
    },
    reload_doc: async () => {
      events.push("reload");
    },
    scroll_to_field: (field) => {
      events.push(field);
    },
  });
  fixture.handlers.refresh(fixture.frm);
  await prepare();
  assert.deepEqual(events, ["prepare_count", "reload", "items"]);
});

test("native layout keeps counting controls outside Monthly Count Setup", () => {
  const layout = JSON.parse(
    fs.readFileSync(
      new URL(
        "../../stock/doctype/stock_count/stock_count.json",
        import.meta.url
      ),
      "utf8"
    )
  );
  const fields = Object.fromEntries(
    layout.fields.map((field) => [field.fieldname, field])
  );
  const ancestorSections = {};
  const ancestorTabs = {};
  let section;
  let tab;
  for (const fieldname of layout.field_order) {
    const field = fields[fieldname];
    if (field.fieldtype === "Tab Break") {
      tab = field;
      section = undefined;
    }
    if (field.fieldtype === "Section Break") section = field;
    ancestorSections[fieldname] = section ? [section] : [];
    ancestorTabs[fieldname] = tab;
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
          assert.equal(
            visible(fieldname, doc),
            true,
            `${fieldname}: ${JSON.stringify(doc)}`
          );
        }
        for (const fieldname of [
          "item_count",
          "quarantine_warehouse",
          "quarantine_storage_location",
        ]) {
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
  assert.deepEqual(
    layout.fields
      .filter((field) => field.fieldtype === "Tab Break")
      .map((field) => field.label),
    ["Count Setup", "Counting", "Review"]
  );
  for (const fieldname of [
    "company",
    "warehouse",
    "item_count",
    "dimensions",
  ]) {
    assert.equal(ancestorTabs[fieldname].label, "Count Setup");
  }
  for (const fieldname of ["scan_storage_location", "scan_barcode", "items"]) {
    assert.equal(ancestorTabs[fieldname].label, "Counting");
  }
  for (const fieldname of [
    "status",
    "snapshot_datetime",
    "stock_entry",
    "stock_reconciliation",
  ]) {
    assert.equal(ancestorTabs[fieldname].label, "Review");
  }
});

test("a scanned location label is resolved to the location name", async () => {
  const fixture = setup([], {}, "C001-01-01", "Count Warehouse / C001-01-01");
  fixture.handlers.scan_storage_location(fixture.frm);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(
    fixture.frm.doc.scan_storage_location,
    "Count Warehouse / C001-01-01"
  );
});

test("Set uncounted rows to 0 is an explicit button that completes only the uncounted rows", () => {
  const rows = [
    row("A", { system_qty: 3 }),
    row("B", { system_qty: 2, counted_qty: 5, is_counted: 1 }),
  ];
  const fixture = setup(rows, {});
  const buttons = {};
  fixture.frm.add_custom_button = (label, callback) => {
    buttons[label] = callback;
  };
  fixture.handlers.refresh(fixture.frm);
  assert.ok(buttons["Set uncounted rows to 0"]);
  assert.deepEqual(
    rows.map((r) => r.is_counted),
    [undefined, 1]
  );
  buttons["Set uncounted rows to 0"]();
  assert.deepEqual(
    rows.map((r) => r.counted_qty),
    [0, 5]
  );
  assert.deepEqual(
    rows.map((r) => r.is_counted),
    [1, 1]
  );
  assert.equal(rows[0].difference_qty, -3);
});

test("a failed scan names the scanned code and the location", async () => {
  const fixture = setup([row("SHELF")], null, "SHELF");
  await scan(fixture);
  await Promise.resolve();
  assert.match(
    fixture.messages[0].message,
    /Code barcode was not found at SHELF/
  );
  const none = setup([row("SHELF")], null, "");
  await scan(none);
  await Promise.resolve();
  assert.match(none.messages[0].message, /Code barcode was not found\./);
});

test("the Lost and Found Warehouses are read-only: the system fills them", () => {
  const fixture = setup([], null, "");
  fixture.handlers.refresh(fixture.frm);
  assert.equal(fixture.properties["quarantine_warehouse.read_only"], 1);
  assert.equal(fixture.properties["found_warehouse.read_only"], 1);
});

test("the variance summary counts rows and units short and over, never money", () => {
  const ctx = {
    __: (m) => m,
    flt: (v) => Number(v) || 0,
    erpnext: { stock_count: {} },
    frappe: { provide() {}, ui: { form: { on() {} } } },
  };
  vm.runInNewContext(source, ctx);
  const v = ctx.erpnext.stock_count.variance_summary([
    { is_counted: 1, counted_qty: 3, system_qty: 5 },
    { is_counted: 1, counted_qty: 9, system_qty: 5 },
    { is_counted: 1, counted_qty: 2, system_qty: 2 },
    { is_counted: 0, counted_qty: 0, system_qty: 4 },
  ]);
  assert.deepEqual(JSON.parse(JSON.stringify(v)), {
    short: 1,
    over: 1,
    short_qty: 2,
    over_qty: 4,
  });
});

test("the first counter of a recount cannot count it", async () => {
  const fixture = setup([row("SHELF")], null, "");
  const intros = [];
  fixture.frm.doc.recount_of = "SC-1";
  fixture.frm.set_intro = (text) => intros.push(text);
  fixture.frm.remove_custom_button = () => {};
  fixture.handlers.refresh(fixture.frm);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.equal(fixture.properties["items.read_only"], 1);
  assert.match(intros.at(-1), /another person must do this recount/);
});
