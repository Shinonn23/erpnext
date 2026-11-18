import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import vm from "node:vm";

// UX WP9 (19 Nov 2026): the Stock Entry and Asset Movement grids show only the fields of the purpose.
const read = (path) => readFileSync(new URL(path, import.meta.url), "utf8");

function stock_entry_columns(purpose) {
  const source = read("../../stock/doctype/stock_entry/stock_entry.js");
  const start = source.indexOf("\ttoggle_warehouse_fields(frm) {");
  const end = source.indexOf("\n\tpurpose: function (frm) {", start);
  const body = source.slice(start, end).trim().replace(/,$/, "");
  const events = vm.runInNewContext(`({${body}})`, {});
  const shown = {};
  const grid = {
    update_docfield_property(field, property, value) {
      if (property === "in_list_view") shown[field] = value;
    },
    reset_grid() {},
  };
  events.toggle_warehouse_fields({
    doc: { purpose },
    fields_dict: { items: { grid } },
  });
  return shown;
}

test("a Stock Entry location column follows its warehouse column", () => {
  for (const [purpose, source, target] of [
    ["Material Issue", 1, 0],
    ["Material Receipt", 0, 1],
    ["Receive from Customer", 0, 1],
    ["Material Transfer", 1, 1],
    ["Repack", 1, 1],
  ]) {
    const shown = stock_entry_columns(purpose);
    assert.equal(shown.s_warehouse, source, `${purpose}: s_warehouse`);
    assert.equal(
      shown.storage_location,
      source,
      `${purpose}: storage_location`
    );
    assert.equal(shown.t_warehouse, target, `${purpose}: t_warehouse`);
    assert.equal(
      shown.to_storage_location,
      target,
      `${purpose}: to_storage_location`
    );
  }
});

function movement_helpers() {
  const source = read("../../assets/doctype/asset_movement/asset_movement.js");
  const start = source.indexOf("const MOVEMENT_FIELDS_BY_PURPOSE");
  const end = source.indexOf(
    "async function set_material_request_purpose_lock",
    start
  );
  return vm.runInNewContext(
    `${source.slice(
      start,
      end
    )}; ({ movement_fields, hidden_movement_values })`,
    {}
  );
}

const ROW = {
  source_location: "Office",
  target_location: "Typed",
  from_employee: "EMP-1",
};

test("a purpose change empties the locations the new purpose hides", () => {
  const { hidden_movement_values } = movement_helpers();
  for (const purpose of ["Issue", "Reassignment"]) {
    const values = hidden_movement_values({ doc: { purpose } }, ROW);
    assert.equal(values.source_location, null, purpose);
    assert.equal(values.target_location, null, purpose);
    assert.equal(values.change_custodian, 0);
  }
  for (const purpose of ["Transfer", "Receipt", "Transfer and Issue"]) {
    const values = hidden_movement_values({ doc: { purpose } }, ROW);
    assert.equal("source_location" in values, false, purpose);
    assert.equal("target_location" in values, false, purpose);
  }
  assert.equal(
    hidden_movement_values({ doc: { purpose: "Transfer" } }, ROW).to_employee,
    "EMP-1"
  );
});

test("service equipment and borrowing rows keep the places the system writes", () => {
  const { hidden_movement_values } = movement_helpers();
  const equipment = hidden_movement_values(
    { doc: { purpose: "Issue" } },
    { ...ROW, service_equipment: "EQ" }
  );
  assert.equal("target_location" in equipment, false);
  const borrowing = hidden_movement_values(
    { doc: { purpose: "Issue", is_borrowing_transaction: 1 } },
    ROW
  );
  assert.equal("target_location" in borrowing, false);
});

test("every purpose shows the custodian it moves from and an Issue shows no location", () => {
  const { movement_fields } = movement_helpers();
  for (const purpose of [
    "Transfer",
    "Receipt",
    "Issue",
    "Transfer and Issue",
    "Reassignment",
  ]) {
    assert.equal(movement_fields(purpose).has("from_employee"), true, purpose);
  }
  assert.equal(movement_fields("Issue").has("target_location"), false);
  assert.equal(movement_fields("Receipt").has("change_custodian"), false);
});
