# Data layout

## Month split

Use `YYYY-MM` folders (e.g., `2021-04/`) to keep file sizes manageable and enable
incremental publishing.

## Language split

Use `en/` and `ja/` as top-level splits. If you add more languages, keep them
as ISO language tags (e.g., `fr`, `de`, `zh-Hans`).

## View split

Views are file-based. For example:

- `tidy.csv`          : structured CSV (source of truth)
- `journal.csv`       : book of original entry derived from tidy
- `ledger.csv`        : general ledger derived from tidy
- `trial_balance.csv` : derived summary
- `bs.csv`, `pl.csv`  : period-end statements derived summary

The starter `web/app.js` expects `<view>.csv` naming. If your production naming differs,
align either the filenames or the loader logic.
