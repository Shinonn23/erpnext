# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from typing import TYPE_CHECKING

import frappe
from frappe import _
from frappe.model.document import Document


class StockCountTemplate(Document):
	if TYPE_CHECKING:
		from frappe.types import DF

		from erpnext.stock.doctype.stock_count_template_item.stock_count_template_item import (
			StockCountTemplateItem,
		)

		company: DF.Link
		disabled: DF.Check
		items: DF.Table[StockCountTemplateItem]
		title: DF.Data
		warehouse: DF.Link | None

	def validate(self):
		from erpnext.stock.doctype.stock_count.stock_count import validate_countable_item_rows

		self.title = (self.title or "").strip()
		if not self.title:
			frappe.throw(_("Title is required."))
		if self.warehouse:
			warehouse = frappe.db.get_value(
				"Warehouse", self.warehouse, ["company", "is_group", "disabled"], as_dict=True
			)
			if not warehouse or warehouse.company != self.company or warehouse.is_group or warehouse.disabled:
				frappe.throw(_("Select an enabled, non-group Warehouse of Company {0}.").format(self.company))
		if not self.items:
			frappe.throw(_("Add at least one item to the template."))
		validate_countable_item_rows(self.items)
