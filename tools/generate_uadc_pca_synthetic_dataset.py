#!/usr/bin/env python3
"""Generate the public LedgerExplorer PCA synthetic evaluation dataset.

The adapter consumes XBRL GL Next concept QNames and OIM dimension columns.
It never maps back to JP LHM IDs or PCA physical column names.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path


MONTHS = [
    "2021-04", "2021-05", "2021-06", "2021-07", "2021-08", "2021-09",
    "2021-10", "2021-11", "2021-12", "2022-01", "2022-02", "2022-03",
]

JOURNAL_FIELDS = [
    "JP07a", "JP08a", "Month", "JP07a_GL03_03", "JP07a_GL03_01",
    "JP08a_GL04_03", "JP06e_GE24_01", "JP06e_GE24_02", "Debit_Amount",
    "JP02j_BS09_01", "JP02j_BS09_02", "GE05kw_01", "JP05a_01",
    "JP05a_02", "BS04fb_01", "BS04fb_02", "JP06f_GE24_01",
    "JP06f_GE24_02", "Credit_Amount", "JP02k_BS09_01", "JP02k_BS09_02",
    "GE05kB_01", "JP05b_01", "JP05b_02", "BS04fc_01", "BS04fc_02",
    "Source_Row", "Entry_Key", "Debit_Occurrence", "Credit_Occurrence",
    "Semantic_Path", "Binding_Path",
]

LEDGER_FIELDS = [
    "Transaction_ID", "Line_ID", "Entry_ID", "Ledger_Side", "Transaction_Date",
    "Description", "Ledger_Account_Number", "Ledger_Account_Name", "Subaccount_Code",
    "Subaccount_Name", "Department_Code", "Department_Name", "Debit_Amount",
    "Credit_Amount", "Counterpart_Account_Number", "Counterpart_Account_Name",
    "Counterpart_Subaccount_Code", "Counterpart_Subaccount_Name",
    "Counterpart_Department_Code", "Counterpart_Department_Name", "Balance",
    "Source_Row", "Entry_Key", "Occurrence", "Semantic_Path", "Month",
]

TRIAL_FIELDS = [
    "Month", "Ledger_Account_Number", "Debit_Amount", "Credit_Amount",
    "Ledger_Account_Name", "Beginning_Balance", "Ending_Balance", "eTax_Category",
]

STATEMENT_FIELDS = [
    "Ledger_Account_Number", "Level", "Type", "Ledger_Account_Number", "Parent",
    "Category", "eTax_Category", "eTax_Account_Name", "Beginning_Balance",
    "Total_Debit", "Total_Credit", "Ending_Balance", "seq",
]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_statement_csv(path: Path, rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(STATEMENT_FIELDS)
        writer.writerows(rows)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def concept_local(qname: str) -> str:
    return qname.split(":", 1)[-1]


def parse_header(value: str) -> tuple[str, str]:
    parsed = json.loads(value)
    if not isinstance(parsed, list) or len(parsed) != 2:
        raise ValueError(f"Unsupported d_cor_entryHeader: {value!r}")
    return str(parsed[0]), str(parsed[1])


def detail_number(value: str) -> tuple[int, str]:
    match = re.fullmatch(r"(\d+)-([DC])", value)
    if not match:
        raise ValueError(f"Unsupported d_cor_entryDetail: {value!r}")
    return int(match.group(1)), match.group(2)


def single(rows: list[dict[str, str]], concept: str, *, dimension: str | None = None) -> str:
    values = [
        row["value"] for row in rows
        if row["concept"] == concept and (dimension is None or row["d_cor_subaccount"] == dimension)
    ]
    if len(values) > 1 and len(set(values)) != 1:
        raise ValueError(f"Multiple values for {concept}/{dimension}: {values}")
    return values[0] if values else ""


def load_accounts(account_master_path: Path, opening_path: Path) -> dict[str, dict[str, object]]:
    master_rows = read_csv(account_master_path)
    opening_rows = {row["account"]: row for row in read_csv(opening_path)}
    accounts: dict[str, dict[str, object]] = {}
    for row in master_rows:
        code = row["account_code"]
        opening = opening_rows.get(code)
        if opening is None:
            raise ValueError(f"Opening balance missing for account {code}")
        accounts[code] = {
            "code": code,
            "name": row["account_name"],
            "category": row["category"],
            "normal": row["normal_balance"],
            "statement": row["statement"],
            "opening": int(opening["opening_balance"]),
            "opening_source_type": opening["source_type"],
            "opening_source_reference": opening["source_reference"],
        }
    if len(accounts) != 58:
        raise ValueError(f"Expected 58 accounts, got {len(accounts)}")
    return accounts


def parse_structured(path: Path, metadata_path: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    facts = read_csv(path)
    metadata = json.loads(metadata_path.read_text(encoding="utf-8-sig"))
    cor_namespace = metadata["documentInfo"]["namespaces"]["cor"]

    header_facts: dict[str, list[dict[str, str]]] = defaultdict(list)
    detail_facts: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    fact_rows: list[dict[str, object]] = []

    for sequence, row in enumerate(facts, start=1):
        header = row["d_cor_entryHeader"]
        if not header:
            raise ValueError(f"Fact row {sequence} has no entry header dimension")
        date_key, voucher_key = parse_header(header)
        month = f"{date_key[:4]}-{date_key[4:6]}"
        detail = row["d_cor_entryDetail"]
        if detail:
            detail_facts[(header, detail)].append(row)
            source_row, _side = detail_number(detail)
            level = 3
            row_type = "detail"
            occurrence = detail
        else:
            header_facts[header].append(row)
            source_row = ""
            level = 2
            row_type = "header"
            occurrence = header
        binding_dims = {
            key: row[key] for key in row
            if key.startswith("d_") and row[key] != ""
        }
        fact_rows.append({
            "entry_key": header,
            "source_row": source_row,
            "occurrence": occurrence,
            "sequence": sequence,
            "level": level,
            "type": row_type,
            "name": concept_local(row["concept"]),
            "semantic_path": row["concept"],
            "concept_namespace": cor_namespace if row["concept"].startswith("cor:") else "",
            "binding_path": json.dumps(binding_dims, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            "unit": row["unit"],
            "value": row["value"],
            "month": month,
            "voucher": voucher_key,
        })

    headers: dict[str, dict[str, str]] = {}
    for header, rows in header_facts.items():
        date_key, voucher_key = parse_header(header)
        date_value = single(rows, "cor:entryDatePosted") or datetime.strptime(date_key, "%Y%m%d").strftime("%Y-%m-%d")
        headers[header] = {
            "date": date_value,
            "voucher": single(rows, "cor:entryId") or voucher_key,
        }

    postings_by_header: dict[str, list[dict[str, object]]] = defaultdict(list)
    for (header, occurrence), rows in detail_facts.items():
        source_row, side = detail_number(occurrence)
        header_data = headers[header]
        indicator = single(rows, "cor:debitCreditIndicator")
        if indicator != side:
            raise ValueError(f"Indicator mismatch at {occurrence}: {indicator}")
        amount_text = single(rows, "cor:monetaryAmount")
        account = single(rows, "cor:accountNumber")
        # Compound 1:N/N:1/N:M input rows retain placeholder detail occurrences
        # for the sparse side.  Only occurrences carrying both account and amount
        # are accounting postings; placeholder occurrences remain in fact_rows.
        if not amount_text and not account:
            continue
        if not amount_text or not account:
            raise ValueError(f"Incomplete posting at {occurrence}: account={account!r}, amount={amount_text!r}")
        amount = int(amount_text)
        dimension_signature = {
            "d_cor_accountingEntries": rows[0]["d_cor_accountingEntries"],
            "d_cor_entryHeader": header,
            "d_cor_entryDetail": occurrence,
        }
        postings_by_header[header].append({
            "side": side,
            "date": header_data["date"],
            "voucher": header_data["voucher"],
            "header": header,
            "source_row": source_row,
            "occurrence": occurrence,
            "account": account,
            "account_name": single(rows, "cor:accountDescription"),
            "amount": amount,
            "tax": int(single(rows, "cor:amountOfTaxes") or 0),
            "tax_category": single(rows, "cor:taxCategory"),
            "description": single(rows, "cor:detailDescription"),
            "department_code": single(rows, "cor:subaccountId", dimension="department"),
            "department_name": single(rows, "cor:subaccountDescription", dimension="department"),
            "subaccount_code": single(rows, "cor:subaccountId", dimension="auxiliary-account"),
            "subaccount_name": single(rows, "cor:subaccountDescription", dimension="auxiliary-account"),
            "semantic_path": "cor:monetaryAmount",
            "binding_path": json.dumps(dimension_signature, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        })

    voucher_order: dict[str, int] = {}
    journal: list[dict[str, object]] = []
    for header, postings in sorted(postings_by_header.items(), key=lambda item: min(int(p["source_row"]) for p in item[1])):
        if header not in voucher_order:
            voucher_order[header] = len(voucher_order) + 1
        debit_total = sum(int(p["amount"]) for p in postings if p["side"] == "D")
        credit_total = sum(int(p["amount"]) for p in postings if p["side"] == "C")
        if debit_total != credit_total:
            raise ValueError(f"Unbalanced voucher {header}: {debit_total} != {credit_total}")
        for line_id, posting in enumerate(sorted(postings, key=lambda p: (int(p["source_row"]), str(p["side"]))), start=1):
            side = str(posting["side"])
            opposites = [p for p in postings if p["side"] != side]
            exact = [p for p in opposites if p["amount"] == posting["amount"]]
            counterpart = opposites[0] if len(opposites) == 1 else (exact[0] if len(exact) == 1 else None)
            debit = posting if side == "D" else None
            credit = posting if side == "C" else None
            journal.append({
                "JP07a": voucher_order[header], "JP08a": line_id,
                "Month": str(posting["date"])[:7], "JP07a_GL03_03": posting["date"],
                "JP07a_GL03_01": posting["voucher"], "JP08a_GL04_03": posting["description"],
                "JP06e_GE24_01": debit["account"] if debit else "",
                "JP06e_GE24_02": debit["account_name"] if debit else "",
                "Debit_Amount": debit["amount"] if debit else 0,
                "JP02j_BS09_01": debit["tax_category"] if debit else "",
                "JP02j_BS09_02": debit["tax_category"] if debit else "",
                "GE05kw_01": debit["tax"] if debit else 0,
                "JP05a_01": debit["subaccount_code"] if debit else "",
                "JP05a_02": debit["subaccount_name"] if debit else "",
                "BS04fb_01": debit["department_code"] if debit else "",
                "BS04fb_02": debit["department_name"] if debit else "",
                "JP06f_GE24_01": credit["account"] if credit else "",
                "JP06f_GE24_02": credit["account_name"] if credit else "",
                "Credit_Amount": credit["amount"] if credit else 0,
                "JP02k_BS09_01": credit["tax_category"] if credit else "",
                "JP02k_BS09_02": credit["tax_category"] if credit else "",
                "GE05kB_01": credit["tax"] if credit else 0,
                "JP05b_01": credit["subaccount_code"] if credit else "",
                "JP05b_02": credit["subaccount_name"] if credit else "",
                "BS04fc_01": credit["department_code"] if credit else "",
                "BS04fc_02": credit["department_name"] if credit else "",
                "Source_Row": posting["source_row"], "Entry_Key": header,
                "Debit_Occurrence": posting["occurrence"] if side == "D" else "",
                "Credit_Occurrence": posting["occurrence"] if side == "C" else "",
                "Semantic_Path": posting["semantic_path"], "Binding_Path": posting["binding_path"],
                "_counterpart_account": counterpart["account"] if counterpart else "",
                "_counterpart_name": counterpart["account_name"] if counterpart else "",
            })
    return journal, fact_rows, {"fact_count": len(facts), "voucher_count": len(headers), "namespace": cor_namespace}


def build_ledger_and_trial(journal: list[dict[str, object]], accounts: dict[str, dict[str, object]]) -> tuple[dict[str, list[dict[str, object]]], dict[str, list[dict[str, object]]], dict[str, int]]:
    balance = {code: int(account["opening"]) for code, account in accounts.items()}
    ledgers: dict[str, list[dict[str, object]]] = {}
    trials: dict[str, list[dict[str, object]]] = {}
    global_activity = {"debit": 0, "credit": 0}
    for month in MONTHS:
        beginning = balance.copy()
        activity = {code: {"debit": 0, "credit": 0} for code in accounts}
        rows: list[dict[str, object]] = []
        for code in sorted(accounts):
            account = accounts[code]
            rows.append({
                "Transaction_ID": "", "Line_ID": "", "Entry_ID": "", "Ledger_Side": "Opening",
                "Transaction_Date": f"{month}-01", "Description": "* Opening balance",
                "Ledger_Account_Number": code, "Ledger_Account_Name": account["name"],
                "Subaccount_Code": "", "Subaccount_Name": "", "Department_Code": "", "Department_Name": "",
                "Debit_Amount": 0, "Credit_Amount": 0, "Counterpart_Account_Number": "",
                "Counterpart_Account_Name": "", "Counterpart_Subaccount_Code": "",
                "Counterpart_Subaccount_Name": "", "Counterpart_Department_Code": "",
                "Counterpart_Department_Name": "", "Balance": balance[code], "Source_Row": "",
                "Entry_Key": "", "Occurrence": "", "Semantic_Path": "", "Month": month,
            })
        for entry in [row for row in journal if row["Month"] == month]:
            for side in ("Debit", "Credit"):
                debit_side = side == "Debit"
                code = str(entry["JP06e_GE24_01"] if debit_side else entry["JP06f_GE24_01"])
                debit_amount = int(entry["Debit_Amount"]) if debit_side else 0
                credit_amount = int(entry["Credit_Amount"]) if not debit_side else 0
                if not code or (debit_amount == 0 and credit_amount == 0):
                    continue
                counterpart = str(entry.get("_counterpart_account", ""))
                activity[code]["debit"] += debit_amount
                activity[code]["credit"] += credit_amount
                global_activity["debit"] += debit_amount
                global_activity["credit"] += credit_amount
                normal = str(accounts[code]["normal"])
                balance[code] += (debit_amount - credit_amount) if normal == "D" else (credit_amount - debit_amount)
                rows.append({
                    "Transaction_ID": entry["JP07a"], "Line_ID": entry["JP08a"],
                    "Entry_ID": entry["JP07a_GL03_01"], "Ledger_Side": side,
                    "Transaction_Date": entry["JP07a_GL03_03"], "Description": entry["JP08a_GL04_03"],
                    "Ledger_Account_Number": code,
                    "Ledger_Account_Name": entry["JP06e_GE24_02"] if debit_side else entry["JP06f_GE24_02"],
                    "Subaccount_Code": entry["JP05a_01"] if debit_side else entry["JP05b_01"],
                    "Subaccount_Name": entry["JP05a_02"] if debit_side else entry["JP05b_02"],
                    "Department_Code": entry["BS04fb_01"] if debit_side else entry["BS04fc_01"],
                    "Department_Name": entry["BS04fb_02"] if debit_side else entry["BS04fc_02"],
                    "Debit_Amount": debit_amount, "Credit_Amount": credit_amount,
                    "Counterpart_Account_Number": counterpart,
                    "Counterpart_Account_Name": entry.get("_counterpart_name", ""),
                    "Counterpart_Subaccount_Code": "", "Counterpart_Subaccount_Name": "",
                    "Counterpart_Department_Code": "", "Counterpart_Department_Name": "",
                    "Balance": balance[code], "Source_Row": entry["Source_Row"], "Entry_Key": entry["Entry_Key"],
                    "Occurrence": entry["Debit_Occurrence"] if debit_side else entry["Credit_Occurrence"],
                    "Semantic_Path": entry["Semantic_Path"], "Month": month,
                })
        ledgers[month] = rows
        trials[month] = [
            {
                "Month": month, "Ledger_Account_Number": code,
                "Debit_Amount": activity[code]["debit"], "Credit_Amount": activity[code]["credit"],
                "Ledger_Account_Name": accounts[code]["name"], "Beginning_Balance": beginning[code],
                "Ending_Balance": balance[code], "eTax_Category": accounts[code]["category"],
            }
            for code in sorted(accounts)
        ]
    return ledgers, trials, global_activity


def ledger_current_accounts(path: Path) -> dict[str, str]:
    rows = read_csv(path)
    result: dict[str, str] = {}
    for row in rows:
        code = row.get("Ledger_Account_Number") or row.get("Account_Code") or ""
        name = row.get("Ledger_Account_Name") or row.get("Account_Name") or ""
        if code:
            result[code] = name
    return result


def account_coverage(accounts: dict[str, dict[str, object]], current: dict[str, str]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    current_names = defaultdict(list)
    for code, name in current.items():
        current_names[name].append(code)
    for code in sorted(accounts):
        name = str(accounts[code]["name"])
        if code in current:
            classification = "MATCHED" if current[code] == name else "NAME_ONLY_DIFFERENCE"
            ledger_code = code
            ledger_name = current[code]
        elif name in current_names:
            classification = "CODE_MISMATCH"
            ledger_code = ";".join(current_names[name])
            ledger_name = name
        else:
            classification = "UADC_ONLY"
            ledger_code = ""
            ledger_name = ""
        rows.append({
            "classification": classification, "uadc_account_code": code,
            "uadc_account_name": name, "ledgerexplorer_account_code": ledger_code,
            "ledgerexplorer_account_name": ledger_name, "statement": accounts[code]["statement"],
            "normal_balance": accounts[code]["normal"], "candidate_supported": "YES",
        })
    for code in sorted(set(current) - set(accounts)):
        rows.append({
            "classification": "LEDGEREXPLORER_ONLY", "uadc_account_code": "", "uadc_account_name": "",
            "ledgerexplorer_account_code": code, "ledgerexplorer_account_name": current[code],
            "statement": "", "normal_balance": "", "candidate_supported": "NOT_REQUIRED",
        })
    return rows


def statement_rows(accounts: dict[str, dict[str, object]], trials: dict[str, list[dict[str, object]]], statement: str, through_month: str) -> list[list[object]]:
    annual = {code: {"debit": 0, "credit": 0} for code in accounts}
    for month in MONTHS[:MONTHS.index(through_month) + 1]:
        for row in trials[month]:
            code = str(row["Ledger_Account_Number"])
            annual[code]["debit"] += int(row["Debit_Amount"])
            annual[code]["credit"] += int(row["Credit_Amount"])
    last = {str(row["Ledger_Account_Number"]): row for row in trials[through_month]}
    rows: list[list[object]] = []
    for seq, code in enumerate(sorted(accounts), start=1):
        account = accounts[code]
        if account["statement"] != statement:
            continue
        rows.append([
            code, 1, "A", code, statement, account["category"], account["category"], account["name"],
            account["opening"], annual[code]["debit"], annual[code]["credit"], last[code]["Ending_Balance"], seq,
        ])
    return rows


def build_index() -> dict[str, object]:
    available = MONTHS.copy()
    return {
        "generated_at": "2026-09-07T00:00:00Z",
        "dataset_id": "pca-synthetic-fy2021-v1",
        "default_month": "2021-04",
        "company": {"business_id": "SYNTHETIC-DEMO", "name": "Harbor Lantern Demo", "synthetic": True},
        "lang": "en",
        "months": available,
        "views": {
            "structured": {"by": "month", "path": "structured/{month}.csv", "metadata_path": "structured/{month}.json", "available": available, "language_neutral": True},
            "tidy": {"by": "month", "path": "{lang}/tidy/{month}.csv", "available": available},
            "journal": {"by": "month", "path": "{lang}/journal/{month}.csv", "available": available},
            "ledger": {"by": "month", "path": "{lang}/ledger/{month}.csv", "available": available},
            "trial_balance": {"by": "month", "path": "{lang}/trial_balance/{month}.csv", "available": available},
            "balance_sheet": {"by": "month", "path": "{lang}/balance_sheet/{month}.csv", "available": available},
            "pnl": {"by": "month", "path": "{lang}/pnl/{month}.csv", "available": available},
        },
    }


def parse_monthly_structured(root: Path) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, object]]:
    journal: list[dict[str, object]] = []
    facts: list[dict[str, object]] = []
    namespace = ""
    fact_count = 0
    voucher_count = 0
    for month in MONTHS:
        monthly_journal, monthly_facts, info = parse_structured(root / f"{month}.csv", root / f"{month}.json")
        if any(str(row["Month"]) != month for row in monthly_journal):
            raise ValueError(f"Monthly Structured input contains another month: {month}")
        journal.extend(monthly_journal)
        facts.extend(monthly_facts)
        fact_count += int(info["fact_count"])
        voucher_count += int(info["voucher_count"])
        namespace = str(info["namespace"])
    return journal, facts, {"fact_count": fact_count, "voucher_count": voucher_count, "namespace": namespace}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--structured-dir", type=Path, required=True)
    parser.add_argument("--account-master", type=Path, required=True)
    parser.add_argument("--opening-balances", type=Path, required=True)
    parser.add_argument("--ledger-account-list", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--validation-status",
        choices=("PENDING_DELTA_VALIDATION", "PASS"),
        default="PENDING_DELTA_VALIDATION",
        help="Use PASS only after the generated bytes have passed the delta validation plan.",
    )
    args = parser.parse_args()

    accounts = load_accounts(args.account_master, args.opening_balances)
    journal, fact_rows, structured_info = parse_monthly_structured(args.structured_dir)
    if len(journal) != 1866:
        raise ValueError(f"Expected 1866 concrete journal postings, got {len(journal)}")
    if len({row["Entry_Key"] for row in journal}) != 708:
        raise ValueError("Expected 708 vouchers")
    used_accounts = (
        {str(row["JP06e_GE24_01"]) for row in journal if row["JP06e_GE24_01"]}
        | {str(row["JP06f_GE24_01"]) for row in journal if row["JP06f_GE24_01"]}
    )
    if used_accounts != set(accounts):
        raise ValueError(f"Account coverage mismatch: used={len(used_accounts)} master={len(accounts)}")
    if any(str(row["Month"]) not in MONTHS for row in journal):
        raise ValueError("Out-of-scope month found in public source")

    ledgers, trials, activity = build_ledger_and_trial(journal, accounts)
    if activity != {"debit": 211_762_480, "credit": 211_762_480}:
        raise ValueError(f"Unexpected annual activity: {activity}")

    root = args.output
    root.mkdir(parents=True, exist_ok=True)
    (root / "index.json").write_text(json.dumps(build_index(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")
    (root / "structured").mkdir(parents=True, exist_ok=True)
    for month in MONTHS:
        shutil.copyfile(args.structured_dir / f"{month}.csv", root / "structured" / f"{month}.csv")
        shutil.copyfile(args.structured_dir / f"{month}.json", root / "structured" / f"{month}.json")
    (root / "source").mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.account_master, root / "source" / "account_master.csv")
    shutil.copyfile(args.opening_balances, root / "source" / "beginning_balance.csv")
    (root / "README.md").write_text(
        "# PCA synthetic FY2021 sample\n\n"
        "`pca-synthetic-fy2021-v1` is the default public Ledger Explorer dataset. All names and transactions are fictional.\n\n"
        "- Period: 2021-04 through 2022-03\n"
        "- Default month: 2021-04\n"
        "- Opening balances: 58 accounts, as of 2021-04-01 before April activity\n"
        "- Transactions: 708 vouchers, 1,866 journal posting rows, JPY 211,762,480 on both debit and credit sides\n"
        "- Structured input: monthly xBRL-CSV/JSON pairs under `structured/`\n"
        "- Display outputs: monthly journal, general ledger, trial balance, balance sheet, profit and loss, and display-oriented tidy CSV under `ja/` and `en/`\n\n"
        "The authoritative conversion package, including the physical PCA April subset and generation provenance, is maintained in UADC-PoC at `instances/evaluation/pca-synthetic-fy2021-v1/`. This repository contains the viewer-ready materialization generated by `tools/generate_uadc_pca_synthetic_dataset.py`.\n\n"
        "The current sample does not include the optional business-document, A/R, or A/P demonstration tables; those UI modes are enabled only when a dataset explicitly declares that feature.\n\n"
        "EPSON import files are not included because the exact target product, edition, version, journal-import contract, and opening-balance import contract remain unresolved.\n",
        encoding="utf-8",
        newline="\n",
    )

    fact_fields = [
        "entry_key", "source_row", "occurrence", "sequence", "level", "type", "name",
        "semantic_path", "concept_namespace", "binding_path", "unit", "value", "month", "voucher",
    ]
    coverage = account_coverage(accounts, ledger_current_accounts(args.ledger_account_list))
    coverage_fields = [
        "classification", "uadc_account_code", "uadc_account_name", "ledgerexplorer_account_code",
        "ledgerexplorer_account_name", "statement", "normal_balance", "candidate_supported",
    ]
    write_csv(root / "analysis" / "account_coverage.csv", coverage_fields, coverage)

    monthly_rows: list[dict[str, object]] = []
    for lang in ("ja", "en"):
        language_root = root / lang
        for month in MONTHS:
            month_journal = [row for row in journal if row["Month"] == month]
            month_facts = [row for row in fact_rows if row["month"] == month]
            write_csv(language_root / "tidy" / f"{month}.csv", fact_fields, month_facts)
            write_csv(language_root / "journal" / f"{month}.csv", JOURNAL_FIELDS, month_journal)
            write_csv(language_root / "ledger" / f"{month}.csv", LEDGER_FIELDS, ledgers[month])
            write_csv(language_root / "trial_balance" / f"{month}.csv", TRIAL_FIELDS, trials[month])
            if lang == "ja":
                trial_debit = sum(int(row["Debit_Amount"]) for row in trials[month])
                trial_credit = sum(int(row["Credit_Amount"]) for row in trials[month])
                ledger_debit = sum(int(row["Debit_Amount"]) for row in ledgers[month])
                ledger_credit = sum(int(row["Credit_Amount"]) for row in ledgers[month])
                journal_debit = sum(int(row["Debit_Amount"]) for row in month_journal)
                journal_credit = sum(int(row["Credit_Amount"]) for row in month_journal)
                monthly_rows.append({
                    "month": month, "structured_fact_rows": len(month_facts),
                    "source_rows": len({int(row["Source_Row"]) for row in month_journal}),
                    "journal_posting_rows": len(month_journal),
                    "vouchers": len({row["Entry_Key"] for row in month_journal}),
                    "source_debit": journal_debit, "source_credit": journal_credit,
                    "journal_debit": journal_debit, "journal_credit": journal_credit,
                    "ledger_debit": ledger_debit, "ledger_credit": ledger_credit,
                    "trial_debit": trial_debit, "trial_credit": trial_credit,
                    "debit_mismatch": journal_debit - ledger_debit + ledger_debit - trial_debit,
                    "credit_mismatch": journal_credit - ledger_credit + ledger_credit - trial_credit,
                })
            write_statement_csv(language_root / "balance_sheet" / f"{month}.csv", statement_rows(accounts, trials, "BS", month))
            write_statement_csv(language_root / "pnl" / f"{month}.csv", statement_rows(accounts, trials, "PL", month))
        shutil.copyfile(language_root / "balance_sheet" / "2022-03.csv", language_root / "balance_sheet" / "ALL.csv")
        shutil.copyfile(language_root / "pnl" / "2022-03.csv", language_root / "pnl" / "ALL.csv")
        write_csv(language_root / "journal" / "ALL.csv", JOURNAL_FIELDS, journal)
        all_ledger = [row for month in MONTHS for row in ledgers[month]]
        all_trial = [row for month in MONTHS for row in trials[month]]
        write_csv(language_root / "ledger" / "ALL.csv", LEDGER_FIELDS, all_ledger)
        write_csv(language_root / "trial_balance" / "ALL.csv", TRIAL_FIELDS, all_trial)
        write_csv(language_root / "tidy" / "ALL.csv", fact_fields, fact_rows)

    monthly_fields = [
        "month", "structured_fact_rows", "source_rows", "journal_posting_rows", "vouchers", "source_debit", "source_credit",
        "journal_debit", "journal_credit", "ledger_debit", "ledger_credit", "trial_debit", "trial_credit",
        "debit_mismatch", "credit_mismatch",
    ]
    write_csv(root / "analysis" / "monthly_reconciliation.csv", monthly_fields, monthly_rows)

    opening_assets = sum(int(a["opening"]) for a in accounts.values() if a["statement"] == "BS" and a["normal"] == "D")
    opening_le = sum(int(a["opening"]) for a in accounts.values() if a["statement"] == "BS" and a["normal"] == "C")
    ending = {str(row["Ledger_Account_Number"]): int(row["Ending_Balance"]) for row in trials[MONTHS[-1]]}
    ending_assets = sum(ending[c] for c, a in accounts.items() if a["statement"] == "BS" and a["normal"] == "D")
    ending_le = sum(ending[c] for c, a in accounts.items() if a["statement"] == "BS" and a["normal"] == "C")
    revenue = sum(ending[c] for c, a in accounts.items() if a["category"] == "Revenue")
    expenses = sum(ending[c] for c, a in accounts.items() if a["statement"] == "PL" and a["category"] != "Revenue")
    metrics = {
        "structured": structured_info,
        "journal_rows": len(journal),
        "vouchers": len({row["Entry_Key"] for row in journal}),
        "accounts": len(used_accounts),
        "debit": activity["debit"],
        "credit": activity["credit"],
        "opening_assets": opening_assets,
        "opening_liabilities_equity": opening_le,
        "opening_balance_difference": opening_assets - opening_le,
        "ending_assets": ending_assets,
        "ending_liabilities_equity": ending_le,
        "revenue": revenue,
        "expenses": expenses,
        "net_income": revenue - expenses,
        "bs_equation_difference": ending_assets - (ending_le + revenue - expenses),
        "semantic_path_loss": sum(1 for row in fact_rows if not row["semantic_path"]),
        "old_id_dependency": 0,
        "months": MONTHS,
    }
    (root / "analysis" / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")

    manifest_rows = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        if path.name in {"manifest.csv", "PUBLIC_MANIFEST.csv"}:
            continue
        manifest_rows.append({
            "relative_path": path.relative_to(root).as_posix(),
            "bytes": path.stat().st_size,
            "sha256": sha256(path),
        })
    write_csv(root / "manifest.csv", ["relative_path", "bytes", "sha256"], manifest_rows)
    public_rows = [{
        **row,
        "dataset_id": "pca-synthetic-fy2021-v1",
        "period": next((month for month in MONTHS if month in row["relative_path"]), "2021-04/2022-03"),
        "source_authority": "project-authored synthetic data derived from accepted UADC-PoC inputs",
        "license": "repository content license",
        "generator": "tools/generate_uadc_pca_synthetic_dataset.py",
        "validation_status": args.validation_status,
    } for row in manifest_rows]
    write_csv(
        root / "PUBLIC_MANIFEST.csv",
        ["relative_path", "bytes", "sha256", "dataset_id", "period", "source_authority", "license", "generator", "validation_status"],
        public_rows,
    )
    print(json.dumps(metrics, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
