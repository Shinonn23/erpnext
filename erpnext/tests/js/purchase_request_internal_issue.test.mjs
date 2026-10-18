import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(
  new URL("../../stock/doctype/stock_entry/stock_entry.js", import.meta.url), "utf8"
);
const start = source.indexOf("function () {\n\t\t\t\t\tconst purchase_internal_issue");
assert.ok(start > 0);
const end = source.indexOf('\n\t\t\t\t},\n\t\t\t\t__("Get Items From")', start);
assert.ok(end > start);
const callback = source.slice(start, end) + "\n}";

function mapping(overrides = {}) {
  let options;
  const context = {
    frm: { doc: { docstatus: 0, purpose: "Material Issue", operation_issue_type: "Internal Use", company: "CD", ...overrides } },
    __: value => value,
    erpnext: { utils: { map_current_doc: value => { options = value; return {}; } } },
  };
  vm.runInNewContext(`(${callback})()`, context);
  return options;
}

test("Internal Use draft offers Purchase in the existing mapper with the same Company", () => {
  const value = mapping();
  assert.equal(value.method, "erpnext.stock.doctype.material_request.material_request.make_stock_entry");
  assert.equal(value.source_doctype, "Material Request");
  assert.equal(value.target.doc.company, "CD");
  assert.equal(value.setters[0].default, "Purchase");
  assert.ok(value.setters[0].options.split("\n").includes("Purchase"));
  assert.equal(value.get_query_filters.company, "CD");
  assert.equal(value.get_query_filters.docstatus, 1);
  assert.ok(value.get_query_filters.material_request_type[1].includes("Purchase"));
  assert.deepEqual(Array.from(value.get_query_filters.status[1]), ["Transferred", "Issued", "Cancelled", "Stopped"]);
});

test("other issue classifications and transfer/manufacture routes keep their existing options", () => {
  for (const change of [
    { docstatus: 1 }, { docstatus: 2 }, { purpose: "Material Transfer" },
    { purpose: "Material Transfer for Manufacture" },
    { operation_issue_type: "Demo" }, { operation_issue_type: "Maintenance Spare Parts" },
    { operation_issue_type: "Disposal" }, { operation_issue_type: undefined },
  ]) {
    const value = mapping(change);
    assert.equal(value.setters[0].default, "Material Transfer");
    assert.ok(!value.setters[0].options.split("\n").includes("Purchase"));
    assert.ok(!value.get_query_filters.material_request_type[1].includes("Purchase"));
    assert.equal(value.get_query_filters.company, undefined);
  }
});
