Request for material (Items) from internal warehouse or by purchase.

A Purchase Order made from a request (`make_purchase_order`, `make_purchase_orders_by_supplier`) starts each row at the row estimate (`estimate_rate` in `estimate_currency`) converted to the order currency (`apply_estimated_rates`); a Material Request `rate` is always in the company currency, so the mapper would otherwise copy a Baht amount into a USD order.
