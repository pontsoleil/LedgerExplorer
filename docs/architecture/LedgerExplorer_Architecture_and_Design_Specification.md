# LedgerExplorer Reference Application Framework
## Architecture and Design Specification

## 1. Document Status

This document specifies the architecture of the LedgerExplorer reference application framework as implemented and published in the [LedgerExplorer repository](https://github.com/pontsoleil/LedgerExplorer).

- Status: public reference-architecture specification
- Implementation baseline: commit `306d76cc69f149c65a889b4811ee4ebad48ecdeb`
- Baseline date: 2026-09-02
- Relationship to the [README](../../README.md): this document expands the overview and design principles in the README; it does not replace them.
- Relationship to the XBRL GL Next Taxonomy Framework: XBRL GL Next provides a related semantic foundation. LedgerExplorer does not define or implement that framework.
- Relationship to Universal Adapter for Data Conversion (UADC): UADC is an upstream transformation component. LedgerExplorer consumes the resulting representation and does not depend on UADC internals.
- Companion document: [UADC and LedgerExplorer Reference Architecture](UADC_LedgerExplorer_Reference_Architecture.md)

The statements marked **Current implementation** describe behavior traceable to the baseline repository. Statements marked **Architectural capability** or **Planned** describe boundaries or extensions and must not be read as implemented features.

## 2. Purpose and Scope

LedgerExplorer is a reference application framework that consumes semantic-model-based Structured CSV and demonstrates application-independent visualization, validation, and analysis of accounting and transactional data.

It is a technical reference implementation, not a commercial product specification. Its current published form includes:

- a static browser application under `web/`;
- a Python/Tkinter viewer and export pipeline in `tools/ledger_explorer_i18n.py`;
- a no-cache local static server in `server/serve.py`;
- Japanese and English public demonstration datasets under `data/sample/`.

The browser consumes published CSV and JSON files. The Python pipeline can read hierarchical accounting CSV, derive journal, ledger, trial-balance, balance-sheet, and profit-and-loss views, and export monthly files for the browser. Business-document, AR/AP, settlement, and cash-application views use explicit related CSV tables already included in the public sample.

## 3. Design Principles

### 3.1 Application independence

LedgerExplorer is not designed as a PCA-specific, Yayoi-specific, ERP-specific, UBL-specific, or CII-specific viewer. Source-specific parsing and binding occur before LedgerExplorer's consumption boundary. A source application is usable when an upstream process expresses its information in the expected Structured CSV and related-table contract.

### 3.2 Semantic interpretation

The current implementation recognizes stable semantic identifiers such as `JP07a`, `JP08a`, and XBRL GL-derived field codes configured in `tools/parameters*.json`. `tools/JP_LHM.csv` supplies identifier definitions and is used by the Python generation path. The browser also maps known codes to localized display labels.

The current browser does **not** load a taxonomy, resolve taxonomy QNames, read an HMD, or resolve `semantic_path` values at runtime. Those are related semantic-model capabilities and future integration boundaries, not current browser features.

### 3.3 Loose coupling

UADC and LedgerExplorer are independent components. UADC performs conversion and binding; LedgerExplorer performs consumption. Structured CSV is the exchange contract between them. Neither component needs the other's internal programming language, object model, or user interface.

## 4. System Context

Figure 1 shows the context and the responsibility boundary. Source formats are outside LedgerExplorer. UADC is one possible producer; another conforming producer may be substituted.

**Figure 1 — LedgerExplorer system context**

```text
Source Applications
        |
        v
       UADC (or another conforming producer)
        |
        v
Semantic-model-based Structured CSV
        |
        v
 LedgerExplorer
        |
        +-- Visualization
        +-- Navigation and tracing
        +-- Implemented consistency checks
        `-- Analysis and extension points
```

## 5. Entry Points and Deployment

Table 1 maps the externally visible entry points to the current implementation.

**Table 1 — Current entry points**

| Entry point | Current implementation | Purpose |
|---|---|---|
| Static browser UI | `web/index.html` loads `web/app.js` and `web/app.css` | Fetch, filter, render, and trace published datasets |
| Browser bootstrap | `main()` in `web/app.js` | Resolve `data/{sample|full}/index.json`, initialize controls, and call `refresh()` |
| Local static server | `main()` and `Handler` in `server/serve.py` | Serve a selected repository root with CORS and no-cache headers |
| Python CLI | module body in `tools/ledger_explorer_i18n.py` | Load a parameter file; optionally export web CSV; optionally open Tkinter |
| Python processing object | `TidyData` in `tools/ledger_explorer_i18n.py` | Load configuration and CSV inputs and construct accounting views |
| Python export | `export_web_csv()` | Write language- and month-partitioned view files and index metadata |

The documented local browser command is `python server/serve.py --root .`. The static browser has no JavaScript package-manager dependency. The Python processing path depends materially on pandas and NumPy, declared in `tools/requirements.txt`; Tkinter is supplied by the Python environment where available.

## 6. Input Contract

### 6.1 Current browser contract

The browser first resolves an index document from candidate locations below `data/sample` or `data/full`. The index root must be a JSON object with a `views` object. Its `months` array and per-view `path`, `fallback`, and `available` properties determine the CSV URLs. Language-specific files are expected below `ja/` and `en/`.

The current browser directly uses the following classes of input:

- monthly `tidy`, `journal`, `ledger`, and `trial_balance` CSV files;
- period-end `balance_sheet/ALL.csv` and `pnl/ALL.csv` files;
- `index.json` dataset metadata;
- business-document and relationship CSVs under each language's `source/` directory;
- trading-partner master and initial-balance files for AR/AP reports.

Browser CSV parsing accepts comma-separated records, quoted fields, escaped quotes, CRLF or LF, and an optional UTF-8 BOM. The project-authored text convention is UTF-8; existing authoritative inputs and generator outputs may include a BOM and are read with BOM-tolerant logic. Dates used by the current sample are ISO-style `YYYY-MM-DD`; month keys are `YYYY-MM`.

### 6.2 Current Python generation contract

The Python CLI takes a JSON parameter file. The Japanese and English parameter files configure:

- the hierarchical transaction CSV (`e-tax_file_path`);
- an optional separate structural file (`structural_file_path`, falling back to the display file);
- account, opening-balance, tax-category, and trading-partner CSVs;
- the LHM definition CSV;
- balance-sheet and profit-and-loss templates;
- a mapping from business labels to physical Structured CSV columns;
- language, diagnostics, and export options.

`TidyData.csv2dataframe()` reads CSV with `utf-8-sig`, keeps values initially as strings, normalizes configured numeric columns, and derives a `Month` value from the configured voucher-date column. When display and structural files differ, it requires equal row counts and matching `JP07a` and `JP08a` identifiers before selected structural columns are copied.

### 6.3 Required, optional, and unused semantic inputs

Table 2 distinguishes current requirements from adjacent semantic artifacts.

**Table 2 — Semantic and physical input status**

| Input | Current browser | Current Python generator | Status |
|---|---|---|---|
| Structured CSV / derived view CSV | Required | Required source plus generated outputs | Current implementation |
| Dataset `index.json` | Required in server mode | Generated when configured | Current implementation |
| Parameter JSON | Not used | Required | Current implementation |
| LHM CSV (`tools/JP_LHM.csv`) | Not loaded | Required by configured processing path | Current implementation |
| Business-document relationship CSVs | Required for document views; several are optional-load tolerant | Not generated by `export_web_csv()` | Current sample contract |
| xBRL-CSV metadata JSON | Not loaded | Not loaded | Not currently used |
| Taxonomy documents / QName resolution | Not loaded | Not loaded | Not currently used |
| HMD | Not loaded | Not loaded | Planned integration boundary |
| `semantic_path` mapping | Not resolved at runtime | Not resolved by the published export path | Planned integration boundary |
| Executable content in data | Not supported | Not required | Outside the contract |

### 6.4 Identifiers and occurrence information

The hierarchical transaction sample uses `JP07a` for a journal/transaction grouping identifier and `JP08a` for a detail-line identifier. Sparse structural rows and repeat/group indicator columns such as `BS04fb`, `JP05a`, `BS04fc`, and `JP05b` preserve occurrence context. The raw monthly splitter deliberately copies source lines rather than round-tripping them through a DataFrame so that row order and sparse hierarchy remain intact.

Business-document views use explicit immutable `Document_ID` values, transaction and line IDs, open-item IDs, settlement IDs, and cash-application IDs. Relations are joined through dedicated link tables; descriptions and voucher numbers are not treated as substitute document identifiers.

## 7. Internal Architecture

Figure 2 separates the offline Python preparation path from the runtime browser path.

**Figure 2 — Current processing paths**

```text
Parameter JSON + Structured CSV + masters + LHM/templates
        |
        v
 TidyData.csv2dataframe()
        |
        +--> pandas DataFrames / dictionaries
        |        |
        |        +--> journal / ledger / trial balance / BS / PL
        |
        `--> export_web_csv() --> monthly CSV + index metadata
                                      |
                                      v
index.json + published CSV --> fetch/parseCSV --> arrays / maps
                                      |
                                      v
                         filter, validate, trace, render
                                      |
                                      v
                                  browser DOM
```

Table 3 gives traceable implementation mappings. No component names in this table are invented abstractions presented as code symbols.

**Table 3 — Architecture-to-implementation mapping**

| Architecture component | Current implementation |
|---|---|
| Configuration and path resolution | `TidyData.__init__()` |
| Structured CSV and master loading | `TidyData.csv2dataframe()` |
| Raw hierarchy-preserving monthly split | `_write_tidy_csv_by_fiscal_month_raw()` |
| Journal/ledger/summary export | `export_web_csv()`, `_write_df_by_month()` |
| General-ledger construction | `TidyData.general_ledger()` |
| Trial-balance construction | `TidyData.trial_balance_carried_forward()` |
| BS/PL construction | `TidyData.bs_pl()` |
| Dataset discovery | `initDatasetAndPaths()` in `web/app.js` |
| CSV parsing and fetch cache | `parseCSV()`, `fetchCSV()`, `CSV_CACHE` |
| Standard table filtering/rendering | `filterRows()`, `renderTable()`, `refresh()` |
| AR/AP report construction | `buildPartnerReport()`, `refreshPartnerReport()` |
| Business-document object graph | `loadBusinessDocumentModel()`, `recordsFromCsv()` |
| Document-to-journal tracing | `documentJournalRows()`, `relatedDocumentIds()` |

## 8. Data Structures

The Python path uses pandas `DataFrame` objects for normalized transaction rows, general-ledger rows, trial-balance summaries, and statement templates. `TidyData` retains these objects and related dictionaries as instance state. Account directions, account metadata, LHM rows, business partners, and statement output are dictionary-based.

The browser's generic CSV representation is an array of row arrays: the first row is the header, and later rows are data. `recordsFromCsv()` converts relation tables to plain objects keyed by header. `loadBusinessDocumentModel()` builds `Map` indexes for document IDs, open items, settlements, cash applications, journal links, and partner names. These maps are created per model load and consumed by status calculation and detail renderers; the underlying published CSV is not mutated.

Table 4 describes the principal structures and lifecycle.

**Table 4 — Principal data structures**

| Structure | Purpose | Created by | Mutation | Consumer |
|---|---|---|---|---|
| Raw CSV rows | Preserve physical and sparse layout | browser `parseCSV()` or raw Python reader | Filter/render produces separate arrays | generic views and raw tidy view |
| pandas `DataFrame` | Normalize and aggregate accounting rows | `csv2dataframe()` and processing methods | Enriched through conversions, merges, balances | Python GUI and exporter |
| `TidyData.amount_rows` | Journal detail rows with debit/credit amounts | `csv2dataframe()` | Assigned during processing | journal export and GUI |
| `general_ledger_df` | Account-side ledger rows with counterpart and balance | `general_ledger()` | Assigned and localized | ledger export and GUI |
| `summary_df` | Monthly account balances | trial-balance processing | Assigned and localized | trial-balance export and GUI |
| Business-document maps | Explicit relationship graph and cutoff-aware status | `loadBusinessDocumentModel()` | Built from CSV on load | document list/detail and reverse tracing |
| Partner snapshots | AR/AP opening, occurrence, settlement, and balance | `buildPartnerReport()` | Accumulated month by month | receivable/payable views |

## 9. Processing Flow

The following pseudocode reflects the published implementation rather than a target-only design.

```text
resolve paths relative to the parameter JSON
load partner master, LHM, account definitions, statement templates
read Structured CSV as strings
if display and structural files differ:
    require equal rows and matching transaction/line identifiers
normalize configured identifiers, dates, and amount fields
derive journal amount rows
build ledger rows and running balances
build trial balance and period-end statements
if web export requested:
    split raw Structured CSV without DataFrame round-trip
    write derived views by month
    write index metadata when configured
```

At browser runtime:

```text
resolve and validate dataset index
restore language, view, month, as-of month, account, and search state
resolve the requested CSV path
fetch and parse CSV
for a generic view:
    filter rows and render a table
for AR/AP:
    accumulate partner balances and settlements
for business documents:
    load explicit relation tables
    calculate cutoff-aware matching state
    render document, related document, journal, and cash-application details
```

## 10. Visualization Architecture

The static browser renders Structured CSV, journal, general ledger, trial balance, balance sheet, and profit-and-loss tables. It provides language switching, month selection, account filtering, text search for searchable views, column toggles, and URL query-state preservation.

Virtual AR and AP views aggregate ledger rows by trading-partner subaccount and classify settlement amounts by counterpart account. The business-document mode supports sales invoices, purchase invoices, receipts, payments, issuer/recipient parties, line and tax details, related documents, journal links, open items, settlements, and cash applications. Target-month and as-of-month controls support cutoff-aware presentation.

Department, partner, account, debit, credit, tax, and subaccount fields are displayed when present in their respective files. There is no implemented charting subsystem in the baseline. Additional charts are an extension point.

## 11. Validation and Analysis

The current code performs bounded physical and relationship checks rather than a general validation engine:

- dataset index JSON shape and HTTP success checks;
- optional BOM removal and CSV parsing;
- display/structural row-count and transaction/line identifier agreement in the Python path;
- presence of partner master/opening balances for report rows;
- document-to-journal and invoice-to-open-item presence checks;
- invoice/document amount comparison;
- applied, unapplied, partially applied, overapplied, unposted, and mismatch states;
- as-of cutoff filtering for cash applications.

Figure 3 distinguishes implemented checks from architectural extensions.

**Figure 3 — Validation and analysis progression**

```text
Physical parsing and required-file checks       [partly implemented]
        |
        v
Identifier and relationship consistency         [implemented for selected flows]
        |
        v
Accounting consistency and reconciliation       [implemented for selected AR/AP cases]
        |
        v
General semantic-rule validation                 [architectural capability]
        |
        v
Analytical and AI-assisted review                [possible extension]
```

The baseline does not provide a general taxonomy validator, xBRL-CSV validator, rule language, anomaly-detection model, or AI-assisted review engine.

## 12. Error Handling

The browser reports dataset-index resolution failures with the attempted locations, rejects non-object or missing-`views` JSON, and throws on failed required CSV fetches. Optional relationship files are loaded through `fetchOptionalCSV()` and may degrade to empty datasets. Errors caught by `refresh()` are presented in the status area; selected data integrity issues are also logged to the browser console.

The Python path raises ordinary file/JSON/CSV/pandas errors, explicit `ValueError` for display/structural mismatch, and trace warnings when optional statement construction or export cannot be completed. It is not a hardened ingestion sandbox and must not be used to trust arbitrary external data without surrounding controls.

## 13. Technology Independence

The reference implementation uses Python, pandas, NumPy, Tkinter, HTML, CSS, and browser JavaScript. The application architecture and Structured CSV contract are not dependent on a particular programming language, UI framework, database, hosting platform, or accounting software product. A compatible consumer may reproduce the published contract with different technology.

## 14. Extension Points

The following are architectural capabilities, not claims about baseline completion:

- additional table or chart visualizations;
- declarative physical and semantic validation rules;
- taxonomy/HMD/`semantic_path` integration;
- broader accounting consistency and audit procedures;
- AI-assisted analysis with explicit provenance and human review;
- alternative Structured CSV producers, including but not limited to UADC;
- consumers other than LedgerExplorer;
- evidence-package linking, while preserving the distinction between original evidence and derived data.

Extensions should preserve stable identifiers, explicit relationships, the source/conversion/consumption boundary, and backward-compatible URL and file contracts where applicable.

## 15. Security and Trust Boundary

Source data and every derived CSV are external inputs at the LedgerExplorer trust boundary. A `.csv` suffix, a successful parse, or production by UADC is not proof that content is complete, accurate, authorized, or safe.

- Validate expected files, columns, identifiers, amounts, dates, relationships, and size limits before relying on results.
- Treat displayed text as data. The browser uses HTML escaping in generated markup; extensions must retain equivalent output encoding.
- Structured CSV is data, not executable content. Scripts, macros, formulas, and active payloads are outside the contract.
- Keep private accounting data, credentials, local settings, and large/private exports outside public paths.
- Preserve provenance between original evidence, source-system export, conversion result, derived tables, and rendered views.
- Do not infer legal authenticity, evidentiary status, or completeness from visualization alone.

## 16. Long-Term Data Use

LedgerExplorer demonstrates how `Structured CSV + semantic model` can reduce dependence on a particular source software product. Stable identifiers, explicit relationships, documented semantics, ordinary text encodings, and independent consumers support later review and migration.

This architecture does not declare Structured CSV to be a legal original, nor does it replace retention of authoritative source records and evidence. It provides a reusable technical representation and a reference consumer through which data can remain understandable and usable over time.
