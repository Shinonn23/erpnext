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
  const context = vm.createContext({
    __: (text, args) =>
      args ? text.replace(/\{(\d)\}/g, (_m, i) => args[i]) : text,
    erpnext: {},
    frappe: {
      ui: {
        form: {
          on: (doctype, events) => {
            handlers[doctype] = events;
          },
        },
      },
      model: { can_create: () => canCreate },
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
  return { handlers, calls, alerts, confirms, routes, thailand: context.erpnext.thailand };
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

test("button only on a new party for a user who may create it", () => {
  for (const doctype of ["Customer", "Supplier"]) {
    const env = load();
    const fresh = form(doctype);
    env.handlers[doctype].refresh(fresh.frm);
    assert.ok(fresh.buttons["Fetch from Revenue Department"]);
    const saved = form(doctype, { isNew: false });
    env.handlers[doctype].refresh(saved.frm);
    assert.deepEqual(Object.keys(saved.buttons), []);
    const denied = load({ canCreate: false });
    const other = form(doctype);
    denied.handlers[doctype].refresh(other.frm);
    assert.deepEqual(Object.keys(other.buttons), []);
  }
});

test("complete Tax ID asks RD once with only tax id and branch, and fills the party", () => {
  const env = load();
  const { frm, values } = form("Supplier", {
    doc: { tax_id: "0105551058581", branch_code: "00000" },
    fields: ["branch_code", "address_line1", "address_line2", "city", "state", "pincode", "country"],
  });
  env.handlers.Supplier.tax_id(frm);
  env.handlers.Supplier.tax_id(frm);
  assert.equal(env.calls.length, 1);
  const call = env.calls[0];
  assert.equal(
    call.method,
    "erpnext.regional.thailand.utils.get_party_by_tax_id"
  );
  assert.deepEqual(
    { ...call.args },
    { doctype: "Supplier", tax_id: "0105551058581", branch: "00000" }
  );
  call.callback({
    message: {
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
    },
  });
  assert.equal(values.supplier_name, "บริษัท เอ จำกัด");
  assert.equal(values.supplier_type, "Company");
  assert.equal(values.branch_code, "00000");
  assert.equal(values.address_line1, "1/57 Soi วัชรพล 2/7");
  assert.equal(values.address_line2, "แขวงท่าแร้ง");
  assert.equal(values.city, "เขตบางเขน");
  assert.equal(values.county, undefined);
  assert.equal(values.state, "กรุงเทพมหานคร");
  assert.equal(values.pincode, "10220");
  assert.equal(values.country, "Thailand");
  assert.ok(!("rd_fill_address" in frm.doc));
});

test("short Tax ID does not call; existing party is offered; not found only warns", () => {
  const env = load();
  const short = form("Customer", { doc: { tax_id: "01055" } });
  env.handlers.Customer.tax_id(short.frm);
  assert.equal(env.calls.length, 0);

  const dup = form("Customer", { doc: { tax_id: "0105551058581" } });
  env.handlers.Customer.tax_id(dup.frm);
  env.calls[0].callback({
    message: {
      found: false,
      tax_id: "0105551058581",
      branch_code: "00000",
      existing: [{ name: "CUS-0001", title: "Old Co" }],
    },
  });
  assert.equal(env.confirms.length, 1);
  assert.match(env.confirms[0].message, /Old Co/);
  env.confirms[0].yes();
  assert.deepEqual(env.routes[0], ["Form", "Customer", "CUS-0001"]);
  assert.deepEqual(dup.values, {});

  const none = form("Customer", { doc: { tax_id: "1234567890123" } });
  env.handlers.Customer.tax_id(none.frm);
  env.calls[1].callback({
    message: {
      found: false,
      tax_id: "1234567890123",
      branch_code: "00000",
      existing: [],
    },
  });
  assert.equal(env.alerts.at(-1).indicator, "orange");
  assert.deepEqual(none.values, {});
});

test("retyping the Tax ID or branch after a fill looks the new pair up", () => {
  const env = load();
  const { frm } = form("Supplier", {
    doc: { tax_id: "0105551058581", branch_code: "00000" },
  });
  env.handlers.Supplier.tax_id(frm);
  env.calls[0].callback({
    message: {
      found: true,
      tax_id: "0105551058581",
      branch_code: "00000",
      existing: [],
      legal_name: "A",
      address: {},
    },
  });
  assert.equal(env.calls.length, 1);
  frm.doc.branch_code = "00001";
  env.handlers.Supplier.branch_code(frm);
  assert.equal(env.calls.length, 2);
  assert.equal(env.calls[1].args.branch, "00001");
});

test("a Supplier at another branch is filled, the same branch is offered", () => {
  const env = load();
  const other = form("Supplier", {
    doc: { tax_id: "0105551058581", branch_code: "00001" },
  });
  env.handlers.Supplier.tax_id(other.frm);
  env.calls[0].callback({
    message: {
      found: true,
      tax_id: "0105551058581",
      branch_code: "00001",
      legal_name: "A",
      address: {},
      existing: [
        { name: "S-1", title: "A", branch_code: "00000", same_branch: false },
      ],
    },
  });
  assert.equal(env.confirms.length, 0);
  assert.equal(other.values.supplier_name, "A");
  const same = form("Supplier", { doc: { tax_id: "0105551058581" } });
  env.handlers.Supplier.tax_id(same.frm);
  env.calls[1].callback({
    message: {
      found: false,
      tax_id: "0105551058581",
      branch_code: "00000",
      existing: [
        { name: "S-1", title: "A", branch_code: "00000", same_branch: true },
      ],
    },
  });
  assert.equal(env.confirms.length, 1);
  assert.doesNotMatch(env.confirms[0].message, /Address/);
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
