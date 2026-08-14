# LedgerExplorer Current Baseline

記録日: 2026-08-14 (JST)

## Git baseline

- branch: `main`
- upstream: `origin/main`
- code baseline commit: `a188f70e10fdecacf1a9649cabbf0661576f7844`
- subject: `Add business document tracing and as-of settlement views`
- 記録時点のahead/behind: `0/0`
- 本文書を追加するdocumentation commitは自己参照を避けるため固定値として記録しない。最新値は`git rev-parse HEAD`で確認する。

## 正式入力

公開sampleの起点は次のStructured CSVである。WORK側には記録時点で主要コード・sampleが存在しないため、以下のSHA-256はGIT baselineから取得した。

|相対パス|用途|SHA-256|
|---|---|---|
|`data/sample/ja/source/tidyGLeTax.csv`|日本語authoritative transaction sample|`B2D8F03849EFBC51AC45E28BA49E69DE3698FA229EA9FC15163EFBF9964249E4`|
|`data/sample/en/source/tidyGLeTax_en.csv`|英語authoritative transaction sample|`0AFF39839EFB0B2D3EF9836EB466C99C88E3B8DCB997D5F1CBAC839340CA4A75`|
|`data/sample/ja/source/business_document.csv`|business document sample|`DEFCEEC92260E6B26EB86EEE2703CA0297DCBB07426C676AE2AC10DB9F1A44BB`|
|`data/sample/ja/source/cash_application.csv`|cash application sample|`C00BB4338D91590BB505D2471692BF0DC8BEF462B1B385BE6BEE6042D1661235`|

`data/full/`及び実会計データは正式公開入力ではない。

## 正式成果物

- `web/`: Structured CSVを表示するstatic web viewer
- `data/sample/`: 公開可能な小規模sampleと派生view
- `server/serve.py`: local preview helper
- `tools/`: sample再構築・国際化支援

|相対パス|SHA-256|
|---|---|
|`README.md`|`CA96FDB8919424D9B13707A63E1E324B1C181FF048530A978E6EB89068D6480F`|
|`web/index.html`|`DA1A32585B905C17854DF381D66A5902F0580EB4C5A9AFA028287568B32B256A`|
|`web/app.js`|`886701413080ED72B7B43E85C7695F17DDA0938854C9FF79AB2FCE1898996F7E`|

## 検証状態とWORK差分

- baseline commitの追跡ファイル数: 185
- 記録作業ではコード、sample、画面テストを再実行していない。
- WORK側はGit checkoutではなく、主要コードとsampleが不足している。連携検証前にGITからの選択同期計画を別途確定する。
- private data、`data/full/`、ローカル設定はGITへ同期しない。
