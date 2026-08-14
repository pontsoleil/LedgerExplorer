# LedgerExplorer Handoff

更新日: 2026-08-14 (JST)

## 前回作業の結果

- 最新の公開code baselineは`a188f70e10fdecacf1a9649cabbf0661576f7844`である。
- business document tracing、transaction-document link、cash application、as-of settlement viewがsampleへ追加されている。
- business document management IDは変更しない識別子として扱い、voucher numberや説明文からinvoice関係を推測しない。
- `main`と`origin/main`は記録時点で一致していた。

## 現在の注意事項

- WORK側には主要コードとsampleが存在しないため、WORKだけでは現行baselineの画面確認を実施できない。
- 実会計データ、取引先、口座、カード、請求書データをGitHubへ登録しない。
- Apache AliasはWORK側を参照する想定であり、`htdocs`への無断コピーは行わない。

## 次の作業

1. XBRL-GL-Next、UADC_PoC、LedgerExplorerの連携契約を確定する。
2. UADC_PoCが生成するStructured CSVの列、`semantic_path`、document ID、settlement linkをLedgerExplorer入力へ対応付ける。
3. 公開sampleだけを使う統合fixtureを定義する。
4. WORKへ必要なコードを選択同期した後、local Apacheまたは`server/serve.py`で代表URLを確認する。
5. 月、言語、view、document trace、as-of settlementの回帰条件を記録する。

## 未完了・未確認

- 3プロジェクト間の正式なintegration manifestは未作成。
- XBRL-GL taxonomy QName／HMD `semantic_path`からLedgerExplorer列への正式mappingは未確定。
- 本文書作成時にはブラウザー検証を実施していない。
