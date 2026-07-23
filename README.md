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

These two files are preserved as unchanged source snapshots. The document-linked
demo uses the following derived Structured CSVs:

- `data/sample/ja/source/tidyGLeTax_revised.csv`
- `data/sample/en/source/tidyGLeTax_revised_en.csv`

The derived data keeps the 1 April 2021 opening balances unchanged. It extends
only the transactions needed to demonstrate invoice, receipt, payment, and cash
application relationships across the accounting-period boundary. Existing
descriptions in the authoritative source snapshots are not rewritten.

Each business document has an immutable management ID such as
`DOC-20210430-0001`, consisting of the document date and a daily sequence.
The business document number is stored separately and is populated only when a
number is explicitly available. Accounting voucher numbers are not treated as
invoice numbers. Invoice-to-settlement relationships are recorded in explicit
document-link and cash-application tables rather than inferred from journal
descriptions.

The fiscal selector covers April 2021 through March 2022. Journal and ledger
reference data for February and March 2021 and April and May 2022 is used only
for related-transaction tracing across the fiscal-year boundaries.

The revised sample consolidates the former checking-account subaccount
`1 / Mizuho Bank` into the existing ordinary-deposit subaccount
`20 / Mizuho Head Office` (`20 / みずほ本店` in Japanese). The authoritative
`tidyGLeTax*.csv` snapshots remain unchanged; this conversion is represented in
the derived Structured CSVs. Synthetic receipt and payment entries use
subaccount 20 on their cash-and-deposits side and common department `000`.

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
accounts-payable debits, and cash and deposits is the balancing amount.
Scenario-specific rebuild scripts, candidate files, input hashes, formulas, and
validation logs are development artifacts and are not part of this public
repository. The published repository contains the approved sample inputs,
derived data, and the public export tooling required by the viewer.

The Web UI reads the existing dataset index at `data/sample/index.json`. The
generation parameters disable language-specific indexes with
`write_language_index: false`; `data/sample/ja/index.json` and
`data/sample/en/index.json` are not generated.

## AR/AP Document Tracking Demo

The receivables and payables views provide an end-to-end demonstration from an
invoice-based AR/AP entry through receipt or payment and cash application.
Selecting a trading partner limits both the balance table and related journals
to that partner. Selecting a related journal row opens its supporting document
in a right-side panel; narrow screens use a full-width panel.

The related-document panel can show:

- sales and purchase invoices with document date, issuer, recipient,
  departments, people, document lines, tax-rate totals, and document total
- bank receipt and payment notices with transaction date, amount, and bank
  branch
- the selected journal entry, related invoice, open item, settlement, and cash
  application

The `Accounting ledgers / Business documents` mode switch also supports reverse
tracing from documents to accounting records. The top-right controls use a
target month and an as-of month. The as-of month ranges from the target month
through May 2022 and determines which documents and cash applications are
available at that month end. Balance-sheet and profit-and-loss views are hidden
when the as-of month is earlier than the March 2022 fiscal year end.

The business-document view filters documents by document type, trading partner,
and reconciliation status. For invoices, the list contains the target month's
invoices plus invoices from earlier months that remain unsettled as of the
selected month end. Earlier invoices disappear after full settlement, while
target-month invoices remain visible so that their transition to the matched
state can be confirmed. For example, with June 2021 as the target month, May and
June invoices are shown as unsettled at the end of June; May invoices disappear
after settlement in July, and June invoices remain visible as matched after
settlement in August.

The document list uses the available page height. Selecting a document opens its
issuer and recipient, line items, related documents, journal entries, and cash
applications in a fixed right-side panel. The panel uses approximately one-third
of a wide screen, one-half of a normal desktop screen, and the full width of a
narrow screen.

Invoice status checks the journal link, AR/AP open-item link, agreement between
the invoice and accounting amount, and cash applications up to the as-of month.
Invoices are shown as `Unsettled`, `Partially settled`, or `Matched` as their
remaining balance changes. An invoice is not marked invalid merely because
settlement occurs in a later month. Receipt, payment, and adjustment documents
also retain applied, partially applied, unapplied, and overapplied checks; their
detailed cash applications remain visible from the right-side panel.

Invoice documents contain line and tax information. Receipt and payment notices
do not duplicate invoice lines or consumption-tax details. Document-level tax
is calculated from the net subtotal for each tax rate using the configured
rounding rule; the current demo uses floor rounding.

The accounting period remains April 2021 through March 2022. February and March
2021 and April and May 2022 are reference-only months used to demonstrate
settlement relationships across the fiscal-year boundaries. The initial
related-journal range includes the previous two months, and the range controls
support up to three months where data exists.

The sample uses invoice basis for both AR and AP. AR is recognized on the invoice
issue date. Aggregation is by legal-entity trading-partner ID, while documents
also carry issuer and recipient department and person information.

Generated source tables under each language's `source/` directory include:

- `business_document.csv`: 1,520 documents
- `business_document_party.csv`: 3,040 issuer/recipient records
- `transaction_document_detail.csv`: 2,280 invoice-detail rows
- `ar_ap_open_item.csv`: 760 open items
- `cash_settlement.csv`: 760 receipt/payment events
- `cash_application.csv`: 760 invoice applications
- `journal_document_link.csv`: 1,520 journal/document links
- `transaction_document_link.csv`: 760 invoice/settlement links

Japanese and English datasets share document IDs, dates, account codes, partner
IDs, amounts, and relationships. Only language-specific display strings differ.

The AR/AP document model, journal-origin related-document panel, and
document-origin reverse tracing with mismatch presentation are complete for
this demo. Remaining project work includes standards validation for UBL
bindings, UBL XML and evidence-PDF generation, generic accounting-source
conversion, financial-statement-to-document tracing, a portable audit package,
and AI/OCR-assisted exception review.

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
