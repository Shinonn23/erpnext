import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function fixture(doctype, doc = {}) {
  const handlers = {};
  const queries = {};
  const displays = {};
  const context = {
    erpnext: { queries: {} },
    locals: { Row: { row: { warehouse: "Warehouse", item_code: "Item" } } },
    frappe: {
      provide() {},
      boot: { sysdefaults: { enable_putaway_rule: 0 } },
      ui: { form: { on: (name, events) => (handlers[name] = events) } },
      get_meta: () => ({
        fields: [
          {
            fieldname: "storage_location",
            fieldtype: "Link",
            options: "Storage Location",
          },
        ],
      }),
    },
  };
  vm.runInNewContext(
    fs.readFileSync(
      new URL("../../public/js/storage_location.js", import.meta.url),
      "utf8"
    ),
    context
  );
  const frm = {
    doc,
    meta: {
      fields: [
        { fieldname: "items", fieldtype: "Table", options: "Row" },
        { fieldname: "apply_putaway_rule", fieldtype: "Check" },
      ],
    },
    set_query(field, ...args) {
      queries[[field, args.length > 1 ? args[0] : ""].join(":")] = args.at(-1);
    },
    toggle_display(field, show) {
      displays[field] = show;
    },
    set_value(field, value) {
      this.doc[field] = value;
    },
  };
  handlers[doctype].setup(frm);
  return { context, handlers, frm, queries, displays };
}

test("transaction picker sends current row Item and warehouse", () => {
  const f = fixture("Purchase Receipt");
  const result = f.queries["storage_location:items"]({}, "Row", "row");
  assert.match(result.query, /storage_location_query$/);
  assert.equal(result.filters.item_code, "Item");
  assert.equal(result.filters.warehouse, "Warehouse");
  f.context.locals.Row.row.item_code = "Changed Item";
  assert.equal(
    f.queries["storage_location:items"]({}, "Row", "row").filters.item_code,
    "Changed Item"
  );
});

test("Stock Entry source and target use their own warehouse and the same Item", () => {
  const f = fixture("Stock Entry");
  f.context.locals.Row.row.s_warehouse = "Source";
  f.context.locals.Row.row.t_warehouse = "Target";
  f.frm.meta.fields[0].options = "Row";
  f.context.frappe.get_meta = () => ({
    fields: ["storage_location", "to_storage_location"].map((fieldname) => ({
      fieldname,
      fieldtype: "Link",
      options: "Storage Location",
    })),
  });
  f.handlers["Stock Entry"].setup(f.frm);
  assert.equal(
    f.queries["storage_location:items"]({}, "Row", "row").filters.warehouse,
    "Source"
  );
  assert.equal(
    f.queries["to_storage_location:items"]({}, "Row", "row").filters.warehouse,
    "Target"
  );
  assert.equal(
    f.queries["to_storage_location:items"]({}, "Row", "row").filters.item_code,
    "Item"
  );
});

test("Item master previews unsaved Storage Condition without overriding transaction requirements", () => {
  const f = fixture("Item", { storage_condition: "Cold" });
  const result = f.queries["storage_location:items"](f.frm.doc, "Row", "row");
  assert.equal(result.filters.storage_condition, "Cold");
  assert.equal(result.filters.item_code, "");
});

test("default warehouse without Item context remains a scoped location search", () => {
  const f = fixture("BOM");
  const result = f.context.erpnext.queries.storage_location("Warehouse");
  assert.equal(result.filters.warehouse, "Warehouse");
  assert.equal(result.filters.item_code, "");
  assert.equal(
    f.context.erpnext.queries.storage_location(null, "Item").filters.warehouse,
    ""
  );
});

test("supplied raw materials use supplier warehouse rather than the finished-goods warehouse", () => {
  const f = fixture("Subcontracting Receipt", {
    supplier_warehouse: "Supplier",
  });
  f.context.locals.Row.row = {
    rm_item_code: "Raw Material",
    parentfield: "supplied_items",
    warehouse: "Finished Goods",
  };
  const result = f.queries["storage_location:items"](f.frm.doc, "Row", "row");
  assert.equal(result.filters.warehouse, "Supplier");
  assert.equal(result.filters.item_code, "Raw Material");
});

test("Putaway control is hidden only when explicitly disabled in boot settings", () => {
  const f = fixture("Purchase Receipt");
  f.handlers["Purchase Receipt"].refresh(f.frm);
  assert.equal(f.displays.apply_putaway_rule, false);
  delete f.context.frappe.boot.sysdefaults.enable_putaway_rule;
  f.handlers["Purchase Receipt"].refresh(f.frm);
  assert.equal(f.displays.apply_putaway_rule, true);
});

test("disabling Putaway clears only draft flags, retaining submitted history", () => {
  const f = fixture("Stock Entry", { docstatus: 0, apply_putaway_rule: 1 });
  f.handlers["Stock Entry"].refresh(f.frm);
  assert.equal(f.frm.doc.apply_putaway_rule, 0);
  f.frm.doc.docstatus = 1;
  f.frm.doc.apply_putaway_rule = 1;
  f.handlers["Stock Entry"].refresh(f.frm);
  assert.equal(f.frm.doc.apply_putaway_rule, 1);
});

test("manufacturing output uses semi-finished Item, while raw-input defaults do not use output condition", () => {
  const f = fixture("Job Card", {
    production_item: "Final Ambient",
    finished_good: "Semi Finished Cold",
    source_warehouse: "Raw",
    target_warehouse: "FG",
  });
  f.frm.meta.fields = [
    "source_storage_location",
    "target_storage_location",
  ].map((fieldname) => ({
    fieldname,
    fieldtype: "Link",
    options: "Storage Location",
  }));
  f.handlers["Job Card"].setup(f.frm);
  assert.equal(
    f.queries["source_storage_location:"](f.frm.doc).filters.item_code,
    ""
  );
  assert.equal(
    f.queries["target_storage_location:"](f.frm.doc).filters.item_code,
    "Semi Finished Cold"
  );
  f.context.locals.Row.row = {
    finished_good: "Operation Cold",
    fg_warehouse: "FG",
  };
  f.context.frappe.get_meta = () => ({
    fields: [
      {
        fieldname: "fg_storage_location",
        fieldtype: "Link",
        options: "Storage Location",
      },
    ],
  });
  f.frm.meta.fields = [
    { fieldname: "operations", fieldtype: "Table", options: "Row" },
  ];
  f.handlers["Job Card"].setup(f.frm);
  assert.equal(
    f.queries["fg_storage_location:operations"]({}, "Row", "row").filters
      .item_code,
    "Operation Cold"
  );
});

function link_control(locals) {
  class ControlLink {
    set_custom_query(args) {
      args.filters = { company: "Company" };
    }
    get_filter_description(filters) {
      return filters;
    }
  }
  vm.runInNewContext(
    fs.readFileSync(
      new URL("../../public/js/storage_location.js", import.meta.url),
      "utf8"
    ),
    {
      erpnext: { queries: {} },
      locals,
      frappe: {
        provide() {},
        boot: { sysdefaults: {} },
        ui: { form: { on() {}, ControlLink } },
      },
    }
  );
  return ControlLink;
}

test("Item row Warehouse search goes through the Storage Condition query", () => {
  const ControlLink = link_control({
    Row: { row: { parenttype: "Stock Entry", item_code: "Cold Item" } },
  });
  const control = Object.assign(new ControlLink(), { doctype: "Row", docname: "row" });
  const args = { doctype: "Warehouse" };
  control.set_custom_query(args);
  assert.match(args.query, /storage_location\.warehouse_query$/);
  assert.equal(args.filters.storage_condition_item, "Cold Item");
  assert.deepEqual(JSON.parse(JSON.stringify(args.filters.filters)), { company: "Company" });
  assert.deepEqual(
    JSON.parse(JSON.stringify(control.get_filter_description(args.filters))),
    { company: "Company" }
  );
});

test("Warehouse search keeps native queries outside Item rows and custom form queries", () => {
  const ControlLink = link_control({
    Header: { doc: { item_code: "Cold Item" } },
    Row: { row: { parenttype: "Stock Entry" } },
    ItemRow: { row: { parenttype: "Stock Entry", item_code: "Cold Item" } },
  });
  for (const [doctype, docname] of [
    ["Header", "doc"],
    ["Row", "row"],
  ]) {
    const control = Object.assign(new ControlLink(), { doctype, docname });
    const args = { doctype: "Warehouse" };
    control.set_custom_query(args);
    assert.equal(args.query, undefined);
  }
  const row = Object.assign(new ControlLink(), { doctype: "ItemRow", docname: "row" });
  const custom = { doctype: "Warehouse", query: "custom.query" };
  row.set_custom_query(custom);
  assert.equal(custom.query, "custom.query");
  const other = { doctype: "Item" };
  row.set_custom_query(other);
  assert.equal(other.query, undefined);
});
