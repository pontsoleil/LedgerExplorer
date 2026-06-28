# Architecture

The project is intentionally split into three layers:

1. **Generator / exporter (Python)**  
   `tools/ledger_explorer_i18n.py` produces monthly “Structured CSV / hierarchical tidy data”
   plus derived views (ledger/journal/trial balance/BS/PL).

2. **Data layer (CSV/JSON files)**  
   Output is stored as files under `data/`, typically split by month.

3. **Presentation layer (static web UI)**  
   `web/` is a static SPA that loads CSV files via `fetch()` and renders tables.

This design keeps deployment simple:
- static hosting works (GitHub Pages, Nginx, S3+CloudFront)
- data refresh is done by re-generating files and re-deploying
