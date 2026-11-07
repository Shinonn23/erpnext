"""Static consistency checks between DocType JSON, controllers, hooks, custom fields and workspace."""

import importlib
import json
import re
import unittest
from pathlib import Path

from erpnext import hooks
from erpnext.regulatory_affairs import constants
from erpnext.regulatory_affairs.custom_fields import RA_FIELDS, SNAPSHOT_FIELDNAMES

APP = Path(__file__).resolve().parents[2]  # .../erpnext (the python package directory)
RA = APP / "regulatory_affairs"
KNOWN_ROLES = {
	constants.ROLE_VIEWER,
	constants.ROLE_OPERATOR,
	constants.ROLE_APPROVER,
	"System Manager",
}


def doctype_files():
	return sorted((RA / "doctype").glob("*/*.json"))


def load(path):
	with open(path) as handle:
		return json.load(handle)


def camel(name):
	return "".join(part.capitalize() for part in re.split(r"[ _]", name))


class TestDocTypeFiles(unittest.TestCase):
	def test_every_constant_has_a_doctype(self):
		names = {load(path)["name"] for path in doctype_files()}
		for constant in (
			"PRODUCT_REGISTRATION",
			"REGISTERED_PRODUCT",
			"ESTABLISHMENT_REGISTRATION",
			"REGULATORY_DOCUMENT",
			"REGISTRATION_ACTION",
			"ALERT_LOG",
			"SETTINGS",
			"ALERT_RULE",
			"ALERT_RECIPIENT",
			"RENEWAL_HISTORY",
		):
			self.assertIn(getattr(constants, constant), names, constant)
		self.assertEqual(len(names), 10)

	def test_field_order_matches_fields_and_controllers_exist(self):
		for path in doctype_files():
			doc = load(path)
			names = [field["fieldname"] for field in doc["fields"]]
			self.assertEqual(names, doc["field_order"], doc["name"])
			self.assertEqual(len(names), len(set(names)), doc["name"])
			self.assertEqual(doc["module"], constants.MODULE)
			scrub = path.stem
			module = importlib.import_module(f"erpnext.regulatory_affairs.doctype.{scrub}.{scrub}")
			self.assertTrue(hasattr(module, camel(doc["name"])), f"{doc['name']} controller class")

	def test_permissions_use_known_roles_and_children_have_none(self):
		for path in doctype_files():
			doc = load(path)
			roles = {perm["role"] for perm in doc.get("permissions", [])}
			self.assertTrue(roles <= KNOWN_ROLES, (doc["name"], roles - KNOWN_ROLES))
			if doc.get("istable"):
				self.assertEqual(roles, set(), doc["name"])
			else:
				self.assertIn("System Manager", roles, doc["name"])

	def test_link_targets_and_dynamic_links_resolve(self):
		names = {load(path)["name"] for path in doctype_files()}
		for path in doctype_files():
			doc = load(path)
			fieldnames = {field["fieldname"] for field in doc["fields"]}
			for field in doc["fields"]:
				if field["fieldtype"] == "Table":
					self.assertIn(field["options"], names)
				if field["fieldtype"] == "Dynamic Link":
					self.assertIn(field["options"], fieldnames)
			for link in doc.get("links", []):
				target = load(
					RA
					/ "doctype"
					/ link["link_doctype"].lower().replace(" ", "_")
					/ (link["link_doctype"].lower().replace(" ", "_") + ".json")
				)
				self.assertIn(link["link_fieldname"], {field["fieldname"] for field in target["fields"]})

	def test_settings_fields_used_by_policy_exist(self):
		settings = load(RA / "doctype/regulatory_settings/regulatory_settings.json")
		fieldnames = {field["fieldname"] for field in settings["fields"]}
		needed = {
			f"validate_on_{doctype.lower().replace(' ', '_')}" for doctype in constants.TRANSACTION_DOCTYPES
		}
		needed |= {
			"missing_registration_action",
			"expired_registration_action",
			"suspended_registration_action",
			"revoked_registration_action",
			"missing_newcode_action",
			"manufacturer_mismatch_action",
			"expired_registration_existing_stock_policy",
			"auto_create_renewal_action",
			"renewal_start_days_before_expiry",
			"enable_expiry_alerts",
			"send_system_notification",
			"send_email",
			"overdue_repeat_days",
			"fda_query_alert_days",
			"send_weekly_digest",
			"expiry_alert_rules",
			"default_regulatory_owner",
		}
		self.assertEqual(needed - fieldnames, set())


NATIVE_JSON = {
	"Item": "stock/doctype/item/item.json",
	"Purchase Order Item": "buying/doctype/purchase_order_item/purchase_order_item.json",
	"Purchase Receipt Item": "stock/doctype/purchase_receipt_item/purchase_receipt_item.json",
	"Stock Entry Detail": "stock/doctype/stock_entry_detail/stock_entry_detail.json",
	"Sales Order Item": "selling/doctype/sales_order_item/sales_order_item.json",
	"Delivery Note Item": "stock/doctype/delivery_note_item/delivery_note_item.json",
	"Sales Invoice Item": "accounts/doctype/sales_invoice_item/sales_invoice_item.json",
}


def ra_specs(doctype):
	"""The RA DocFields of a DocType JSON, in the order of its field_order."""
	document = load(APP / NATIVE_JSON[doctype])
	by_name = {field["fieldname"]: field for field in document["fields"]}
	names = [name for name in document["field_order"] if name in RA_FIELDS[doctype]]
	return [by_name[name] for name in names], document["field_order"]


class TestRegulatoryFields(unittest.TestCase):
	"""The RA fields are DocFields in the JSON of Item and of the six item tables, not Custom Fields (migrate does only
	schema, Role Profile Authority Design 12.1)."""

	def test_every_target_holds_every_field_once_in_order_and_in_one_block(self):
		self.assertEqual(set(RA_FIELDS), set(NATIVE_JSON))
		self.assertEqual(sum(len(names) for names in RA_FIELDS.values()), 70)
		for doctype, names in RA_FIELDS.items():
			document = load(APP / NATIVE_JSON[doctype])
			fieldnames = [field["fieldname"] for field in document["fields"]]
			self.assertEqual(len(fieldnames), len(set(fieldnames)), doctype)
			self.assertEqual(sorted(document["field_order"]), sorted(fieldnames), doctype)
			specs, order = ra_specs(doctype)
			self.assertEqual([spec["fieldname"] for spec in specs], list(names), doctype)
			start = order.index(names[0])
			self.assertEqual(
				order[start : start + len(names)], list(names), f"{doctype}: block is not contiguous"
			)

	def test_the_fields_are_not_made_as_custom_fields_any_more(self):
		self.assertFalse(
			hasattr(importlib.import_module("erpnext.regulatory_affairs.custom_fields"), "CUSTOM_FIELDS")
		)

	def test_item_product_category_replaces_the_retired_classification_fields(self):
		specs = {spec["fieldname"]: spec for spec in ra_specs("Item")[0]}
		retired_fields = (
			"is_medical_device",
			"regulatory_registration_requirement",
			"regulatory_exemption_reason",
			# The Thai FDA Assessment Status pair: the Product Category alone now classifies an Item.
			"custom_thai_fda_applicability",
			"custom_thai_fda_assessment_note",
		)
		for retired in retired_fields:
			self.assertNotIn(retired, specs)
		category = specs[constants.ITEM_CATEGORY_FIELD]
		self.assertEqual(category["fieldtype"], "Select")
		self.assertEqual(category["options"].split("\n"), ["", *constants.ITEM_CATEGORIES])
		self.assertNotIn("default", category)
		self.assertIn("unclassified and is not sold", category["description"])
		self.assertIn("Other", specs["regulatory_other_category"]["depends_on"])
		self.assertEqual(specs["regulatory_other_category"]["fieldtype"], "Data")
		self.assertIn("Medical Device", specs["medical_device_category"]["depends_on"])
		self.assertNotIn("regulatory_owner", specs)
		self.assertEqual(
			specs["regulatory_current_section"]["depends_on"], "eval:doc.regulatory_product_category"
		)

	def test_item_tab_sits_between_quality_and_pricing(self):
		_, order = ra_specs("Item")
		self.assertEqual(order[order.index("regulatory_tab") - 1], "quality_inspection_template")
		self.assertEqual(order[order.index("current_registration_status") + 1], "pricing_tab")

	def test_snapshot_fields_match_declared_names(self):
		for item_doctype in constants.TRANSACTION_ITEM_DOCTYPES.values():
			fieldnames = {spec["fieldname"] for spec in ra_specs(item_doctype)[0]}
			self.assertTrue(set(SNAPSHOT_FIELDNAMES) <= fieldnames, item_doctype)

	def test_snapshot_section_is_collapsed_and_the_fields_read_only(self):
		for item_doctype in constants.TRANSACTION_ITEM_DOCTYPES.values():
			specs = {spec["fieldname"]: spec for spec in ra_specs(item_doctype)[0]}
			self.assertEqual(specs["regulatory_snapshot_section"]["collapsible"], 1, item_doctype)
			for name in SNAPSHOT_FIELDNAMES:
				self.assertEqual(specs[name]["read_only"], 1, (item_doctype, name))

	def test_field_links_point_to_ra_doctypes(self):
		names = {load(path)["name"] for path in doctype_files()}
		for doctype in RA_FIELDS:
			for spec in ra_specs(doctype)[0]:
				if spec["fieldtype"] == "Link" and spec["options"].startswith("Regulatory"):
					self.assertIn(spec["options"], names, (doctype, spec["fieldname"]))


class TestHooksAndWorkspace(unittest.TestCase):
	def test_ra_hook_handlers_resolve(self):
		handlers = []
		for events in hooks._regulatory_affairs_doc_events.values():
			for handler in events.values():
				handlers.extend(handler if isinstance(handler, list) else [handler])
		handlers.extend(
			[
				"erpnext.regulatory_affairs.alerts.run_daily_expiry_processing",
				"erpnext.regulatory_affairs.alerts.send_weekly_regulatory_digest",
				"erpnext.regulatory_affairs.install.after_migrate",
			]
		)
		for dotted in handlers:
			module_name, function_name = dotted.rsplit(".", 1)
			module = importlib.import_module(module_name)
			self.assertTrue(callable(getattr(module, function_name)), dotted)

	def test_hooks_are_merged_into_doc_events_and_scheduler(self):
		for doctype in hooks._regulatory_affairs_doc_events:
			self.assertIn(doctype, hooks.doc_events)
		purchase_receipt = hooks.doc_events["Purchase Receipt"]["before_submit"]
		purchase_receipt = purchase_receipt if isinstance(purchase_receipt, list) else [purchase_receipt]
		self.assertIn("erpnext.regulatory_affairs.transactions.validate_transaction", purchase_receipt)
		self.assertLess(
			purchase_receipt.index("erpnext.regulatory_affairs.transactions.validate_transaction"),
			purchase_receipt.index("erpnext.regulatory_affairs.transactions.capture_transaction_snapshot"),
		)
		self.assertIn(
			"erpnext.regulatory_affairs.alerts.run_daily_expiry_processing", hooks.scheduler_events["daily"]
		)
		self.assertIn(
			"erpnext.regulatory_affairs.alerts.send_weekly_regulatory_digest",
			hooks.scheduler_events["weekly"],
		)
		self.assertIn("erpnext.regulatory_affairs.install.after_migrate", hooks.after_migrate)
		self.assertTrue((APP / hooks.doctype_js["Item"]).exists())

	def test_module_is_registered(self):
		self.assertIn(constants.MODULE, (APP / "modules.txt").read_text().splitlines())

	def test_workspace_targets_exist(self):
		doctypes = {load(path)["name"] for path in doctype_files()}
		reports = {load(path)["name"] for path in (RA / "report").glob("*/*.json")}
		workspace = load(RA / "workspace/regulatory_affairs/regulatory_affairs.json")
		sidebar = load(APP / "workspace_sidebar/regulatory_affairs.json")
		desktop = load(APP / "desktop_icon/regulatory_affairs.json")
		for link in workspace["links"]:
			if link["type"] != "Link":
				continue
			self.assertIn(link["link_to"], reports if link["link_type"] == "Report" else doctypes)
		for item in sidebar["items"]:
			if item["type"] == "Link" and item["link_type"] in ("DocType", "Report"):
				self.assertIn(item["link_to"], reports | doctypes, item["label"])
		for shortcut in workspace["shortcuts"]:
			self.assertIn(shortcut["link_to"], reports | doctypes)
		self.assertEqual(desktop["link_to"], sidebar["name"])

	def test_reports_are_registered_for_ra_roles(self):
		for path in (RA / "report").glob("*/*.json"):
			report = load(path)
			self.assertEqual(report["module"], constants.MODULE)
			self.assertEqual(report["report_type"], "Script Report")
			self.assertEqual({row["role"] for row in report["roles"]}, KNOWN_ROLES, report["name"])
			module_name = f"erpnext.regulatory_affairs.report.{path.stem}.{path.stem}"
			self.assertTrue(callable(importlib.import_module(module_name).execute), report["name"])


if __name__ == "__main__":
	unittest.main()
