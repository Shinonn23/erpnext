import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const item_link = (fieldname) => ({
  fieldname,
  fieldtype: "Link",
  options: "Item",
});
const uom_link = (fieldname) => ({
  fieldname,
  fieldtype: "Link",
  options: "UOM",
});

function link_control({ restrict = 1, locals = {}, docfields = {} } = {}) {
  class ControlLink {
    set_custom_query(args) {
      if (this.form_query) args.query = this.form_query;
    }
  }
  const context = {
    erpnext: {},
    locals,
    frappe: {
      provide(name) {
        context.erpnext[name.split(".")[1]] ||= {};
      },
      boot: { sysdefaults: { restrict_item_uoms: restrict } },
      meta: { get_docfields: (doctype) => docfields[doctype] || [] },
      ui: { form: { ControlLink } },
    },
  };
  vm.runInNewContext(
    fs.readFileSync(
      new URL("../../public/js/item_uom.js", import.meta.url),
      "utf8"
    ),
    context
  );
  const search = (control, doctype = "UOM") => {
    const args = { doctype };
    Object.assign(new ControlLink(), control).set_custom_query(args);
    return JSON.parse(JSON.stringify(args));
  };
  return { context, search };
}

const ITEM_QUERY = "erpnext.controllers.queries.get_item_uom_query";

test("a row UOM searches only the row Item's UOMs", () => {
  const { search } = link_control({
    locals: { Row: { row: { item_code: "Kit A" } } },
    docfields: { Row: [item_link("item_code"), uom_link("uom")] },
  });
  assert.deepEqual(
    search({ df: uom_link("uom"), doctype: "Row", docname: "row" }),
    {
      doctype: "UOM",
      query: ITEM_QUERY,
      filters: { item_code: "Kit A" },
    }
  );
});

test("a row without an Item offers no UOM, even when the form query returns nothing", () => {
  const { search } = link_control();
  const result = search({
    df: uom_link("uom"),
    doc: { item_code: "" },
    grid: { docfields: [item_link("item_code"), uom_link("uom")] },
  });
  assert.equal(result.query, ITEM_QUERY);
  assert.deepEqual(result.filters, { item_code: "" });
});

test("prefixed UOM fields follow their own Item field", () => {
  const { search } = link_control();
  const fields = [
    item_link("free_item"),
    item_link("service_item"),
    item_link("finished_good"),
  ];
  const doc = {
    free_item: "Free",
    service_item: "Service",
    finished_good: "FG",
  };
  for (const [fieldname, item_code] of [
    ["free_item_uom", "Free"],
    ["service_item_uom", "Service"],
    ["finished_good_uom", "FG"],
  ]) {
    const result = search({ df: uom_link(fieldname), doc, layout: { fields } });
    assert.deepEqual(result.filters, { item_code }, fieldname);
  }
  // BOM header and Production Plan sub-assembly rows.
  assert.deepEqual(
    search({
      df: uom_link("uom"),
      doc: { item: "BOM Item" },
      layout: { fields: [item_link("item")] },
    }).filters,
    { item_code: "BOM Item" }
  );
  assert.deepEqual(
    search({
      df: uom_link("uom"),
      doc: { production_item: "Sub Assembly" },
      layout: {
        fields: [item_link("parent_item_code"), item_link("production_item")],
      },
    }).filters,
    { item_code: "Sub Assembly" }
  );
});

test("dialog fields read the Item from the dialog", () => {
  const { search } = link_control();
  const result = search({
    df: uom_link("uom"),
    layout: {
      fields: [item_link("item_code"), uom_link("uom")],
      fields_dict: { item_code: { get_value: () => "Dialog Item" } },
    },
  });
  assert.deepEqual(result.filters, { item_code: "Dialog Item" });
});

test("UOMs not tied to an Item, form queries, and other doctypes stay as they are", () => {
  const { search } = link_control();
  const fields = [item_link("item_code"), item_link("variant_of")];
  for (const fieldname of [
    "stock_uom",
    "weight_uom",
    "pack_uom",
    "include_uom",
  ]) {
    assert.equal(
      search({
        df: uom_link(fieldname),
        doc: { item_code: "A" },
        layout: { fields },
      }).query,
      undefined,
      fieldname
    );
  }
  // The Item form's item_code is Data, so its own UOM fields are not tied to an Item link.
  assert.equal(
    search({
      df: uom_link("uom"),
      doc: { item_code: "A" },
      layout: { fields: [{ fieldname: "item_code", fieldtype: "Data" }] },
    }).query,
    undefined
  );
  assert.equal(
    search({
      df: uom_link("uom"),
      doc: { item_code: "A" },
      layout: { fields },
      form_query: "custom.query",
    }).query,
    "custom.query"
  );
  assert.equal(
    search(
      {
        df: item_link("item_code"),
        doc: { item_code: "A" },
        layout: { fields },
      },
      "Item"
    ).query,
    undefined
  );
});

test("the restriction is off when Stock Settings turn it off", () => {
  const { search } = link_control({ restrict: 0 });
  const result = search({
    df: uom_link("uom"),
    doc: { item_code: "A" },
    layout: { fields: [item_link("item_code")] },
  });
  assert.equal(result.query, undefined);
});

test("Advanced Search, which sends no doctype, is restricted too", () => {
  const { context } = link_control();
  const control = Object.assign(new context.frappe.ui.form.ControlLink(), {
    df: uom_link("uom"),
    doc: { item_code: "A" },
    layout: { fields: [item_link("item_code")] },
    get_options: () => "UOM",
  });
  const args = { txt: "" };
  control.set_custom_query(args);
  assert.equal(args.query, "erpnext.controllers.queries.get_item_uom_query");
  assert.deepEqual(JSON.parse(JSON.stringify(args.filters)), {
    item_code: "A",
  });
});

test("a same-item free UOM stays open, while a chosen free Item restricts it", () => {
  const { search } = link_control();
  const fields = [item_link("free_item")];
  assert.equal(
    search({
      df: uom_link("free_item_uom"),
      doc: { same_item: 1, free_item: "" },
      layout: { fields },
    }).query,
    undefined
  );
  assert.deepEqual(
    search({
      df: uom_link("free_item_uom"),
      doc: { same_item: 0, free_item: "" },
      layout: { fields },
    }).filters,
    { item_code: "" }
  );
  assert.deepEqual(
    search({
      df: uom_link("free_item_uom"),
      doc: { same_item: 0, free_item: "Gift" },
      layout: { fields },
    }).filters,
    { item_code: "Gift" }
  );
});
