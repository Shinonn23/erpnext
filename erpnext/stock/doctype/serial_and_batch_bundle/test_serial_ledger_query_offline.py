"""Bulk serial filters retain legacy exact-line matches without oversized regexes."""

import unittest
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from pypika import MySQLQuery, Table
from pypika.dialects import MySQLQueryBuilder

from erpnext.stock.doctype.serial_and_batch_bundle.serial_and_batch_bundle import (
	get_stock_ledgers_for_serial_nos,
)


class TestSerialLedgerQuery(unittest.TestCase):
	def query(self, serial_nos):
		queries = []
		def run(query, **kwargs):
			queries.append(query.get_sql())
			return []

		qb = SimpleNamespace(DocType=Table, from_=MySQLQuery.from_)
		with patch.object(frappe, "qb", qb), patch.object(MySQLQueryBuilder, "run", run, create=True):
			get_stock_ledgers_for_serial_nos(
				frappe._dict(serial_nos=serial_nos, item_code="ITEM", warehouse="WH", voucher_no="PR")
			)
		return queries[0]

	def test_bulk_receipt_bounds_regexes_and_preserves_all_serials(self):
		serials = [str(12937129313 - i) for i in range(3000)]
		sql = self.query(serials)
		self.assertEqual(sql.count(" REGEXP "), 60)
		for serial in serials:
			self.assertIn(serial, sql)
		self.assertIn("`serial_no` IN (", sql)
		self.assertIn("`is_cancelled`=0", sql)
		self.assertIn("`warehouse`='WH'", sql)
		self.assertIn("`voucher_no`<>'PR'", sql)
		self.assertIn("ORDER BY", sql)

	def test_literal_regex_characters_and_line_boundaries_are_preserved(self):
		sql = self.query(["SER.1", "SER+2", "SER(3)"])
		self.assertEqual(sql.count(" REGEXP "), 1)
		self.assertIn(r"\n(SER\.1|SER\+2|SER\(3\))\n", sql)

	def test_unfiltered_query_does_not_add_a_serial_join(self):
		sql = self.query([])
		self.assertNotIn("REGEXP", sql)
		self.assertNotIn("Serial and Batch Entry", sql)
