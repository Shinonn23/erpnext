# Copyright (c) 2023, Kitti U. and contributors
# For license information, please see license.txt

# import frappe
from frappe.model.document import Document


class WithholdingTaxSetting(Document):
	# begin: auto-generated types
	# This code is auto-generated. Do not modify anything in this block.

	from typing import TYPE_CHECKING

	if TYPE_CHECKING:
		from frappe.types import DF

		wht_cert_image: DF.AttachImage | None
	# end: auto-generated types

	pass
