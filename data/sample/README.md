# PCA synthetic FY2021 16-month settlement sample

`pca-synthetic-fy2021-v3-structured-tidy-16m` is a wholly fictional public evaluation dataset.

- Reporting period: 2021-04 through 2022-03.
- Reference-before: 2021-02 and 2021-03. Reference-after: 2022-04 and 2022-05.
- Structured Tidy output: C1...C18 under `structured/`, one HMD class occurrence per row.
- EntryHeader properties occur once; descendants inherit only complete occurrence coordinates.
- Monthly JSON files are xBRL-CSV primary metadata; `structured/columns.json` is separate display metadata.
- Existing `{ja|en}/tidy/` files are unreferenced predecessor diagnostics and are not runtime authority.
- Explicit document, open-item, settlement, application, and journal relations are under `{ja|en}/source/`.
- Reference-period postings are excluded from the twelve-month trial balance, BS, and PL.
