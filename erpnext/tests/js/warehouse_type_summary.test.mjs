import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function fixture(doc, summary, mode = "sync") {
  const handlers = {};
  const calls = [];
  const intros = [];
  const descriptions = {};
  const context = {
    __: (text, args) =>
      args ? text.replace(/\{(\d+)\}/g, (_, i) => args[i]) : text,
    frappe: {
      ui: { form: { on: (name, events) => (handlers[name] = events) } },
      utils: { escape_html: (text) => String(text).replace(/</g, "&lt;") },
      get_meta: () => ({
        fields: [{ fieldname: "warehouse_type", description: "Static hint" }],
      }),
      call: (opts) => {
        calls.push(opts);
        if (mode === "sync") opts.callback({ message: summary });
        if (mode === "error") opts.error();
      },
      contacts: { clear_address_and_contact() {} },
      boot: { allowed_reports: {} },
    },
  };
  vm.runInNewContext(
    fs.readFileSync(
      new URL("../../stock/doctype/warehouse/warehouse.js", import.meta.url),
      "utf8"
    ),
    context
  );
  const frm = {
    doc,
    is_new: () => false,
    has_perm: () => false,
    toggle_display() {},
    toggle_enable() {},
    add_custom_button() {},
    set_intro: (text, color) => intros.push([text, color]),
    set_df_property: (field, prop, value) =>
      (descriptions[field + ":" + prop] = value),
  };
  return { handlers, frm, calls, intros, descriptions };
}

const ops = (allowed) =>
  ["receipt", "issue", "transfer", "sales", "loan", "reconciliation"].map(
    (operation) => ({
      operation,
      allowed: allowed.includes(operation),
      documents: ["Doc " + operation],
    })
  );

test("no Warehouse Type shows no restriction without a server call", () => {
  const f = fixture({ name: "WH", warehouse_type: null }, null);
  f.handlers.Warehouse.warehouse_type(f.frm);
  assert.equal(f.calls.length, 0);
  assert.match(f.intros.at(-1)[0], /No Warehouse Type: no restriction/);
  assert.equal(f.descriptions["warehouse_type:description"], "Static hint");
});

test("summary lists allowed and blocked operations and the outgoing role", () => {
  const f = fixture(
    { name: "WH", warehouse_type: "Lab" },
    {
      restricted: true,
      operations: ops(["receipt", "issue"]),
      outgoing_role: "Stock <Manager>",
    }
  );
  f.handlers.Warehouse.warehouse_type(f.frm);
  assert.equal(f.calls[0].args.warehouse_type, "Lab");
  const line = f.descriptions["warehouse_type:description"];
  assert.match(line, /Allowed: Receipt, Issue/);
  assert.match(line, /Not allowed: Transfer, Sales, Loan, Reconciliation/);
  assert.match(line, /Stock &lt;Manager>/);
  assert.match(f.intros.at(-1)[0], /Doc receipt/);
});

test("group warehouse shows no type summary", () => {
  const f = fixture({ name: "WH", is_group: 1, warehouse_type: "Lab" }, null);
  f.handlers.Warehouse.warehouse_type(f.frm);
  assert.equal(f.calls.length, 0);
  assert.equal(f.intros.length, 0);
});

test("intro is only cleared when this code set it", () => {
  const f = fixture({ name: "WH", warehouse_type: null }, null);
  f.handlers.Warehouse.warehouse_type(f.frm);
  assert.equal(f.intros.filter(([text]) => text === "").length, 0);
  f.frm.doc.warehouse_type = "Lab";
  const summary = { restricted: true, operations: ops(["receipt"]) };
  const g = fixture({ name: "WH", warehouse_type: "Lab" }, summary);
  g.handlers.Warehouse.warehouse_type(g.frm);
  g.frm.doc.warehouse_type = null;
  g.handlers.Warehouse.warehouse_type(g.frm);
  assert.equal(g.intros.filter(([text]) => text === "").length, 1);
});

test("a stale response is ignored", () => {
  const summary = { restricted: true, operations: ops(["receipt"]) };
  const f = fixture({ name: "WH", warehouse_type: "Lab" }, summary, "defer");
  f.handlers.Warehouse.warehouse_type(f.frm);
  f.frm.doc.warehouse_type = "Other";
  f.calls[0].callback({ message: summary });
  assert.equal(f.intros.length, 0);
  assert.equal(f.descriptions["warehouse_type:description"], undefined);
});

test("Transit states that loan counts as transfer", () => {
  const f = fixture(
    { name: "WH", warehouse_type: "Transit" },
    {
      restricted: true,
      operations: ops(["transfer"]),
      loan_counts_as_transfer: true,
    }
  );
  f.handlers.Warehouse.warehouse_type(f.frm);
  assert.match(
    f.descriptions["warehouse_type:description"],
    /Loan documents count as Transfer/
  );
});

test("a failed call keeps the previous state silently", () => {
  const f = fixture({ name: "WH", warehouse_type: "Lab" }, null, "error");
  f.handlers.Warehouse.warehouse_type(f.frm);
  assert.equal(f.intros.length, 0);
  assert.equal(f.descriptions["warehouse_type:description"], undefined);
});
