# GitHub Pages publishing (static)

## Option A: Pages from `/docs` or `/web`

- GitHub Settings → Pages
- Source: Deploy from a branch
- Folder: `/web` (recommended)

Ensure your app loads data with relative paths like `../data/...`.

## Data size considerations

GitHub Pages has size constraints; large CSV exports should not be placed in the Pages
artifact. Recommended options:

- keep only small demo data in this repo
- publish big datasets via GitHub Releases (zipped) and have the UI download on demand
- use a separate data repo
- host data in S3 (public read) and point the UI to that base URL
