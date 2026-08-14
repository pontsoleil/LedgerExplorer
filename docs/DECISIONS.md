# LedgerExplorer Decisions

更新日: 2026-08-14 (JST)

## 採用済み設計指針

### D-001 Structured CSVを表示入力の境界とする

- LedgerExplorerはStructured CSV／hierarchical tidy dataを表示・exportするconsumerとする。
- XBRL-GL-NextやUADC_PoC固有の生成処理を表示コードへ埋め込まない。

### D-002 公開sampleとprivate dataを分離する

- `data/sample/`には公開可能な小規模sampleだけを置く。
- `data/full/`、実会計データ、ローカル生成物はGit対象外とする。

### D-003 document関係を明示的な表で管理する

- business document IDはimmutableとする。
- invoice、receipt、payment、cash applicationの関係はlink tableで表し、摘要やvoucher numberから推測しない。

### D-004 WORKをlocal preview元とする

- 既存Apache Aliasまたは`server/serve.py`を使用する。
- 通常確認のために別のWeb rootへ複製しない。

### D-005 連携検証ではproducer contractを固定する

- UADC_PoCが生成した公開fixtureのSHA-256とschema／列契約を記録してからLedgerExplorerへ渡す。
- XBRL-GL-Nextの`semantic_path`とLedgerExplorer表示列を同一概念として暗黙統合しない。

## 保留事項

- XBRL-GL-Next／UADC_PoCとのintegration manifest形式。
- HMD occurrenceとLedgerExplorer document／journal viewの正式mapping。
