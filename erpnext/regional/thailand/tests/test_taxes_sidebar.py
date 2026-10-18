"""Static checks of the Taxes workspace sidebar: grouping, nesting and link targets. No site or database."""

import importlib.util
import json
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[3]  # .../erpnext (the python package directory)
FRAPPE = Path(importlib.util.find_spec("frappe").origin).parent
SIDEBAR = APP / "workspace_sidebar" / "taxes.json"

TOP_LEVEL = ["Sales Tax Template", "Purchase Tax Template", "Item Tax Template"]
SECTIONS = {
	"Tax Invoices": ["Sales Tax Invoices", "Purchase Tax Invoices"],
	"Withholding Tax": ["Withholding Tax Certificates", "Deduction Certificate"],
	"Setup": [
		"Tax Category",
		"Tax Rule",
		"Tax Withholding Category",
		"Tax Withholding Group",
		"Withholding Tax Income Types",
	],
	"Reports": [
		"Sales Tax Report",
		"Purchase Tax Report",
		"PND 3 Return",
		"PND 53 Return",
		"Tax Withholding Details",
	],
	"Settings": ["Thailand Tax Settings", "Withholding Tax Settings", "Thailand Billing Settings"],
}
CLOSED_SECTIONS = {"Setup", "Reports", "Settings"}
# Frappe imports a standard document only when the file is newer than the stored one.
LAST_SYNCED_MODIFIED = "2026-10-18 11:18:36.492049"


def load(path):
	with open(path, encoding="utf-8") as handle:
		return json.load(handle)


def scrub(name):
	return name.lower().replace(" ", "_").replace("-", "_")


def find_document(kind, name):
	folder = scrub(name)
	for path in APP.glob(f"*/{kind}/{folder}/{folder}.json"):
		if load(path)["name"] == name:
			return path
	return None


class TestTaxesSidebar(unittest.TestCase):
	@classmethod
	def setUpClass(cls):
		cls.sidebar = load(SIDEBAR)
		cls.items = cls.sidebar["items"]

	def grouped(self):
		"""Return (top-level link labels, {section label: child labels}) as Frappe nests them."""
		top, sections, current = [], {}, None
		for row in self.items:
			if row["type"] == "Section Break":
				current = row["label"]
				sections[current] = []
			elif row["child"] and current:
				sections[current].append(row["label"])
			else:
				top.append(row["label"])
		return top, sections

	def test_top_level_templates_come_first_then_the_sections_in_order(self):
		top, sections = self.grouped()
		self.assertEqual(top, TOP_LEVEL)
		self.assertEqual([row["label"] for row in self.items[: len(TOP_LEVEL)]], TOP_LEVEL)
		self.assertEqual(list(sections), list(SECTIONS))

	def test_every_document_is_filed_under_its_section(self):
		_, sections = self.grouped()
		self.assertEqual(sections, SECTIONS)

	def test_nesting_follows_the_sibling_sidebars(self):
		current = None
		for row in self.items:
			if row["type"] == "Section Break":
				current = row
				self.assertEqual((row["child"], row["indent"], row["collapsible"]), (0, 1, 1), row["label"])
				self.assertTrue(row.get("icon"), row["label"])
				self.assertEqual(row["keep_closed"], int(row["label"] in CLOSED_SECTIONS), row["label"])
				self.assertNotIn("link_to", row, row["label"])
			elif row["child"]:
				self.assertIsNotNone(current, f"{row['label']} has no section to nest under")
				self.assertEqual((row["indent"], row["keep_closed"]), (0, 0), row["label"])
				self.assertNotIn("icon", row, row["label"])
			else:
				self.assertTrue(row.get("icon"), row["label"])

	def test_each_target_is_linked_once_and_exists(self):
		links = [row for row in self.items if row["type"] == "Link"]
		targets = [(row["link_type"], row["link_to"]) for row in links]
		self.assertEqual(len(targets), 20)
		self.assertEqual(len(set(targets)), len(targets), "a target is linked twice")
		self.assertEqual(len({row["label"] for row in links}), len(links), "a label is used twice")
		for link_type, name in targets:
			kind = {"DocType": "doctype", "Report": "report"}[link_type]
			self.assertTrue(find_document(kind, name), f"{link_type} {name} does not exist")

	def test_only_fields_of_the_sidebar_item_doctype_are_used(self):
		item_doctype = FRAPPE / "desk" / "doctype" / "workspace_sidebar_item" / "workspace_sidebar_item.json"
		fields = {field["fieldname"] for field in load(item_doctype)["fields"]}
		for row in self.items:
			self.assertLessEqual(set(row) - {"doctype", "name"}, fields, row["label"])

	def test_the_desktop_icon_still_finds_the_sidebar(self):
		desktop = load(APP / "desktop_icon" / "taxes.json")
		self.assertEqual(desktop["link_to"], self.sidebar["name"])
		self.assertEqual(self.sidebar["name"], self.sidebar["title"])

	def test_the_file_is_newer_than_what_a_migrated_site_holds(self):
		self.assertGreater(self.sidebar["modified"], LAST_SYNCED_MODIFIED)
