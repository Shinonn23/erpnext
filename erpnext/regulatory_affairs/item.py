"""Item validation hook for the regulatory classification.

An Item with a Product Category (``regulatory_product_category``) is regulated: transactions validate it
against the registration that covers it and the Item keeps the read-only coverage fields. An Item without
a Product Category is not checked and carries no coverage.
"""

from typing import Any

from erpnext.regulatory_affairs import coverage
from erpnext.regulatory_affairs.constants import ITEM_CATEGORY_FIELD, ITEM_COVERAGE_FIELDS


def is_regulated(item: Any) -> bool:
	"""Whether the Item has a Product Category, i.e. needs an FDA registration."""
	return bool((item.get(ITEM_CATEGORY_FIELD) or "").strip())


def validate(doc, method=None):
	"""Item `validate` hook: refresh the derived coverage of a regulated Item and clear it otherwise."""
	if is_regulated(doc):
		coverage.refresh_item_coverage(doc)
	else:
		doc.update(dict.fromkeys(ITEM_COVERAGE_FIELDS))
