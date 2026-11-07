"""The Regulatory Affairs fields of Item and of the item tables of the guarded transactions.

They are DocFields in the DocType JSON (Item, Purchase Order Item, Purchase Receipt Item, Stock Entry Detail, Sales Order
Item, Delivery Note Item, Sales Invoice Item), not Custom Fields: migrate only does schema (Role Profile Authority Design
12.1, batch e). This module lists their names and the constants the code and the tests share.

Layout rules (see the DKSH notes on `insert_after`): the Regulatory tab of Item opens after `quality_inspection_template`,
the last field of the Quality tab, and each item table's Regulatory Snapshot section is the last section of the row form,
so no native section is swallowed by a new Section/Tab Break (`field_order` in each JSON).
"""

from erpnext.regulatory_affairs.constants import TRANSACTION_ITEM_DOCTYPES

# The Product Category switches the FDA checks on and off, so it sits on its own permission level: every Item
# reader sees it, only the Regulatory roles change it (through `item.set_product_category`).
ITEM_CATEGORY_PERMLEVEL = 1

SNAPSHOT_FIELDNAMES = (
	"regulatory_registration",
	"regulatory_newcode",
	"regulatory_identifier",
	"regulatory_manufacturer",
	"regulatory_manufacturer_country",
	"regulatory_registration_status",
	"regulatory_registration_expiry",
	"regulatory_coverage_date",
)

ITEM_FIELDNAMES = (
	"regulatory_tab",
	"regulatory_product_category",
	"regulatory_other_category",
	"medical_device_category",
	"regulatory_current_section",
	"current_regulatory_registration",
	"current_newcode",
	"regulatory_current_column",
	"current_registration_expiry",
	"current_registration_status",
)

# The section and column breaks of an item row's snapshot, around the eight snapshot fields.
SNAPSHOT_BREAKS = ("regulatory_snapshot_section", "regulatory_snapshot_column")

# DocType -> fieldnames, in the order of the form (71 fields).
RA_FIELDS = {
	"Item": ITEM_FIELDNAMES,
	**{
		item_doctype: ("regulatory_snapshot_section", *SNAPSHOT_FIELDNAMES[:4], "regulatory_snapshot_column")
		+ SNAPSHOT_FIELDNAMES[4:]
		for item_doctype in TRANSACTION_ITEM_DOCTYPES.values()
	},
}
