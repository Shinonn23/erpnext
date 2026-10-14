# Copyright (c) 2026, ERPNext contributors
# License: GNU General Public License v3. See license.txt
from frappe.model.document import Document

from erpnext.stock.stock_control import validate_stock_control_type


class LocationType(Document):
	def validate(self):
		validate_stock_control_type(self)
