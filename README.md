# Ledger Explorer

Ledger Explorer is a static web viewer and export toolkit for accounting ledger
data represented as Structured CSV / hierarchical tidy data.

The repository is prepared for publishing:

- the browser UI under `web/`
- local/static serving helpers under `server/`
- export and bootstrap tooling under `tools/`
- documentation under `docs/`
- small public sample datasets under `data/sample/`

Large or private exports should stay outside Git, or be published separately
with Git LFS, GitHub Releases, a separate data repository, or object storage.

## Repository Layout

```text
.
|-- .github/                  # Pull request template and CI workflow
|-- data/
|   |-- README.md
|   |-- sample/               # Commit small public demo data
|   `-- full/                 # Ignored; local/private generated exports
|-- docs/
|   |-- architecture.md
|   |-- data-layout.md
|   `-- github-pages.md
|-- server/
|   |-- serve.py              # Local no-cache static server
|   `-- nginx/
|-- tools/
|   |-- bootstrap_from_existing.py
|   |-- ledger_explorer_i18n.py
|   `-- requirements.txt
|-- web/
|   |-- index.html
|   |-- app.js
|   |-- app.css
|   `-- i18n/
|-- Dockerfile.tools
|-- Dockerfile.web
|-- docker-compose.yml
|-- LICENSE-CODE
`-- LICENSE-CONTENT
```

## Local Preview

From the repository root:

```bash
python server/serve.py --root .
```

Then open:

```text
http://localhost:8000/web/?view=ledger&month=2021-04&mode=server&lang=ja
```

The web UI defaults to `data/sample`. You can switch datasets with:

```text
?dataset=sample
?dataset=full
```

`data/full` is ignored by Git and is intended for local/private exports.

## Authoritative Sample Inputs and Rebuild

The downstream rebuild starts from the existing Structured CSV files. Recreating
them from the original PCA Accounting export is outside this repository's rebuild
scope.

Authoritative transaction inputs:

- `data/sample/ja/source/tidyGLeTax.csv`
- `data/sample/en/source/tidyGLeTax_en.csv`

Supporting inputs are the language-specific `account_list`, `beginning_balance`,
`tax_category`, and `trading_partner` CSV files under the same `source/`
directories, plus the balance-sheet and profit-and-loss templates under each
language's `e-tax/` directory. `tools/parameters.json` and
`tools/parameters_en.json` resolve these paths relative to the parameter file,
not the process working directory.

The complete LHM used for type definitions is `tools/JP_LHM.csv`. Its expected
SHA-256 is:

```text
9C17F91AF074DEBCCE3D90483C55D05A83CD90327B4BCE1B99685AD25B8774C0
```

The approved 1 April 2021 opening-balance reconstruction treats JPY 84,256,000
of inventory as a fixed input. Accounts payable is calculated from April and May
accounts-payable debits, and cash and deposits is the balancing amount. Scripts,
candidate files, input hashes, formulas, and validation results are kept under
`work/latest-opening-balance/`; the older `work/rebuilt-opening-balance/` results
belong to a separate scenario.

From the repository root, the reproducible commands are:

```powershell
python work/latest-opening-balance/rebuild_latest.py
python work/latest-opening-balance/rebuild_latest.py --apply
python tools/ledger_explorer_i18n.py tools/parameters.json --export-dir work/latest-opening-balance/generated-data --no-gui
python tools/ledger_explorer_i18n.py tools/parameters_en.json --export-dir work/latest-opening-balance/generated-data --no-gui
python work/latest-opening-balance/validate_latest.py
```

The Web UI reads the existing dataset index at `data/sample/index.json`. The
generation parameters disable language-specific indexes with
`write_language_index: false`; `data/sample/ja/index.json` and
`data/sample/en/index.json` are not generated.

## GitHub Pages

For GitHub Pages, publish the repository root or the `web/` entry point together
with `data/sample`. See `docs/github-pages.md` for details.

## What To Commit

Commit:

- `web/`
- `server/`
- `tools/`
- `docs/`
- `.github/`
- small public demo data under `data/sample/`

Do not commit:

- `data/full/`
- `data/out/`
- virtual environments
- generated cache files
- secrets such as `.env`, keys, or certificates

## Licences

- Code: MIT License (`LICENSE-CODE`)
- Content, documentation, screenshots, and sample/exported data: CC BY-SA 4.0
  (`LICENSE-CONTENT`)
