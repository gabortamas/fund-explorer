# The answer key for step 3

`kid_fields_gold.csv` holds the correct field values for 5 KIDs, filled in by hand from
the PDFs (not from the parsed text and not from any extractor, so the key does not share
their mistakes). The extractors are scored against it, field by field.

The 5 funds were chosen to differ from each other:

| ISIN | Why it is in the set |
|---|---|
| IE00B4L5Y983 | equity, accumulating, USD |
| IE00B4WXJJ64 | government bonds, distributing, EUR, 3-year holding period |
| IE00BDBRDM35 | bonds, currency-hedged share class |
| IE00B1FZS350 | property, distributing, KID dated September 2026 |
| IE00B0M62S72 | European dividend stocks, EUR, KID dated July 2026 |

How to fill it in (one row per fund; open the PDF from `data/raw/ishares/<ISIN>/`):

* Percentages as numbers in percent: `0.20` for 0.20 %, `-25.1` for -25.1 %.
* Money without separators or currency: `7490` for "7,490 USD".
* "We do not charge an entry fee" and "no performance fee": `0`.
* `distribution`: `accumulating` or `distributing`; `management_style`: `passive` or `active`.
* Leave a cell empty only if the KID really does not state the value.
* `checked_by`: your initials once a row is done; `notes`: anything odd you noticed.

The rules behind these conventions are at the top of `src/fundrag/extract/schema.py`.
