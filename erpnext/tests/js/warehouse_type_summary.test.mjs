import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

function fixture(doc, summary) {
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
      call: (opts) => {
        calls.push(opts);
        opts.callback({ message: summary });
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
  assert.match(f.descriptions["warehouse_type:description"], /no restriction/);
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
  assert.equal(f.descriptions["warehouse_type:description"], undefined);
});
