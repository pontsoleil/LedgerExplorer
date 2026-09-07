#!/usr/bin/env python3
"""Delta validator for the public LedgerExplorer 16-month dataset."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path


MONTHS = [
    "2021-02", "2021-03", "2021-04", "2021-05", "2021-06", "2021-07",
    "2021-08", "2021-09", "2021-10", "2021-11", "2021-12", "2022-01",
    "2022-02", "2022-03", "2022-04", "2022-05",
]
REPORTING = MONTHS[2:14]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def semantic_base(path: str) -> str:
    return re.sub(r"\[[^\]]+\]", "", path)


def as_of(open_items: list[dict[str, str]], applications: list[dict[str, str]], cutoff: str) -> dict[str, int]:
    applied = defaultdict(int)
    for row in applications:
        if row["Application_Date"] <= cutoff:
            applied[row["Open_Item_ID"]] += int(row["Applied_Amount"])
    totals = defaultdict(int)
    for item in open_items:
        if item["Recognition_Date"] <= cutoff:
            totals[item["Ledger_Type"]] += int(item["Original_Amount"]) - applied[item["Open_Item_ID"]]
    return dict(totals)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--predecessor", type=Path, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--hmd", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    checks: list[dict[str, object]] = []

    def check(test_id: str, condition: bool, detail: object) -> None:
        require(condition, f"{test_id}: {detail}")
        checks.append({"test_id": test_id, "result": "PASS", "detail": detail})

    index = json.loads((args.dataset / "index.json").read_text(encoding="utf-8"))
    check("MONTH_SET_INDEX", index["months"] == MONTHS, index["months"])
    check("REPORTING_SET_INDEX", index["reporting_months"] == REPORTING, index["reporting_months"])
    for view in ("structured", "tidy", "journal", "ledger"):
        check(f"MONTH_SET_{view.upper()}", index["views"][view]["available"] == MONTHS, len(index["views"][view]["available"]))
    for view in ("trial_balance", "balance_sheet", "pnl"):
        check(f"REPORTING_SET_{view.upper()}", index["views"][view]["available"] == REPORTING, len(index["views"][view]["available"]))

    binding = read_csv(args.binding)
    columns = [row["structured_column"] for row in binding]
    check("CN_CONTIGUOUS", columns == [f"C{i}" for i in range(1, len(columns) + 1)] and len(columns) == 14, columns)
    hmd_paths = {row["semantic_path"] for row in read_csv(args.hmd)}
    unresolved = sorted({semantic_base(row["semantic_path"]) for row in binding} - hmd_paths)
    check("HMD_RESOLUTION", not unresolved, unresolved)
    binding_hash = sha256(args.binding)
    hmd_hash = sha256(args.hmd)
    structured_counts = {}
    for month in MONTHS:
        csv_path = args.dataset / "structured" / f"{month}.csv"
        json_path = args.dataset / "structured" / f"{month}.json"
        rows = read_csv(csv_path)
        structured_counts[month] = len(rows)
        check(f"STRUCTURED_HEADER_{month}", list(rows[0].keys()) == columns if rows else next(csv.reader(csv_path.open(encoding="utf-8"))) == columns, len(rows))
        dates = {row["C1"][:7] for row in rows}
        check(f"STRUCTURED_DATES_{month}", dates == {month}, sorted(dates))
        metadata = json.loads(json_path.read_text(encoding="utf-8"))
        check(f"METADATA_{month}", metadata["schema_id"] == "xbrl-gl-next-ae-cn-v1" and metadata["binding_sha256"] == binding_hash and metadata["hmd_sha256"] == hmd_hash and [r["structured_column"] for r in metadata["columns"]] == columns, metadata["schema_version"])
        for lang in ("ja", "en"):
            for view, date_field in (("journal", "JP07a_GL03_03"), ("ledger", "Transaction_Date"), ("tidy", "month")):
                path = args.dataset / lang / view / f"{month}.csv"
                view_rows = read_csv(path)
                dates = {row[date_field][:7] for row in view_rows if row.get(date_field) and row.get("Ledger_Side") != "Opening"}
                check(f"DATES_{lang}_{view}_{month}", dates == {month}, sorted(dates))
    check("NONEMPTY_ALL_MONTHS", all(value > 0 for value in structured_counts.values()), structured_counts)

    # Reporting statements remain byte-identical to the accepted predecessor.
    compared = 0
    for lang in ("ja", "en"):
        for view in ("trial_balance", "balance_sheet", "pnl"):
            for name in [f"{month}.csv" for month in REPORTING] + ["ALL.csv"]:
                current = args.dataset / lang / view / name
                prior = args.predecessor / lang / view / name
                check(f"REPORT_UNCHANGED_{lang}_{view}_{name}", sha256(current) == sha256(prior), name)
                compared += 1
    check("REPORT_FILE_COUNT", compared == 78, compared)

    sources = args.dataset / "en" / "source"
    documents = read_csv(sources / "business_document.csv")
    open_items = read_csv(sources / "ar_ap_open_item.csv")
    settlements = read_csv(sources / "cash_settlement.csv")
    applications = read_csv(sources / "cash_application.csv")
    journal_links = read_csv(sources / "journal_document_link.csv")
    transaction_links = read_csv(sources / "transaction_document_link.csv")
    doc_ids = [r["Document_ID"] for r in documents]
    open_ids = [r["Open_Item_ID"] for r in open_items]
    settlement_ids = [r["Settlement_ID"] for r in settlements]
    application_ids = [r["Cash_Application_ID"] for r in applications]
    check("UNIQUE_DOCUMENT_IDS", len(doc_ids) == len(set(doc_ids)), len(doc_ids))
    check("UNIQUE_OPEN_IDS", len(open_ids) == len(set(open_ids)), len(open_ids))
    check("UNIQUE_SETTLEMENT_IDS", len(settlement_ids) == len(set(settlement_ids)), len(settlement_ids))
    check("UNIQUE_APPLICATION_IDS", len(application_ids) == len(set(application_ids)), len(application_ids))
    doc_set, open_set, settlement_set = set(doc_ids), set(open_ids), set(settlement_ids)
    check("OPEN_DOCUMENT_FK", all(r["Invoice_Document_ID"] in doc_set for r in open_items), len(open_items))
    check("SETTLEMENT_DOCUMENT_FK", all(r["Settlement_Document_ID"] in doc_set for r in settlements), len(settlements))
    check("APPLICATION_FK", all(r["Open_Item_ID"] in open_set and r["Settlement_ID"] in settlement_set for r in applications), len(applications))
    check("JOURNAL_LINK_FK", all(r["Document_ID"] in doc_set for r in journal_links), len(journal_links))
    check("EXPLICIT_RELATIONS", all(r["relationship"] == "EXPLICIT_APPLICATION" for r in transaction_links), len(transaction_links))
    check("APPLICATION_DATE_ORDER", all(next(i for i in open_items if i["Open_Item_ID"] == r["Open_Item_ID"])["Recognition_Date"] <= r["Application_Date"] for r in applications), len(applications))

    opening = as_of(open_items, applications, "2021-03-31")
    check("OPENING_AR_BRIDGE", opening.get("AR") == 1500000, opening)
    check("OPENING_AP_BRIDGE", opening.get("AP") == 3423720, opening)
    trial = {r["Ledger_Account_Number"]: int(r["Ending_Balance"]) for r in read_csv(args.dataset / "en" / "trial_balance" / "2022-03.csv")}
    year_end = as_of(open_items, applications, "2022-03-31")
    check("YEAR_END_AR_GL", year_end.get("AR") == trial["1200"], {"subledger": year_end.get("AR"), "gl": trial["1200"]})
    check("YEAR_END_AP_GL", year_end.get("AP") == trial["2000"], {"subledger": year_end.get("AP"), "gl": trial["2000"]})
    apr = as_of(open_items, applications, "2022-04-30")
    may = as_of(open_items, applications, "2022-05-31")
    check("CUTOFF_MONOTONIC", apr["AR"] < year_end["AR"] and may["AR"] < apr["AR"] and apr["AP"] < year_end["AP"] and may.get("AP", 0) == 0, {"year_end": year_end, "april": apr, "may": may})
    future_apps = [r for r in applications if r["Application_Date"] > "2022-03-31"]
    check("FUTURE_APPLICATION_EXCLUDED", bool(future_apps) and as_of(open_items, [r for r in applications if r["Application_Date"] <= "2022-03-31"], "2022-03-31") == year_end, len(future_apps))

    def states(cutoff: str, ledger: str) -> set[str]:
        applied = defaultdict(int)
        for app in applications:
            if app["Application_Date"] <= cutoff:
                applied[app["Open_Item_ID"]] += int(app["Applied_Amount"])
        result = set()
        for item in open_items:
            if item["Ledger_Type"] != ledger or item["Recognition_Date"] > cutoff:
                continue
            original, used = int(item["Original_Amount"]), applied[item["Open_Item_ID"]]
            result.add("UNSETTLED" if used == 0 else ("SETTLED" if used == original else "PARTIAL"))
        return result
    state_union = {ledger: set().union(*(states(cutoff, ledger) for cutoff in ("2021-03-31", "2021-04-30", "2022-03-31", "2022-04-30", "2022-05-31"))) for ledger in ("AR", "AP")}
    check("SCENARIO_STATES_AR_AP", all({"UNSETTLED", "PARTIAL", "SETTLED"} <= state_union[ledger] for ledger in ("AR", "AP")), {k: sorted(v) for k, v in state_union.items()})
    check("PRE_TO_FISCAL_RELATION", all(any(link["invoice_month"] in {"2021-02", "2021-03"} and "2021-04" <= link["settlement_month"] <= "2022-03" and link["partner_type"] == p for link in transaction_links) for p in ("C", "S")), "AR/AP")
    check("FISCAL_TO_POST_RELATION", all(any("2021-04" <= link["invoice_month"] <= "2022-03" and link["settlement_month"] in {"2022-04", "2022-05"} and link["partner_type"] == p for link in transaction_links) for p in ("C", "S")), "AR/AP")

    # JA/EN identity and nontranslated numeric/key values.
    for name, keys in {
        "business_document.csv": ["Document_ID", "Document_Date", "Partner_Type", "Partner_Code", "Gross_Amount", "Journal_Transaction_ID", "Journal_Line_ID"],
        "ar_ap_open_item.csv": ["Open_Item_ID", "Ledger_Type", "Invoice_Document_ID", "Original_Amount", "Recognition_Date"],
        "cash_settlement.csv": ["Settlement_ID", "Settlement_Document_ID", "Ledger_Type", "Settlement_Date", "Amount"],
        "cash_application.csv": ["Cash_Application_ID", "Ledger_Type", "Settlement_ID", "Open_Item_ID", "Application_Date", "Applied_Amount"],
    }.items():
        ja, en = read_csv(args.dataset / "ja" / "source" / name), read_csv(args.dataset / "en" / "source" / name)
        check(f"JA_EN_{name}", [[r[k] for k in keys] for r in ja] == [[r[k] for k in keys] for r in en], len(ja))

    # Encoding, confidentiality, and forbidden fallback path scan.
    csv_files = list(args.dataset.rglob("*.csv"))
    check("CSV_NO_BOM", all(path.read_bytes()[:3] != b"\xef\xbb\xbf" for path in csv_files), len(csv_files))
    forbidden = re.compile(r"data/full|docs/(?:Codex|ChatGPT)|backup|private|C:\\\\Users", re.IGNORECASE)
    hits = []
    for path in args.dataset.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".csv", ".json", ".md", ".js", ".html", ".py"}:
            text = path.read_text(encoding="utf-8-sig", errors="replace")
            if forbidden.search(text):
                hits.append(path.relative_to(args.dataset).as_posix())
    check("PUBLIC_FORBIDDEN_REFERENCE_ZERO", not hits, hits)

    output = {
        "result": "PASS", "dataset_id": index["dataset_id"], "checks": checks,
        "summary": {"pass": len(checks), "fail": 0, "months": len(MONTHS), "reporting_months": len(REPORTING), "documents": len(documents), "open_items": len(open_items), "settlements": len(settlements), "applications": len(applications)},
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(output["summary"], sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
