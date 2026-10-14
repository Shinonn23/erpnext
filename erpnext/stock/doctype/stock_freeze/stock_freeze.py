# Copyright (c) 2026, ERPNext contributors
# License: GNU General Public License v3. See license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import now_datetime

from erpnext.stock.stock_control import (
	get_asset_stock_control_items,
	lock_stock_control_items,
	require_stock_control_role,
)

_RELEASE = object()
TARGET_FIELDS = {"Item": "item_code", "Batch": "batch_no", "Serial No": "serial_no", "Asset": "asset"}
IMMUTABLE_FIELDS = (
	"company",
	"freeze_level",
	"item_code",
	"batch_no",
	"serial_no",
	"asset",
	"reason",
)
RELEASE_FIELDS = ("status", "release_reason", "released_by", "released_on")


class StockFreeze(Document):
	def validate(self):
		require_stock_control_role()
		if not (self.reason or "").strip():
			frappe.throw(_("A Freeze Reason is required."))
		field = TARGET_FIELDS.get(self.freeze_level)
		if (
			not field
			or not self.get(field)
			or any(self.get(other) for other in TARGET_FIELDS.values() if other != field)
		):
			frappe.throw(_("Select exactly one target matching the Freeze Level."))
		if self.is_new():
			items, assets = self.get_lock_targets()
			lock_stock_control_items(items, assets)
			if self.asset:
				company = frappe.db.get_value("Asset", self.asset, "company", for_update=True)
				if company != self.company:
					frappe.throw(_("Asset must belong to the Freeze Company."))
			if self.serial_no:
				company = frappe.db.get_value("Serial No", self.serial_no, "company")
				if company and company != self.company:
					frappe.throw(_("Serial No must belong to the Freeze Company."))
			if self.status != "Active" or any(self.get(f) for f in RELEASE_FIELDS if f != "status"):
				frappe.throw(_("A new Stock Freeze must be Active and cannot have release details."))
			return
		old = frappe.db.get_value("Stock Freeze", self.name, "*", as_dict=True, for_update=True)
		if any(self.get(f) != old.get(f) for f in IMMUTABLE_FIELDS):
			frappe.throw(
				_(
					"A Stock Freeze target and original details cannot be changed. Release it and create another Freeze."
				)
			)
		changed = any(self.get(f) != old.get(f) for f in RELEASE_FIELDS)
		if changed:
			if (
				self.get("_stock_control_release") is not _RELEASE
				or old.status != "Active"
				or self.status != "Released"
			):
				frappe.throw(
					_("Use Release to release an Active Stock Freeze. Released records cannot be reopened.")
				)
			if not (self.release_reason or "").strip():
				frappe.throw(_("A Release Reason is required."))

	def get_lock_targets(self, values=None):
		values = values or self
		if values.asset:
			asset = frappe.db.get_value("Asset", values.asset, "*", as_dict=True)
			if not asset:
				frappe.throw(_("Asset does not exist."))
			return get_asset_stock_control_items(asset), (values.asset,)
		if values.batch_no:
			return (frappe.db.get_value("Batch", values.batch_no, "item"),), ()
		if values.serial_no:
			return (frappe.db.get_value("Serial No", values.serial_no, "item_code"),), ()
		return (values.item_code,), ()

	@frappe.whitelist()
	def release(self, reason):
		self.check_permission("write")
		require_stock_control_role()
		if not isinstance(reason, str) or not reason.strip():
			frappe.throw(_("A Release Reason is required."))
		# Targets are immutable; use persisted values rather than the caller's document payload.
		persisted = frappe.db.get_value("Stock Freeze", self.name, "*", as_dict=True)
		if not persisted:
			frappe.throw(_("Stock Freeze does not exist."))
		lock_stock_control_items(*self.get_lock_targets(persisted))
		was_for_update = self.flags.for_update
		self.flags.for_update = True
		try:
			self.reload()
		finally:
			self.flags.for_update = was_for_update
		if self.status != "Active":
			frappe.throw(_("Only an Active Stock Freeze can be released."))
		self._stock_control_release = _RELEASE
		self.status = "Released"
		self.release_reason = reason.strip()
		self.released_by = frappe.session.user
		self.released_on = now_datetime()
		self.save()
		return self

	def on_trash(self):
		frappe.throw(_("Stock Freeze history cannot be deleted. Release the Freeze instead."))


def on_doctype_update():
	for target in TARGET_FIELDS.values():
		frappe.db.add_index("Stock Freeze", ["company", "status", "freeze_level", target])
