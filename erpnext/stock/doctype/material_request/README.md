Request for material (Items) from internal warehouse or by purchase.

Purchase requests use a system-chosen Estimated Buying Price List in the company currency. Their standard Rate is the approved estimate; price_list_rate is its frozen catalog baseline. Purchasing edits Rate while the request waits for pricing. The six older estimate fields are hidden historical data.

A Purchase Order made from a request (`make_purchase_order`, `make_purchase_orders_by_supplier`) starts from the approved company-currency Rate converted at the order's exchange rate (`apply_estimated_rates`). Mapping never rereads the current Item Price, so later catalog changes cannot change the approved basis. A missing foreign exchange rate is refused.

Purchase Order and Supplier Quotation mapping remove the inherited estimated list and its currency/exchange rate before native supplier/default actual-price selection. Supplier Quotations retain native actual pricing; approved request rates apply only to Purchase Orders.

Historical requests whose saved header uses an ordinary list retain the older estimate_currency / estimate_rate conversion. Their saved data is not rewritten.

`material_request_rate_in_order_currency` is the shared conversion used by the mapper and first-save price preservation. The latter validates the submitted request, row identity, company, item, UOM and factor before reconstructing a price for a user who cannot edit it; Supplier Quotation replacements retain their separate pricing policy.
