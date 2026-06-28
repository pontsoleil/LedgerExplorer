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
