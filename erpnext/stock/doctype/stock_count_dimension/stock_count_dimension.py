# Copyright (c) 2026, Frappe Technologies Pvt. Ltd. and Contributors
# License: GNU General Public License v3. See license.txt

from typing import TYPE_CHECKING

from frappe.model.document import Document


class StockCountDimension(Document):
	if TYPE_CHECKING:
		from frappe.types import DF

		dimension_value: DF.DynamicLink | None
		inventory_dimension: DF.Link
		reference_document: DF.Data | None
