"""Status > Close / Reopen of a Delivery Pick List (Siwat, 17 Nov 2026). Offline: mocks, no site."""

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import frappe

from erpnext.controllers.status_updater import status_map
from erpnext.stock.doctype.pick_list import pick_list as module
from erpnext.stock.doctype.pick_list.pick_list import PickList


def refuse(message, *args, **kwargs):
	raise frappe.ValidationError(message)


class Base(unittest.TestCase):
	def setUp(self):
		self.addCleanup(patch.stopall)
		patch.object(frappe, "throw", side_effect=refuse).start()
		patch.object(frappe, "db", MagicMock()).start()
		patch.object(frappe, "flags", frappe._dict()).start()


def row(**values):
	defaults = {
		"idx": 1,
		"item_code": "ITEM",
		"warehouse": "W",
		"storage_location": "",
		"batch_no": "",
		"serial_no": "",
		"serial_and_batch_bundle": "",
		"picked_qty": 5,
		"delivered_qty": 0,
	}
	return frappe._dict({**defaults, **values})


def pick(**values):
	doc = PickList.__new__(PickList)
	defaults = {
		"name": "PL-1",
		"docstatus": 1,
		"purpose": "Delivery",
		"borrowing": None,
		"status": "Open",
		"locations": [row()],
	}
	doc.__dict__.update({**defaults, **values})
	return doc


class TestStatusMap(Base):
	def test_closed_is_the_last_rule_so_it_wins(self):
		rules = status_map["Pick List"]
		self.assertEqual(rules[-1][0], "Closed")
		self.assertEqual(rules[-2][0], "Cancelled")
		self.assertIn("self.status=='Closed'", rules[-1][1])
		self.assertIn("self.docstatus != 2", rules[-1][1])

	def test_a_closed_pick_stays_closed_when_a_delivery_note_recomputes_it(self):
		doc = pick(status="Closed", delivery_status="Partly Delivered")
		doc.as_dict = lambda: frappe._dict(
			status="Closed", docstatus=1, purpose="Delivery", delivery_status="Partly Delivered"
		)
		doc.get = lambda key, default=None: doc.__dict__.get(key, default)
		doc.doctype = "Pick List"
		self.assertEqual(PickList.get_status(doc), {"status": "Closed"})
		doc.as_dict = lambda: frappe._dict(
			status="Open", docstatus=1, purpose="Delivery", delivery_status="Partly Delivered"
		)
		self.assertEqual(PickList.get_status(doc), {"status": "Partly Delivered"})


class TestClose(Base):
	def test_only_an_open_delivery_pick_of_orders_can_close(self):
		for values in (
			{"purpose": "Material Transfer"},
			{"borrowing": "BRW-1"},
			{"status": "Completed"},
			{"status": "Closed"},
			{"status": "Cancelled"},
			{"docstatus": 0},
		):
			doc = pick(**values)
			self.assertFalse(PickList.can_be_closed(doc), values)
		for status in ("Open", "Partly Delivered"):
			self.assertTrue(PickList.can_be_closed(pick(status=status)))

	def test_close_refuses_what_cannot_close(self):
		doc = pick(status="Completed")
		doc.can_be_closed = lambda: PickList.can_be_closed(doc)
		with self.assertRaises(frappe.ValidationError):
			PickList.close(doc)

	def test_close_releases_reservations_then_sets_status_and_recounts(self):
		doc = pick()
		doc.can_be_closed = lambda: True
		doc.has_reserved_stock = lambda: True
		order = []
		doc.cancel_stock_reservation_entries = lambda notify=True: order.append("sre")
		doc.db_set = lambda field, value: order.append(("db_set", field, value))
		doc.update_reference_qty = lambda: order.append("qty")
		doc.update_sales_order_picking_status = lambda: order.append("picking")
		doc.update_prevdoc_status = lambda: order.append("prev")
		doc.add_comment = lambda *args: order.append("comment")
		with patch.object(module, "record_released_reservations"):
			PickList.close(doc)
		self.assertEqual(order[:3], ["sre", ("db_set", "status", "Closed"), "qty"])
		self.assertIn("picking", order)

	def test_a_refused_reservation_release_leaves_the_pick_open(self):
		doc = pick()
		doc.can_be_closed = lambda: True
		doc.has_reserved_stock = lambda: True
		doc.db_set = MagicMock()

		def refuse(notify=True):
			raise frappe.ValidationError("no")

		doc.cancel_stock_reservation_entries = refuse
		with (
			patch.object(module, "record_released_reservations"),
			self.assertRaises(frappe.ValidationError),
		):
			PickList.close(doc)
		doc.db_set.assert_not_called()

	def test_a_closed_pick_cannot_be_cancelled_or_edited(self):
		doc = pick(status="Closed")
		with self.assertRaises(frappe.ValidationError):
			PickList.before_cancel(doc)
		with self.assertRaises(frappe.ValidationError):
			PickList.on_update_after_submit(doc)


class TestReopen(Base):
	def reopen(self, shortages, **values):
		doc = pick(status="Closed", **values)
		calls = []
		doc.db_set = lambda field, value: calls.append((field, value))
		doc.update_status = lambda: calls.append("status")
		doc.update_reference_qty = lambda: calls.append("qty")
		doc.update_sales_order_picking_status = lambda: calls.append("picking")
		doc.update_prevdoc_status = lambda: calls.append("prev")
		doc.add_comment = lambda *args: calls.append("comment")
		doc.reload = lambda: calls.append("reload")
		doc.create_stock_reservation_entries = lambda notify=True: calls.append("reserve")
		with (
			patch.object(module, "get_reopen_shortages", return_value=shortages),
			patch.object(module, "get_reopen_over_picks", return_value=[]),
			patch.object(module, "was_reserved_by", return_value=False),
		):
			PickList.reopen(doc)
		return calls

	def test_reopen_with_everything_free_holds_the_stock_again(self):
		calls = self.reopen([])
		self.assertEqual(calls[0], ("status", "Open"))
		self.assertIn("qty", calls)
		self.assertNotIn("reserve", calls)

	def test_a_pick_of_a_closed_or_cancelled_order_cannot_reopen(self):
		for values, word in ((("Closed", 1), "Reopen Sales Order"), (("Cancelled", 2), "cancelled")):
			doc = pick(status="Closed", locations=[row(sales_order="SO-1")])
			frappe.db.get_value.return_value = frappe._dict(status=values[0], docstatus=values[1])
			with patch.object(module, "get_reopen_shortages", return_value=[]):
				with self.assertRaises(frappe.ValidationError) as caught:
					PickList.reopen(doc)
			self.assertIn(word, str(caught.exception))

	def test_reopen_is_refused_naming_each_short_row_and_changes_nothing(self):
		doc = pick(status="Closed")
		doc.db_set = MagicMock()
		with (
			patch.object(module, "get_reopen_shortages", return_value=["Row 1: ITEM in W: 5 needed, 2 free"]),
			self.assertRaises(frappe.ValidationError) as caught,
		):
			PickList.reopen(doc)
		self.assertIn("Row 1: ITEM in W: 5 needed, 2 free", str(caught.exception))
		doc.db_set.assert_not_called()

	def test_reopen_recreates_the_reservations_the_close_released(self):
		doc = pick(status="Closed")
		calls = []
		for name in (
			"db_set",
			"update_status",
			"update_reference_qty",
			"update_sales_order_picking_status",
			"update_prevdoc_status",
			"add_comment",
			"reload",
		):
			setattr(doc, name, lambda *args, **kwargs: calls.append("x"))
		doc.create_stock_reservation_entries = lambda notify=True: calls.append("reserve")
		with (
			patch.object(module, "get_reopen_shortages", return_value=[]),
			patch.object(module, "get_reopen_over_picks", return_value=[]),
			patch.object(module, "was_reserved_by", return_value=True),
			patch.object(module, "get_reopen_unreserved", return_value=[]),
			patch.object(module.frappe, "get_cached_value", return_value=1),
		):
			PickList.reopen(doc)
		self.assertIn("reserve", calls)

	def test_reopen_is_refused_when_another_open_pick_already_holds_the_order(self):
		doc = pick(status="Closed")
		doc.db_set = MagicMock()
		with (
			patch.object(module, "get_reopen_shortages", return_value=[]),
			patch.object(
				module, "get_reopen_over_picks", return_value=["Row 1: ITEM of SO-1 (picked by PL-2)"]
			),
			self.assertRaises(frappe.ValidationError) as caught,
		):
			PickList.reopen(doc)
		self.assertIn("PL-2", str(caught.exception))
		doc.db_set.assert_not_called()

	def test_only_a_closed_submitted_pick_reopens(self):
		doc = pick(status="Open")
		with self.assertRaises(frappe.ValidationError):
			PickList.reopen(doc)


class TestReopenOverPicks(Base):
	def over(self, rows, so_item, others=(), moved=None):
		doc = pick(locations=rows)
		doc.get = lambda key, default=None: doc.__dict__.get(key, default)
		frappe.db.get_value.side_effect = lambda doctype, name, fields=None, **kw: (
			frappe._dict(so_item) if doctype == "Sales Order Item" else "Open"
		)
		with (
			patch.object(module.frappe, "get_all", return_value=list(others)),
			patch.object(module, "_", side_effect=lambda text: text),
			patch(
				"connect_diagnostics_setup.stock.outgoing_inspection.rejected_moved_by_row",
				return_value=moved or {},
			),
		):
			return module.get_reopen_over_picks(doc)

	def so_item(self, picked):
		return {"stock_qty": 10, "picked_qty": picked, "parent": "SO-1", "item_code": "ITEM"}

	def test_a_second_open_pick_for_the_same_quantity_blocks_the_reopen(self):
		rows = [row(name="R", sales_order_item="SOI-1", picked_qty=10)]
		found = self.over(rows, self.so_item(picked=10), others=["PL-2"])
		self.assertEqual(len(found), 1)
		self.assertIn("PL-2", found[0])

	def test_room_left_on_the_order_passes(self):
		rows = [row(name="R", sales_order_item="SOI-1", picked_qty=10)]
		self.assertEqual(self.over(rows, self.so_item(picked=0)), [])

	def test_what_was_delivered_is_not_held_again(self):
		rows = [row(name="R", sales_order_item="SOI-1", picked_qty=10, delivered_qty=6)]
		self.assertEqual(self.over(rows, self.so_item(picked=6)), [])


class TestReopenShortages(Base):
	def shortages(self, rows, others=(), located=None, unlocated=None, serials_in_warehouse=(), moved=None):
		doc = pick(locations=rows)
		doc.get = lambda key, default=None: doc.__dict__.get(key, default)
		doc._get_pick_list_items = lambda items: [frappe._dict(other) for other in others]
		from erpnext.stock.doctype.storage_location import storage_location

		def stock(item, warehouse, batch=None, unlocated=False):
			return (unlocated_map if unlocated else located_map) or {}

		located_map, unlocated_map = located, unlocated

		with (
			patch.object(storage_location, "location_stock", side_effect=stock),
			patch.object(module.frappe, "get_all", return_value=list(serials_in_warehouse)),
			patch.object(module, "_", side_effect=lambda text: text),
			patch(
				"connect_diagnostics_setup.stock.outgoing_inspection.rejected_moved_by_row",
				return_value=moved or {},
			),
		):
			return module.get_reopen_shortages(doc)

	def test_goods_rejected_and_moved_to_damage_are_not_needed_back(self):
		rows = [row(name="ROW-1", storage_location="W / A", picked_qty=10, delivered_qty=0)]
		self.assertEqual(self.shortages(rows, located={"W / A": 8}, moved={"ROW-1": 2}), [])

	def test_free_stock_passes(self):
		self.assertEqual(self.shortages([row(storage_location="W / A")], located={"W / A": 5}), [])

	def test_stock_held_by_another_open_pick_is_short(self):
		other = {
			"item_code": "ITEM",
			"warehouse": "W",
			"storage_location": "W / A",
			"batch_no": "",
			"serial_no": "",
			"serial_and_batch_bundle": "",
			"picked_qty": 3,
		}
		found = self.shortages([row(storage_location="W / A")], others=[other], located={"W / A": 5})
		self.assertEqual(len(found), 1)
		self.assertIn("Row 1", found[0])

	def test_only_the_remaining_quantity_is_needed(self):
		rows = [row(storage_location="W / A", picked_qty=5, delivered_qty=3)]
		self.assertEqual(self.shortages(rows, located={"W / A": 2}), [])
		self.assertEqual(len(self.shortages(rows, located={"W / A": 1})), 1)

	def test_a_row_without_a_location_counts_the_whole_warehouse(self):
		rows = [row(picked_qty=5)]
		self.assertEqual(self.shortages(rows, located={"W / A": 3}, unlocated={"": 2}), [])
		self.assertEqual(len(self.shortages(rows, located={"W / A": 3}, unlocated={"": 1})), 1)

	def test_serials_must_still_be_in_the_warehouse(self):
		rows = [row(serial_no="S1\nS2", picked_qty=2)]
		self.assertEqual(self.shortages(rows, serials_in_warehouse=["S1", "S2"]), [])
		self.assertEqual(len(self.shortages(rows, serials_in_warehouse=["S1"])), 1)


class TestCloseOrderPicks(Base):
	def run_close(self, picks, orders, cancelling=False, sales_order="SO-1"):
		"""``picks`` {name: values}; ``orders`` {pick: [sales_order, ...]}; SO statuses in ``self.so_status``."""
		closed_docs = []

		def get_all(doctype, filters=None, pluck=None, **kwargs):
			if filters.get("sales_order"):
				return list(picks)
			return orders[filters["parent"]]

		def get_value(doctype, name, fields, as_dict=False):
			if doctype == "Pick List":
				return frappe._dict(picks[name])
			return self.so_status[name]

		def get_doc(doctype, name, for_update=False):
			doc = MagicMock()
			doc.close = lambda: closed_docs.append(name)
			doc.flags = frappe._dict()
			return doc

		with patch.object(
			module,
			"frappe",
			MagicMock(
				**{
					"get_all.side_effect": get_all,
					"get_doc.side_effect": get_doc,
					"db.get_value.side_effect": get_value,
					"_dict": frappe._dict,
				}
			),
		):
			result = module.close_open_picks_of_order(sales_order, cancelling=cancelling)
		return result, closed_docs

	def open_pick(self, **values):
		return {"docstatus": 1, "purpose": "Delivery", "borrowing": None, "status": "Open", **values}

	def test_a_single_order_pick_closes_with_a_closed_order(self):
		self.so_status = {"SO-1": "Closed"}
		result, closed = self.run_close({"PL-1": self.open_pick()}, {"PL-1": ["SO-1"]})
		self.assertEqual(result, (["PL-1"], []))
		self.assertEqual(closed, ["PL-1"])

	def test_the_order_being_cancelled_counts_as_cancelled(self):
		self.so_status = {"SO-1": "To Deliver and Bill"}
		result, _closed = self.run_close({"PL-1": self.open_pick()}, {"PL-1": ["SO-1"]}, cancelling=True)
		self.assertEqual(result, (["PL-1"], []))

	def test_a_pick_shared_with_an_open_order_is_left(self):
		self.so_status = {"SO-1": "Closed", "SO-2": "To Deliver and Bill"}
		result, closed = self.run_close({"PL-1": self.open_pick()}, {"PL-1": ["SO-1", "SO-2"]})
		self.assertEqual(result, ([], ["PL-1"]))
		self.assertEqual(closed, [])

	def test_borrowing_completed_and_closed_picks_are_ignored(self):
		self.so_status = {"SO-1": "Closed"}
		picks = {
			"PL-1": self.open_pick(borrowing="BRW-1"),
			"PL-2": self.open_pick(status="Completed"),
			"PL-3": self.open_pick(status="Closed"),
			"PL-4": self.open_pick(purpose="Material Transfer"),
		}
		result, _closed = self.run_close(picks, {name: ["SO-1"] for name in picks})
		self.assertEqual(result, ([], []))


class TestPickedQtySql(Base):
	def test_a_closed_pick_counts_only_what_it_delivered(self):
		from frappe.query_builder import get_query_builder
		from pypika.queries import QueryBuilder

		captured = {}

		def fake_run(query, as_dict=False):
			captured["sql"] = str(query)
			return []

		patch.object(frappe, "qb", get_query_builder("mariadb")).start()
		patch.object(QueryBuilder, "run", fake_run, create=True).start()
		patch(
			"connect_diagnostics_setup.stock.outgoing_inspection.adjust_picked_totals",
			lambda rows, packed: rows,
		).start()
		module.get_picked_items_qty(["ROW-1"])
		sql = captured["sql"].upper()
		self.assertIn("LEAST", sql)
		self.assertIn("'CLOSED'", sql)
		self.assertIn("DELIVERED_QTY", sql)


class TestSellingSide(Base):
	def test_a_delivery_note_row_of_a_closed_pick_is_refused(self):
		from erpnext.controllers.selling_controller import SellingController

		doc = frappe._dict(doctype="Delivery Note", items=[frappe._dict(against_pick_list="PL-1")])
		doc.get = lambda key, default=None: doc[key] if key in doc else default
		frappe.db.get_value.return_value = "Closed"
		with self.assertRaises(frappe.ValidationError):
			SellingController.check_pick_list_not_closed(doc)
		frappe.db.get_value.return_value = "Open"
		SellingController.check_pick_list_not_closed(doc)

	def test_a_cancel_is_never_blocked_by_a_closed_pick(self):
		from erpnext.controllers.selling_controller import SellingController

		doc = frappe._dict(
			doctype="Delivery Note", docstatus=2, items=[frappe._dict(against_pick_list="PL-1")]
		)
		doc.get = lambda key, default=None: doc[key] if key in doc else default
		frappe.db.get_value.return_value = "Closed"
		SellingController.check_pick_list_not_closed(doc)

	def test_an_invoice_that_does_not_move_stock_is_not_checked(self):
		from erpnext.controllers.selling_controller import SellingController

		doc = frappe._dict(
			doctype="Sales Invoice", update_stock=0, items=[frappe._dict(against_pick_list="PL-1")]
		)
		doc.get = lambda key, default=None: doc[key] if key in doc else default
		frappe.db.get_value.return_value = "Closed"
		SellingController.check_pick_list_not_closed(doc)

	def test_a_sales_order_closes_its_picks_and_refuses_to_cancel_around_a_shared_one(self):
		from erpnext.selling.doctype.sales_order import sales_order as so_module
		from erpnext.selling.doctype.sales_order.sales_order import SalesOrder

		doc = frappe._dict(name="SO-1", add_comment=MagicMock())
		with (
			patch.object(module, "close_open_picks_of_order", return_value=(["PL-1"], [])),
			patch.object(so_module, "_", side_effect=lambda text: text),
		):
			SalesOrder.close_delivery_picks(doc)
		doc.add_comment.assert_called_once()
		with (
			patch.object(module, "close_open_picks_of_order", return_value=([], ["PL-2"])),
			patch.object(so_module, "_", side_effect=lambda text: text),
			patch.object(frappe, "bold", side_effect=lambda text: text),
			self.assertRaises(frappe.ValidationError),
		):
			SalesOrder.close_delivery_picks(doc, cancelling=True)


SRE = "Stock Reservation Entry"


def sre(docstatus, reserved, delivered, detail="ROW-1", name="SRE-1"):
	return frappe._dict(
		name=name,
		docstatus=docstatus,
		reserved_qty=reserved,
		delivered_qty=delivered,
		from_voucher_detail_no=detail,
	)


class TestReopenReservations(Base):
	def was_reserved(self, entries, released=("SRE-1",)):
		with (
			patch.object(module.frappe, "get_all", return_value=entries),
			patch.object(module, "released_by_last_close", return_value=list(released)),
		):
			return module.was_reserved_by("PL-1")

	def test_a_fully_delivered_entry_does_not_hide_the_cancelled_one(self):
		# Partly Delivered: one entry delivered (stays submitted), one cancelled by the Close
		self.assertTrue(self.was_reserved([sre(1, 5, 5, name="SRE-0"), sre(2, 5, 2)]))

	def test_nothing_cancelled_or_something_still_reserved_is_not_reserved_again(self):
		self.assertFalse(self.was_reserved([sre(1, 5, 5)]))
		self.assertFalse(self.was_reserved([sre(2, 5, 0), sre(1, 5, 1, name="SRE-2")]))
		self.assertFalse(self.was_reserved([]))

	def test_an_entry_a_user_unreserved_by_hand_is_not_the_close_s(self):
		# cancelled, but not among the entries the last Close recorded
		self.assertFalse(self.was_reserved([sre(2, 5, 0)], released=()))
		self.assertFalse(self.was_reserved([sre(2, 5, 0, name="SRE-9")], released=("SRE-1",)))

	def test_the_last_close_comment_names_the_released_entries(self):
		def comment(content):
			with patch.object(module.frappe, "get_all", return_value=[frappe._dict(content=content)]):
				return module.released_by_last_close("PL-1")

		self.assertEqual(comment(module.RELEASED_BY_CLOSE + " SRE-1, SRE-2"), ["SRE-1", "SRE-2"])
		self.assertEqual(comment("<div>" + module.RELEASED_BY_CLOSE + " -</div>"), [])
		with patch.object(module.frappe, "get_all", return_value=[]):
			self.assertEqual(module.released_by_last_close("PL-1"), [])

	def test_close_records_the_open_entries_before_cancelling(self):
		doc = pick(status="Open", locations=[row(stock_reserved_qty=5)])
		doc.can_be_closed = lambda: True
		for name in (
			"db_set",
			"update_reference_qty",
			"update_sales_order_picking_status",
			"update_prevdoc_status",
			"add_comment",
			"cancel_stock_reservation_entries",
		):
			setattr(doc, name, MagicMock())
		entries = [sre(1, 5, 2, name="SRE-1"), sre(1, 5, 5, name="SRE-0")]
		with (
			patch.object(module.frappe, "get_all", return_value=entries),
			patch.object(module, "recount_bundle_picked_qty"),
		):
			PickList.close(doc)
		doc.add_comment.assert_any_call("Info", module.RELEASED_BY_CLOSE + " SRE-1")

	def unreserved(self, entries, rows, moved=None):
		doc = pick(locations=rows)
		doc.get = lambda key, default=None: doc.__dict__.get(key, default)
		with (
			patch.object(module.frappe, "get_all", return_value=entries),
			patch.object(module, "_", side_effect=lambda text: text),
			patch(
				"connect_diagnostics_setup.stock.outgoing_inspection.rejected_moved_by_row",
				return_value=moved or {},
			),
		):
			return module.get_reopen_unreserved(doc)

	def so_row(self, **values):
		return row(name="ROW-1", sales_order="SO-1", sales_order_item="SOI-1", **values)

	def test_a_reservation_that_holds_more_than_needed_is_named_too(self):
		lines = self.unreserved([sre(1, 5, 0)], [self.so_row(picked_qty=5, delivered_qty=2)])
		self.assertEqual(len(lines), 1)

	def test_a_fully_delivered_row_holding_nothing_passes(self):
		self.assertEqual(self.unreserved([], [self.so_row(picked_qty=5, delivered_qty=5)]), [])

	def test_a_reopen_reserves_exactly_what_is_still_needed(self):
		# Close cancelled an entry (reserved 5, delivered 2): the row has picked 5, delivered 2, holds nothing
		doc = pick(
			status="Open", locations=[self.so_row(picked_qty=5, delivered_qty=2, stock_reserved_qty=0)]
		)
		delivered = {"ROW-1": 2}
		self.assertEqual(doc.qty_to_reserve(doc.locations[0], delivered, {}, {}), 3)
		self.assertEqual(doc.qty_to_reserve(doc.locations[0], delivered, {"ROW-1": 1}, {}), 2)
		self.assertEqual(doc.qty_to_reserve(doc.locations[0], delivered, {}, {"ROW-1": 3}), 0)
		self.assertEqual(doc.qty_to_reserve(doc.locations[0], {"ROW-1": 5}, {}, {}), 0)

	def test_create_reservations_asks_for_picked_less_delivered(self):
		doc = pick(
			status="Open", locations=[self.so_row(picked_qty=5, delivered_qty=2, stock_reserved_qty=0)]
		)
		doc.check_permission = MagicMock()
		sales_order = MagicMock()
		with (
			patch(
				"connect_diagnostics_setup.stock.outgoing_inspection.rejected_moved_by_row", return_value={}
			),
			patch.object(module.frappe, "get_all", return_value=[]),
			patch.object(module.frappe, "get_doc", return_value=sales_order),
		):
			doc.create_stock_reservation_entries(notify=False)
		details = sales_order._create_stock_reservation_entries.call_args.kwargs["items_details"]
		self.assertEqual(details[0]["qty_to_reserve"], 3)

	def test_a_reservation_that_covers_the_remainder_passes(self):
		self.assertEqual(self.unreserved([sre(1, 3, 0)], [self.so_row(picked_qty=5, delivered_qty=2)]), [])

	def test_a_partial_reservation_is_named(self):
		lines = self.unreserved([sre(1, 2, 0)], [self.so_row(picked_qty=5, delivered_qty=2)])
		self.assertEqual(len(lines), 1)
		self.assertIn("Row 1", lines[0])

	def test_a_delivered_entry_holds_nothing_and_a_rejected_move_is_not_needed(self):
		rows = [self.so_row(picked_qty=5, delivered_qty=5)]
		self.assertEqual(self.unreserved([sre(1, 5, 5)], rows), [])
		rows = [self.so_row(picked_qty=5, delivered_qty=0)]
		self.assertEqual(self.unreserved([sre(1, 3, 0)], rows, moved={"ROW-1": 2}), [])

	def test_reopen_throws_when_the_reservation_came_out_short(self):
		doc = pick(status="Closed")
		for name in (
			"db_set",
			"update_status",
			"update_reference_qty",
			"update_sales_order_picking_status",
			"update_prevdoc_status",
			"add_comment",
			"reload",
		):
			setattr(doc, name, MagicMock())
		doc.create_stock_reservation_entries = MagicMock()
		with (
			patch.object(module, "get_reopen_shortages", return_value=[]),
			patch.object(module, "get_reopen_over_picks", return_value=[]),
			patch.object(module, "recount_bundle_picked_qty"),
			patch.object(module, "was_reserved_by", return_value=True),
			patch.object(module, "get_reopen_unreserved", return_value=["Row 1: ITEM"]),
			patch.object(module.frappe, "get_cached_value", return_value=1),
		):
			with self.assertRaises(frappe.ValidationError):
				PickList.reopen(doc)
		doc.add_comment.assert_not_called()


def bundle_row(name, item, picked, delivered=0, bundle="PI-A", parent="PL-1"):
	return frappe._dict(
		name=name,
		idx=1,
		parent=parent,
		item_code=item,
		product_bundle_item=bundle,
		sales_order="SO-1",
		sales_order_item="SOI-1",
		picked_qty=picked,
		delivered_qty=delivered,
		warehouse="W",
	)


def bundle_rows(picked, delivered, parent="PL-1"):
	"""A bundle of 1 A + 2 B, `picked` bundles. A Delivery Note writes the delivered BUNDLES (not parts) to one
	component row only (add_product_bundles_to_target points the parent at the last component row)."""
	return [
		bundle_row("A-" + parent, "A", picked, 0, "PI-A", parent),
		bundle_row("B-" + parent, "B", picked * 2, delivered, "PI-B", parent),
	]


class BundleBase(Base):
	def setUp(self):
		super().setUp()
		bundle = SimpleNamespace(
			items=[SimpleNamespace(item_code=k, qty=v) for k, v in {"A": 1, "B": 2}.items()]
		)
		self.status_of = {}
		frappe.db.get_value.side_effect = lambda doctype, name, field=None, *a, **k: (
			"BUNDLE" if doctype == "Sales Order Item" else self.status_of[name]
		)
		patch.object(module.frappe, "get_last_doc", return_value=bundle).start()


class TestBundleRecount(BundleBase):
	def recount(self, pick_rows, status_of):
		self.status_of = status_of
		doc = pick(locations=[frappe._dict(sales_order_item="SOI-1", product_bundle_item="PI-A")])
		doc.get = lambda key, default=None: doc.__dict__.get(key, default)
		with patch.object(module.frappe, "get_all", return_value=pick_rows):
			module.recount_bundle_picked_qty(doc)
		return module.frappe.db.set_value.call_args

	def test_a_closed_pick_gives_back_what_it_did_not_deliver(self):
		# 5 bundles picked, 2 delivered: the Delivery Note wrote 2 (bundles) to ONE component row
		call = self.recount(bundle_rows(5, 2), {"PL-1": "Closed"})
		self.assertEqual(call.args[:4], ("Sales Order Item", "SOI-1", "picked_qty", 2))

	def test_a_reopened_pick_holds_all_its_bundles_again(self):
		call = self.recount(bundle_rows(5, 2), {"PL-1": "Partly Delivered"})
		self.assertEqual(call.args[3], 5)

	def test_another_pick_still_counts_and_an_incomplete_set_is_not_a_bundle(self):
		rows = [
			*bundle_rows(5, 0),
			bundle_row("A2", "A", 3, 0, "PI-A2", "PL-2"),
			bundle_row("B2", "B", 2, 0, "PI-B2", "PL-2"),
		]
		call = self.recount(rows, {"PL-1": "Closed", "PL-2": "Open"})
		# PL-1 closed delivers nothing; PL-2 holds min(3/1, 2/2) = 1 bundle
		self.assertEqual(call.args[3], 1)

	def test_delivered_bundles_never_exceed_the_picked_ones(self):
		self.assertEqual(module.bundle_state(bundle_rows(2, 5), {"A": 1, "B": 2}), (2, 2))


class TestBundleRows(BundleBase):
	def doc(self, rows, **values):
		doc = pick(locations=rows, **values)
		doc.get = lambda key, default=None: doc.__dict__.get(key, default)
		return doc

	def test_the_delivered_bundles_become_parts_on_each_component_row(self):
		doc = self.doc(bundle_rows(5, 2))
		self.assertEqual(module.delivered_by_row(doc), {"A-PL-1": 2, "B-PL-1": 4})

	def test_a_plain_row_keeps_its_delivered_quantity(self):
		doc = self.doc([row(name="R", picked_qty=5, delivered_qty=2)])
		self.assertEqual(module.delivered_by_row(doc), {"R": 2})

	def test_a_part_picked_from_two_places_is_filled_in_row_order(self):
		first = bundle_row("A1", "A", 2, 0, "PI-A")
		second = bundle_row("A2", "A", 3, 0, "PI-A")
		b = bundle_row("B1", "B", 10, 3, "PI-B")
		self.assertEqual(module.delivered_by_row(self.doc([first, second, b])), {"A1": 2, "A2": 1, "B1": 6})

	def test_the_reopen_reserves_the_parts_not_yet_delivered(self):
		doc = self.doc(bundle_rows(5, 2))
		held = {"A-PL-1": 3, "B-PL-1": 6}
		with (
			patch.object(module.frappe, "get_all", return_value=[]),
			patch.object(module, "open_reservations_by_row", return_value=held),
			patch(
				"connect_diagnostics_setup.stock.outgoing_inspection.rejected_moved_by_row", return_value={}
			),
			patch.object(module, "_", side_effect=lambda text: text),
		):
			self.assertEqual(module.get_reopen_unreserved(doc), [])
			held["B-PL-1"] = 4
			self.assertEqual(len(module.get_reopen_unreserved(doc)), 1)

	def test_the_shortage_check_needs_only_the_undelivered_parts(self):
		from erpnext.stock.doctype.storage_location import storage_location

		doc = self.doc(bundle_rows(5, 2))
		doc._get_pick_list_items = lambda items: []
		# 3 A and 6 B are still to deliver
		with (
			patch.object(storage_location, "location_stock", side_effect=lambda item, *a, **k: {"x": 6}),
			patch.object(module.frappe, "get_all", return_value=[]),
			patch.object(module, "_", side_effect=lambda text: text),
			patch(
				"connect_diagnostics_setup.stock.outgoing_inspection.rejected_moved_by_row", return_value={}
			),
		):
			self.assertEqual(module.get_reopen_shortages(doc), [])

	def over(self, rows, so_item):
		doc = self.doc(rows)
		frappe.db.get_value.side_effect = lambda doctype, name, fields=None, *a, **k: (
			frappe._dict(so_item)
			if fields and not isinstance(fields, str)
			else ("Open" if doctype == "Pick List" else "BUNDLE")
		)
		with (
			patch.object(module.frappe, "get_all", return_value=["PL-2"]),
			patch.object(module, "_", side_effect=lambda text: text),
			patch(
				"connect_diagnostics_setup.stock.outgoing_inspection.rejected_moved_by_row", return_value={}
			),
		):
			return module.get_reopen_over_picks(doc)

	def so_item(self, picked):
		return {"stock_qty": 5, "picked_qty": picked, "parent": "SO-1", "item_code": "BUNDLE"}

	def test_a_second_pick_of_the_bundle_blocks_the_reopen_of_the_first(self):
		# PL-1 delivered 2 of 5; after its Close another pick took the 3 bundles given back, now 2 + 5 > 5
		found = self.over(bundle_rows(5, 2), self.so_item(picked=5))
		self.assertEqual(len(found), 1)
		self.assertIn("PL-2", found[0])

	def test_room_left_for_the_bundles_passes_and_delivered_ones_are_not_held(self):
		self.assertEqual(self.over(bundle_rows(5, 2), self.so_item(picked=2)), [])
		self.assertEqual(self.over(bundle_rows(5, 5), self.so_item(picked=5)), [])


class TestUpdatePickListStatus(BundleBase):
	"""A Delivery Note of a Closed pick is submitted or cancelled: the order counts what was delivered."""

	def run_status(self, status, rows):
		self.status_of = {"PL-1": status}
		doc = pick(status=status, locations=rows)
		doc.get = lambda key, default=None: doc.__dict__.get(key, default)
		doc.run_method = MagicMock()
		doc.update_reference_qty = MagicMock()
		doc.update_sales_order_picking_status = MagicMock()
		with (
			patch.object(module.frappe, "get_doc", return_value=doc),
			patch.object(module.frappe, "get_all", return_value=rows),
		):
			module.update_pick_list_status("PL-1")
		return doc

	def test_a_delivery_of_a_closed_pick_recounts_the_bundles_it_delivered(self):
		doc = self.run_status("Closed", bundle_rows(5, 3))
		doc.update_reference_qty.assert_called_once()
		doc.update_sales_order_picking_status.assert_called_once()
		self.assertEqual(
			module.frappe.db.set_value.call_args.args[:4], ("Sales Order Item", "SOI-1", "picked_qty", 3)
		)

	def test_a_cancelled_delivery_gives_the_bundles_back_again(self):
		doc = self.run_status("Closed", bundle_rows(5, 1))
		self.assertEqual(module.frappe.db.set_value.call_args.args[3], 1)
		self.assertIsNotNone(doc)

	def test_an_open_pick_is_not_recounted(self):
		doc = self.run_status("Open", bundle_rows(5, 0))
		doc.update_reference_qty.assert_not_called()
