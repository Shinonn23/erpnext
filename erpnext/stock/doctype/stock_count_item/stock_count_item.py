# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from typing import TYPE_CHECKING

from frappe.model.document import Document


class StockCountItem(Document):
	if TYPE_CHECKING:
		from frappe.types import DF

		batch_no: DF.Link | None
		counted_qty: DF.Float | None
		difference_qty: DF.Float | None
		is_counted: DF.Check
		item_code: DF.Link
		item_name: DF.Data | None
		parent: DF.Data
		parentfield: DF.Data
		parenttype: DF.Data
		serial_no: DF.Data | None
		storage_location: DF.Link | None
		system_qty: DF.Float

	pass
