"""The simpler loan return of 5 Nov 2026 (Siwat): a shipped return follows the unshipped path, a missing Quarantine
warehouse is said plainly, a loan row may be picked from several places, one reason for a write-off. Offline."""

from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import MagicMock, patch

import frappe

from erpnext.loan.doctype.custody_loan import custody_loan_core as core


class Row(SimpleNamespace):
	def get(self, fieldname, default=None):
		return getattr(self, fieldname, default)

	def as_dict(self):
		return dict(vars(self))


def throw(message, *args, **kwargs):
	raise frappe.ValidationError(message)


class Patched(TestCase):
	def setUp(self):
		for patcher in (
			patch.object(frappe, "throw", side_effect=throw),
			patch.object(core, "_", side_effect=lambda text: text),
		):
			patcher.start()
			self.addCleanup(patcher.stop)


class TestReturnTemplate(Patched):
	def test_a_returned_row_reverses_the_delivery_row_that_held_its_serial_or_batch(self):
		originals = {
			"D1": Row(name="D1", batch_no="B1", serial_no="S1\nS2", warehouse="A"),
			"D2": Row(name="D2", batch_no="B2", serial_no="S3", warehouse="B"),
		}
		candidates = [{"dn_detail": "D1"}, {"dn_detail": "D2"}]
		by_serial = core._original_return_template(candidates, originals, Row(serial_no="S3", batch_no=None))
		by_batch = core._original_return_template(candidates, originals, Row(serial_no=None, batch_no="B2"))
		neither = core._original_return_template(candidates, originals, Row(serial_no=None, batch_no=None))
		self.assertEqual(
			(by_serial["dn_detail"], by_batch["dn_detail"], neither["dn_detail"]), ("D2", "D2", "D1")
		)


class TestQuarantineWarehouse(Patched):
	def doc(self, *items):
		return Row(docstatus=0, company="CD", items=[Row(item_code=item) for item in items])

	def test_an_inspected_item_without_a_quarantine_warehouse_is_refused_plainly(self):
		with (
			patch.object(core, "quarantine_warehouse_for", return_value=None),
			patch.object(
				core,
				"custody_loan_return_row_requires_inspection",
				side_effect=lambda row: row.item_code == "DEVICE",
			),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "DEVICE is inspected.*Quarantine warehouse"):
				core.require_quarantine_warehouse(self.doc("DEVICE", "PLAIN"))
			core.require_quarantine_warehouse(self.doc("PLAIN"))

	def test_nothing_is_refused_when_the_company_has_one(self):
		with (
			patch.object(core, "quarantine_warehouse_for", return_value="QUARANTINE - CD"),
			patch.object(core, "custody_loan_return_row_requires_inspection", return_value=True),
		):
			core.require_quarantine_warehouse(self.doc("DEVICE"))

	def test_the_warehouse_of_the_row_is_asked_so_a_taken_company_quarantine_is_refused(self):
		doc = Row(docstatus=0, company="CD", items=[Row(item_code="DEVICE", warehouse="STORES - CD")])
		with (
			patch.object(core, "quarantine_warehouse_for", return_value=None) as lookup,
			patch.object(core, "custody_loan_return_row_requires_inspection", return_value=True),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "DEVICE is inspected"):
				core.require_quarantine_warehouse(doc)
		lookup.assert_called_with("CD", "STORES - CD")


class FakeWarehouses:
	"""A Warehouse table for the quarantine lookup: {name: {...}}."""

	def __init__(self, rows):
		self.rows = rows

	def has_column(self, doctype, fieldname):
		return True

	def get_value(self, doctype, name, fields, as_dict=False):
		row = self.rows.get(name)
		if row is None:
			return None
		if isinstance(fields, str):
			return row.get(fields)
		return frappe._dict({field: row.get(field) for field in fields})

	def patched(self):
		def get_all(doctype, filters=None, fields=None, order_by=None, pluck=None):
			if pluck:
				return [row[pluck] for row in self.rows.values() if row.get(pluck)]
			return [
				frappe._dict(name=name, **{field: row.get(field) for field in fields if field != "name"})
				for name, row in sorted(self.rows.items())
				if row["company"] == filters["company"]
				and not row.get("is_group")
				and not row.get("disabled")
			]

		return (
			patch.object(frappe, "db", self),
			patch.object(frappe, "get_all", side_effect=get_all),
		)


def warehouses(**extra):
	base = {
		"CD STOCK - CD": {"company": "CD", "parent_warehouse": "All - CD", "warehouse_type": "Stock"},
		"LAB CWT - CD": {"company": "CD", "parent_warehouse": "All - CD", "warehouse_type": "Stock"},
		"LAB GS - CD": {"company": "CD", "parent_warehouse": "All - CD", "warehouse_type": "Stock"},
		"QUARANTINE - CD": {"company": "CD", "parent_warehouse": "All - CD", "warehouse_type": "Quarantine"},
		"QUARANTINE - CD STOCK - CD": {
			"company": "CD",
			"parent_warehouse": "QUARANTINE BY WAREHOUSE - CD",
			"warehouse_type": "Quarantine",
		},
		"QUARANTINE - LAB CWT - CD": {
			"company": "CD",
			"parent_warehouse": "QUARANTINE BY WAREHOUSE - CD",
			"warehouse_type": "Quarantine",
		},
	}
	base["CD STOCK - CD"]["quarantine_warehouse"] = "QUARANTINE - CD STOCK - CD"
	base["LAB CWT - CD"]["quarantine_warehouse"] = "QUARANTINE - LAB CWT - CD"
	for name, values in extra.items():
		base[name.replace("_", " ")].update(values)
	return base


class TestQuarantinePerWarehouse(Patched):
	"""Siwat, 7 Nov 2026: goods are held in the quarantine of the warehouse they belong to; the company's one stays
	the fallback of a warehouse with no link."""

	def lookup(self, rows, company, near=None):
		first, second = FakeWarehouses(rows).patched()
		with first, second:
			return core.quarantine_warehouse_for(company, near)

	def test_a_warehouse_with_its_own_quarantine_uses_it(self):
		rows = warehouses()
		self.assertEqual(self.lookup(rows, "CD", "CD STOCK - CD"), "QUARANTINE - CD STOCK - CD")
		self.assertEqual(self.lookup(rows, "CD", "LAB CWT - CD"), "QUARANTINE - LAB CWT - CD")

	def test_a_warehouse_without_the_link_falls_back_to_the_company_one(self):
		self.assertEqual(self.lookup(warehouses(), "CD", "LAB GS - CD"), "QUARANTINE - CD")

	def test_the_companys_one_never_is_another_warehouses_own(self):
		rows = warehouses()
		del rows["QUARANTINE - CD"]
		self.assertIsNone(self.lookup(rows, "CD", "LAB GS - CD"))
		# asked for the company without a warehouse, any quarantine will do
		self.assertIn(self.lookup(rows, "CD"), {"QUARANTINE - CD STOCK - CD", "QUARANTINE - LAB CWT - CD"})

	def test_a_link_to_a_disabled_group_or_foreign_warehouse_is_not_used(self):
		for change in ({"disabled": 1}, {"is_group": 1}, {"company": "X"}):
			rows = warehouses()
			rows["QUARANTINE - LAB CWT - CD"].update(change)
			self.assertEqual(self.lookup(rows, "CD", "LAB CWT - CD"), "QUARANTINE - CD", change)

	def test_a_site_without_the_field_keeps_the_old_lookup(self):
		rows = warehouses()
		db = FakeWarehouses(rows)
		db.has_column = lambda *args: False
		first, second = db.patched()
		with first, second:
			self.assertEqual(core.quarantine_warehouse_for("CD", "LAB CWT - CD"), "QUARANTINE - CD")

	def test_every_warehouse_of_the_type_is_a_quarantine_warehouse(self):
		rows = warehouses()
		first, second = FakeWarehouses(rows).patched()
		with first, second:
			for name in ("QUARANTINE - CD", "QUARANTINE - CD STOCK - CD", "QUARANTINE - LAB CWT - CD"):
				self.assertTrue(core.is_quarantine_warehouse(name, "CD"), name)
			self.assertFalse(core.is_quarantine_warehouse("LAB CWT - CD", "CD"))
			self.assertFalse(core.is_quarantine_warehouse("QUARANTINE - LAB CWT - CD", "OTHER"))

	def test_an_inspected_row_is_received_into_the_quarantine_of_its_warehouse_at_its_staging_location(self):
		db = FakeWarehouses(warehouses())
		first, second = db.patched()
		rows = [
			Row(item_code="D", warehouse="LAB CWT - CD", storage_location="LAB CWT-LOC"),
			Row(item_code="E", warehouse="LAB GS - CD", storage_location=None),
		]
		with (
			first,
			second,
			patch.object(core, "get_default_storage_location", side_effect=lambda wh, item: f"STAGING-{wh}"),
		):
			core.receive_row_into_quarantine(rows[0], "CD")
			core.receive_row_into_quarantine(rows[1], "CD")
		self.assertEqual(
			[(row.warehouse, row.storage_location) for row in rows],
			[
				("QUARANTINE - LAB CWT - CD", "STAGING-QUARANTINE - LAB CWT - CD"),
				("QUARANTINE - CD", "STAGING-QUARANTINE - CD"),
			],
		)

	def test_a_row_already_in_quarantine_or_without_a_quarantine_is_left_alone(self):
		db = FakeWarehouses(warehouses())
		first, second = db.patched()
		row = Row(item_code="D", warehouse="QUARANTINE - CD", storage_location="X")
		with first, second:
			self.assertEqual(core.receive_row_into_quarantine(row, "CD"), "QUARANTINE - CD")
		self.assertEqual((row.warehouse, row.storage_location), ("QUARANTINE - CD", "X"))
		other = Row(item_code="D", warehouse="LAB CWT - CD")
		with (
			first,
			second,
			patch.object(core, "quarantine_warehouse_for", return_value=None),
		):
			self.assertIsNone(core.receive_row_into_quarantine(other, "CD"))
		self.assertEqual(other.warehouse, "LAB CWT - CD")


class TestNoShipmentCoupling(Patched):
	"""A Shipment is optional (Siwat, 5 Nov 2026): nothing in a loan waits for one."""

	def test_receiving_requires_explicit_submission_of_the_receiving_document(self):
		doc = core.CustodyLoanReturn.__new__(core.CustodyLoanReturn)
		note = Row(name="DN-1", items=[Row(item_code="PLAIN")])
		with (
			patch.object(core, "receive_return_delivery_note") as receive,
			patch.object(core, "custody_loan_return_row_requires_inspection", return_value=True),
		):
			with self.assertRaises(frappe.ValidationError):
				doc.receive_returned_goods(note)
		receive.assert_not_called()

	def test_the_shipment_gates_are_gone(self):
		for name in (
			"receive_shipped_return",
			"validate_custody_loan_return_shipment_arrival",
			"validate_delivery_note_shipment_link",
		):
			self.assertFalse(hasattr(core, name), name)
		self.assertFalse(hasattr(core.CustodyLoan, "validate_shipment_scope"))


class TestPickListRows(Patched):
	"""A loan row may be picked from several locations, batches and warehouses."""

	def check(self, rows, loan_qty=10, warehouse_ok=True, require_picked_qty=False):
		loan = SimpleNamespace(
			loan_type="Stock",
			docstatus=1,
			company="CD",
			doctype="Custody Loan",
			check_permission=MagicMock(),
			db_set=MagicMock(),
			pick_list="PL-1",
			material_request="MR-1",
			name="L1",
			items=[
				SimpleNamespace(name="LI-1", item_code="DEVICE", qty=loan_qty, material_request_item="MRI-1")
			],
		)
		pick_list = SimpleNamespace(
			name="PL-1",
			company="CD",
			purpose="Delivery",
			material_request="MR-1",
			customer="INTERNAL",
			custody_loan="L1",
			locations=rows,
			flags=SimpleNamespace(get=lambda key: None),
		)

		def get_value(doctype, name, fields=None, as_dict=False):
			if doctype == "Material Request Item":
				return SimpleNamespace(
					parent="MR-1", item_code="DEVICE", uom="Nos", stock_uom="Nos", conversion_factor=1
				)
			if doctype == "Warehouse":
				return SimpleNamespace(company="CD", is_group=0, disabled=0) if warehouse_ok else None
			return None

		with (
			patch.object(frappe, "get_doc", return_value=loan),
			patch.object(frappe, "db", MagicMock(get_value=MagicMock(side_effect=get_value))),
			patch.object(core, "get_internal_loan_customer", return_value="INTERNAL"),
			# offline, frappe's flt cannot round (no site settings)
			patch.object(core, "flt", side_effect=lambda value, precision=None: float(value or 0)),
		):
			core.validate_custody_loan_pick_list(pick_list, require_picked_qty=require_picked_qty)

	def row(self, qty, warehouse="STORES - CD", picked=0):
		return SimpleNamespace(
			idx=1,
			custody_loan_item="LI-1",
			item_code="DEVICE",
			material_request="MR-1",
			material_request_item="MRI-1",
			uom="Nos",
			stock_uom="Nos",
			conversion_factor=1,
			qty=qty,
			stock_qty=qty,
			warehouse=warehouse,
			picked_qty=picked or qty,
		)

	def test_rows_from_several_locations_and_warehouses_add_up_to_the_loan_qty(self):
		self.check([self.row(6), self.row(4, "OTHER - CD")], require_picked_qty=True)

	def test_rows_that_do_not_add_up_are_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "add up to 9.0, but the Loan Qty is 10"):
			self.check([self.row(6), self.row(3)])

	def test_a_disabled_or_foreign_warehouse_is_refused(self):
		with self.assertRaisesRegex(frappe.ValidationError, "choose an enabled warehouse"):
			self.check([self.row(10)], warehouse_ok=False)


class TestPickedWarehousesFollow(Patched):
	def test_the_loan_row_follows_the_warehouse_most_was_picked_from(self):
		loan = SimpleNamespace(items=[SimpleNamespace(name="LI-1", source_warehouse="DEFAULT - CD")])
		pick = SimpleNamespace(
			custody_loan="L1",
			locations=[
				SimpleNamespace(custody_loan_item="LI-1", warehouse="DEFAULT - CD", picked_qty=2),
				SimpleNamespace(custody_loan_item="LI-1", warehouse="OTHER - CD", picked_qty=8),
			],
		)
		db = MagicMock()
		with patch.object(frappe, "get_doc", return_value=loan), patch.object(frappe, "db", db):
			core.follow_picked_warehouses(pick)
		db.set_value.assert_called_once_with("Custody Loan Item", "LI-1", "source_warehouse", "OTHER - CD")


class TestAdjustmentReason(Patched):
	def adjustment(self, header, row):
		doc = core.CustodyLoanAdjustment.__new__(core.CustodyLoanAdjustment)
		doc.name = "ADJ-1"
		doc.reason = header
		doc.custody_loan = "L1"
		doc.items = [Row(idx=1, custody_loan_item="LI-1", qty=1, reason=row, batch_no=None, serial_no=None)]
		loan = SimpleNamespace(items=[SimpleNamespace(name="LI-1", item_code="DEVICE")])
		return doc, loan

	def run_validate(self, header, row):
		doc, loan = self.adjustment(header, row)
		with (
			patch.object(core, "resolve_loan_item_rows"),
			patch.object(core, "is_tracked", return_value=False),
			patch.object(core, "validate_tracked_rows"),
			patch.object(core, "loan_item_available_to_adjust", return_value=5),
		):
			doc.validate_items(loan)
		return doc.items[0].reason

	def test_a_row_without_its_own_reason_takes_the_one_of_the_adjustment(self):
		self.assertEqual(self.run_validate("Lost at the hospital", ""), "Lost at the hospital")
		self.assertEqual(self.run_validate("Lost at the hospital", "Broken"), "Broken")

	def test_a_reason_is_required_somewhere(self):
		with self.assertRaisesRegex(frappe.ValidationError, "give the reason"):
			self.run_validate("", "")
