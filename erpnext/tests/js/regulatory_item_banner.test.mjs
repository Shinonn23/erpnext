import assert from "node:assert/strict";
import fs from "node:fs";
import { test } from "node:test";
import vm from "node:vm";

const source = fs.readFileSync(
  new URL("../../regulatory_affairs/item.js", import.meta.url),
  "utf8"
);

test("the unclassified-Item banner is cleared before it is set, so it shows once", () => {
  let handlers;
  vm.runInNewContext(source, {
    __: (text) => text,
    frappe: {
      user: { has_role: () => false },
      ui: { form: { on: (_doctype, events) => (handlers = events) } },
    },
  });
  const blocks = [];
  const frm = {
    doc: { name: "ITEM-1", is_sales_item: 1, regulatory_product_category: "" },
    is_new: () => false,
    add_custom_button() {},
    dashboard: {
      clear_headline: () => (blocks.length = 0),
      set_headline_alert: (text) => blocks.push(text),
    },
  };
  handlers.refresh(frm);
  handlers.refresh(frm);
  assert.equal(blocks.length, 1);
  assert.match(blocks[0], /has not classified this Item yet/);
});
