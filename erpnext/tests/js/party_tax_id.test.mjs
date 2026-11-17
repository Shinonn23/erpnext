import assert from "node:assert/strict";
import fs from "node:fs";
import test from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(
  new URL("../../public/js/thailand/party_tax_id.js", import.meta.url),
  "utf8"
);

function load({ canCreate = true } = {}) {
  const handlers = {};
  const calls = [];
  const alerts = [];
  const confirms = [];
  const routes = [];
  const newDocs = [];
  const dialogs = [];
  class FakeDialog {
    constructor(opts) {
      this.opts = opts;
      this.values = {};
      this.fields_dict = {};
      for (const df of opts.fields) {
        if (!df.fieldname) continue;
        this.values[df.fieldname] = df.default;
        const html = { value: "" };
        this.fields_dict[df.fieldname] = {
          df,
          $wrapper: { html: (v) => (html.value = v), get: () => html.value },
        };
      }
      this.custom = {};
      this.shown = false;
      dialogs.push(this);
    }
    get_value(f) {
      return this.values[f];
    }
    set_primary_action(label, fn) {
      this.primary = { label, fn };
    }
    add_custom_action(label, fn) {
      this.custom[label] = fn;
    }
    show() {
      this.shown = true;
    }
    hide() {
      this.shown = false;
    }
  }
  const context = vm.createContext({
    __: (text, args) =>
      args ? text.replace(/\{(\d)\}/g, (_m, i) => args[i]) : text,
    erpnext: {},
    frappe: {
      ui: {
        Dialog: FakeDialog,
        form: {
          on: (doctype, events) => {
            handlers[doctype] = events;
          },
        },
      },
      model: {
        can_create: () => canCreate,
        get_new_doc: (doctype) => {
          const doc = { doctype, name: `new-${doctype.toLowerCase()}-1`, __islocal: 1 };
          newDocs.push(doc);
          return doc;
        },
      },
      utils: { escape_html: (v) => v.replace(/</g, "&lt;") },
      call: (options) => {
        calls.push(options);
      },
      show_alert: (alert) => {
        alerts.push(alert);
      },
      confirm: (message, yes) => {
        confirms.push({ message, yes });
      },
      set_route: (...args) => {
        routes.push(args);
      },
    },
  });
  vm.runInContext(source, context);
  return { handlers, calls, alerts, confirms, routes, newDocs, dialogs, thailand: context.erpnext.thailand };
}

function form(doctype, { isNew = true, doc = {}, fields = ["branch_code"] } = {}) {
  const values = {};
  const buttons = {};
  const frm = {
    doctype,
    doc: { ...doc },
    fields_dict: Object.fromEntries(fields.map((f) => [f, {}])),
    is_new: () => isNew,
    add_custom_button: (label, cb) => {
      buttons[label] = cb;
    },
    set_value: (field, value) => {
      values[field] = value;
      frm.doc[field] = value;
    },
  };
  return { frm, values, buttons };
}

const RD = {
  found: true,
  tax_id: "0105551058581",
  branch_code: "00000",
  existing: [],
  legal_name: "บริษัท เอ จำกัด",
  address: {
    address_line1: "1/57 Soi วัชรพล 2/7",
    address_line2: "แขวงท่าแร้ง",
    city: "เขตบางเขน",
    state: "กรุงเทพมหานคร",
    pincode: "10220",
  },
};

test("form button only on a new party for a user who may create it, and it opens the preview", () => {
  for (const doctype of ["Customer", "Supplier"]) {
    const env = load();
    const fresh = form(doctype, { doc: { tax_id: "0105551058581", branch_code: "00002" } });
    env.handlers[doctype].refresh(fresh.frm);
    fresh.buttons["Fetch from Revenue Department"]();
    assert.equal(env.dialogs.length, 1);
    assert.equal(env.dialogs[0].values.branch_code, "00002");
    assert.equal(env.calls[0].method, "erpnext.regional.thailand.utils.get_party_by_tax_id");
    assert.deepEqual({ ...env.calls[0].args }, { doctype, tax_id: "0105551058581", branch: "00002" });
    const saved = form(doctype, { isNew: false });
    env.handlers[doctype].refresh(saved.frm);
    assert.deepEqual(Object.keys(saved.buttons), []);
    const denied = load({ canCreate: false });
    const other = form(doctype);
    denied.handlers[doctype].refresh(other.frm);
    assert.deepEqual(Object.keys(other.buttons), []);
    assert.equal(denied.handlers[doctype].tax_id, undefined, "the form no longer looks RD up by itself");
  }
});

test("list button for a user who may create; the preview starts at branch 00000 and needs 13 digits", () => {
  const env = load();
  const inner = {};
  env.thailand.add_list_button({ doctype: "Customer", page: { add_inner_button: (l, cb) => (inner[l] = cb) } });
  inner["New from Tax ID"]();
  const d = env.dialogs[0];
  assert.equal(d.values.branch_code, "00000");
  assert.equal(d.primary.label, "Fetch");
  d.values.tax_id = "01055";
  d.primary.fn();
  assert.equal(env.calls.length, 0);
  assert.equal(env.alerts.at(-1).indicator, "orange");
  const denied = load({ canCreate: false });
  const none = {};
  denied.thailand.add_list_button({ doctype: "Supplier", page: { add_inner_button: (l, cb) => (none[l] = cb) } });
  assert.deepEqual(Object.keys(none), []);
});

test("found: preview shows the RD record; Save asks the server with only tax id and branch", () => {
  const env = load();
  const d = env.thailand.open_tax_id_preview("Supplier");
  d.values.tax_id = "0105-551058581";
  d.primary.fn();
  env.calls[0].callback({ message: RD });
  const html = d.fields_dict.preview.$wrapper.get();
  for (const text of ["บริษัท เอ จำกัด", "0105551058581", "00000", "แขวงท่าแร้ง", "เขตบางเขน", "กรุงเทพมหานคร", "10220"]) {
    assert.match(html, new RegExp(text));
  }
  assert.equal(d.primary.label, "Save");
  assert.ok(d.custom["Edit Before Saving"]);
  d.primary.fn();
  const save = env.calls[1];
  assert.equal(save.method, "erpnext.regional.thailand.utils.create_party_from_tax_id");
  assert.deepEqual({ ...save.args }, { doctype: "Supplier", tax_id: "0105551058581", branch: "00000" });
  save.callback({ message: { name: "SUP-0009" } });
  assert.deepEqual(env.routes.at(-1), ["Form", "Supplier", "SUP-0009"]);
  assert.equal(d.shown, false);
});

test("Save with a missing default falls back to the prefilled form", () => {
  const env = load();
  const d = env.thailand.open_tax_id_preview("Customer", { tax_id: "0105551058581" });
  env.calls[0].callback({ message: RD });
  d.primary.fn();
  env.calls[1].callback({ message: { missing: ["Customer Group"] } });
  assert.equal(env.alerts.at(-1).indicator, "orange");
  const doc = env.newDocs[0];
  assert.equal(doc.customer_name, "บริษัท เอ จำกัด");
  assert.equal(doc.rd_address_lookup, "0105551058581:00000");
  assert.equal(doc.address_line1, undefined, "the browser never carries the address");
});

test("Edit Before Saving opens an unsaved form with only name, type, tax id, branch and the lookup key", () => {
  const env = load();
  const d = env.thailand.open_tax_id_preview("Supplier", { tax_id: "0105551058581" });
  env.calls[0].callback({ message: RD });
  d.custom["Edit Before Saving"]();
  const doc = env.newDocs[0];
  assert.deepEqual(
    Object.keys(doc).filter((k) => !["doctype", "name", "__islocal"].includes(k)).sort(),
    ["branch_code", "rd_address_lookup", "supplier_name", "supplier_type", "tax_id"],
  );
  assert.equal(doc.supplier_type, "Company");
  assert.deepEqual(env.routes.at(-1), ["Form", "Supplier", doc.name]);
  // from the form: the values go into that form instead of a new one
  const env2 = load();
  const { frm, values } = form("Supplier", { doc: { tax_id: "0105551058581" } });
  env2.handlers.Supplier.refresh(frm);
  const buttons = {};
  frm.add_custom_button = (l, cb) => (buttons[l] = cb);
  env2.handlers.Supplier.refresh(frm);
  buttons["Fetch from Revenue Department"]();
  env2.calls[0].callback({ message: RD });
  env2.dialogs.at(-1).custom["Edit Before Saving"]();
  assert.equal(frm.doc.supplier_name, "บริษัท เอ จำกัด");
  assert.equal(values.branch_code, "00000");
  assert.equal(frm.doc.rd_address_lookup, "0105551058581:00000");
  assert.equal(env2.newDocs.length, 0);
});

test("existing party gives Open Existing; not found gives Enter Manually with only the tax id", () => {
  const env = load();
  const d = env.thailand.open_tax_id_preview("Customer", { tax_id: "0105551058581" });
  env.calls[0].callback({
    message: { ...RD, found: false, existing: [{ name: "CUS-0001", title: "Old Co", branch_code: "00001" }] },
  });
  assert.equal(d.primary.label, "Open Existing");
  assert.match(d.fields_dict.preview.$wrapper.get(), /Old Co/);
  d.primary.fn();
  assert.deepEqual(env.routes.at(-1), ["Form", "Customer", "CUS-0001"]);

  const s = env.thailand.open_tax_id_preview("Supplier", { tax_id: "0105551058581", branch_code: "1" });
  env.calls[1].callback({
    message: { ...RD, branch_code: "00001", existing: [{ name: "S-1", title: "A", same_branch: false }] },
  });
  assert.equal(s.primary.label, "Save", "a Supplier at another branch is a hint only");

  const n = env.thailand.open_tax_id_preview("Customer", { tax_id: "1234567890123" });
  env.calls[2].callback({ message: { found: false, tax_id: "1234567890123", branch_code: "00000", existing: [] } });
  assert.equal(n.primary.label, "Enter Manually");
  n.primary.fn();
  const doc = env.newDocs.at(-1);
  assert.equal(doc.tax_id, "1234567890123");
  assert.equal(doc.rd_address_lookup, undefined);
  assert.equal(doc.customer_name, undefined);
});

test("quick entry: a complete Tax ID asks RD once; retyping looks the new pair up", () => {
  const env = load();
  const dialog = { doc: { tax_id: "0105551058581", branch_code: "00000" }, fields_dict: {}, set_value() {} };
  const target = env.thailand.dialog_target("Supplier", dialog);
  env.thailand.on_rd_key_change(target);
  env.thailand.on_rd_key_change(target);
  assert.equal(env.calls.length, 1);
  dialog.doc.branch_code = "00001";
  env.thailand.on_rd_key_change(target);
  assert.equal(env.calls[1].args.branch, "00001");
});

test("quick entry dialog: RD address fills its Primary Address fields, City falls back", () => {
  const env = load();
  const values = {};
  const fields = ["address_line1", "address_line2", "city", "state", "pincode", "country_address"];
  const target = {
    doctype: "Customer",
    doc: {},
    state: {},
    set_value: (f, v) => (values[f] = v),
    has_field: (f) => fields.includes(f) || f === "branch_code",
  };
  const rd = {
    found: true,
    tax_id: "0105551058581",
    branch_code: "00000",
    existing: [],
    legal_name: "A",
    address: { address_line1: "9", city: "อำเภอเมือง", state: "ชลบุรี", pincode: "20000" },
  };
  env.thailand.apply_rd_result(target, rd);
  assert.equal(values.city, "อำเภอเมือง");
  assert.equal(values.address_line2, "");
  assert.equal(values.country_address, "Thailand");
  assert.equal(values.address_line1, "9");
});
