# Data policy

## Recommended approach

- `data/sample/` : **commit** small, non-sensitive, public demo datasets
- `data/full/`   : do **NOT** commit large exports; keep local/private, or publish separately

Reasons:
- CSV can become very large (monthly splits, ledger lines, etc.)
- Git history is hard to clean once large files are committed
- Better options: Git LFS / GitHub Releases / separate data repository / object storage (S3)

## Expected layout

```
data/
  sample/
    en/2021-04/ledger.csv
    en/2021-04/tidy.csv
    ...
    ja/2021-04/...
  full/
    en/2021-04/...
    ja/2021-04/...
```

Where `<view>.csv` names are matched by `web/app.js`.
Adjust to your real naming conventions as needed.
