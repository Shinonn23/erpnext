# Copyright (c) 2018, Frappe and Contributors
# License: GNU General Public License v3. See license.txt


import frappe


def execute():
	"""

	Fields to move from the item to item defaults child table
	[ default_warehouse, expense_account, income_account ]

	"""
	if not frappe.db.has_column("Item", "default_warehouse"):
		return

	frappe.reload_doc("stock", "doctype", "item_default")
	frappe.reload_doc("stock", "doctype", "item")

	companies = frappe.get_all("Company")
	if len(companies) == 1 and not frappe.get_all("Item Default", limit=1):
		try:
			frappe.db.sql(
				"""
					INSERT INTO `tabItem Default`
						(name, parent, parenttype, parentfield, idx, company, default_warehouse,
						expense_account, income_account, default_supplier)
					SELECT
						SUBSTRING(SHA2(name,224), 1, 10) as name, name as parent, 'Item' as parenttype,
						'item_defaults' as parentfield, 1 as idx, %s as company, default_warehouse,
						expense_account, income_account, default_supplier
					FROM `tabItem`;
			""",
				companies[0].name,
			)
		except Exception:
			pass
	else:
		item_details = frappe.db.sql(
			""" SELECT name, default_warehouse,
				expense_account, income_account
			FROM tabItem
			WHERE
				name not in (select distinct parent from `tabItem Default`) and ifnull(disabled, 0) = 0""",
			as_dict=1,
		)

		items_default_data = {}
		for item_data in item_details:
			for d in [
				["default_warehouse", "Warehouse"],
				["expense_account", "Account"],
				["income_account", "Account"],
			]:
				if item_data.get(d[0]):
					company = frappe.get_value(d[1], item_data.get(d[0]), "company", cache=True)

					if item_data.name not in items_default_data:
						items_default_data[item_data.name] = {}

					company_wise_data = items_default_data[item_data.name]

					if company not in company_wise_data:
						company_wise_data[company] = {}

					default_data = company_wise_data[company]
					default_data[d[0]] = item_data.get(d[0])

		to_insert_data = []

		# items_default_data data structure will be as follow
		# {
		# 	'item_code 1': {'company 1': {'default_warehouse': 'Test Warehouse 1'}},
		# 	'item_code 2': {
		# 		'company 1': {'default_warehouse': 'Test Warehouse 1'},
		# 		'company 2': {'default_warehouse': 'Test Warehouse 1'}
		# 	}
		# }

		for item_code, companywise_item_data in items_default_data.items():
			for company, item_default_data in companywise_item_data.items():
				to_insert_data.append(
					(
						frappe.generate_hash("", 10),
						item_code,
						"Item",
						"item_defaults",
						company,
						item_default_data.get("default_warehouse"),
						item_default_data.get("expense_account"),
						item_default_data.get("income_account"),
					)
				)

		if to_insert_data:
			frappe.db.sql(
				"""
				INSERT INTO `tabItem Default`
				(
					`name`, `parent`, `parenttype`, `parentfield`, `company`, `default_warehouse`,
					`expense_account`, `income_account`
				)
				VALUES {}
			""".format(", ".join(["%s"] * len(to_insert_data))),
				tuple(to_insert_data),
			)
