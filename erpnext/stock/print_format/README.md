# Stock print formats

Stock Count Audit prints System Qty, Counted Qty and Difference Qty with two decimal places using the site's
number separators. Uncounted quantities stay blank; a counted zero prints as `0.00` (or its locale equivalent).
This changes print output only, including for historical counts; stored quantities and stock calculations stay intact.
Row numbers, item identifiers and the integer number of selected item codes retain their existing representation.

Delivery Note builder formats use Frappe's shared print renderer.
