#!/usr/bin/env python3
"""Build the public 16-month LedgerExplorer settlement evaluation dataset.

The current public FY2021 sample supplies the twelve reporting-month amounts.
This generator adds independently authored reference-month transactions, creates
explicit document/open-item/application relations, and materializes the latest
Structured Cn output Binding without copying values from historical fixtures.
"""

from __future__ import annotations

import argparse
import calendar
import csv
import hashlib
import json
import shutil
from collections import defaultdict, deque
from datetime import date
from pathlib import Path


REPORTING_MONTHS = [
    "2021-04", "2021-05", "2021-06", "2021-07", "2021-08", "2021-09",
    "2021-10", "2021-11", "2021-12", "2022-01", "2022-02", "2022-03",
]
REFERENCE_BEFORE = ["2021-02", "2021-03"]
REFERENCE_AFTER = ["2022-04", "2022-05"]
MONTHS = REFERENCE_BEFORE + REPORTING_MONTHS + REFERENCE_AFTER
DATASET_ID = "pca-synthetic-fy2021-v3-structured-tidy-16m"
SCHEMA_ID = "xbrl-gl-next-ae-structured-tidy-v2"
SCHEMA_VERSION = "2.0.0"

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
STRUCTURED_FIELDS = [f"C{i}" for i in range(1, 19)]

BUSINESS_DOCUMENT_FIELDS = [
    "Document_ID", "Document_Number", "Document_Type_Code", "Document_Type_Name",
    "Document_Date", "Recognition_Date", "Recognition_Basis", "Partner_Type",
    "Partner_Code", "Currency", "Gross_Amount", "Journal_Transaction_ID",
    "Journal_Line_ID", "Status", "Source",
]
BUSINESS_PARTY_FIELDS = [
    "Document_Party_Role_ID", "Document_ID", "Role_Code", "Party_ID", "Party_Name",
    "Department_Name", "Person_Name", "Position_Name", "Contact", "Snapshot_Basis",
]
DOCUMENT_DETAIL_FIELDS = [
    "Document_ID", "Document_Number", "Document_Type", "Partner_Type", "Partner_Code",
    "Document_Month", "Transaction_ID", "Line_ID", "Line_Number", "Item_Code",
    "Item_Description", "Quantity", "Unit", "Unit_Price", "Net_Amount", "Tax_Category",
    "Tax_Rate", "Tax_Amount", "Gross_Amount", "Tax_Rounding", "Amount_Basis",
]
OPEN_ITEM_FIELDS = [
    "Open_Item_ID", "Ledger_Type", "Invoice_Document_ID", "Invoice_Document_Number",
    "Partner_Type", "Partner_Code", "Recognition_Date", "Due_Date", "Original_Amount",
    "Applied_Amount", "Open_Amount", "Currency", "Recognition_Basis",
    "Journal_Transaction_ID", "Journal_Line_ID", "Status",
]
SETTLEMENT_FIELDS = [
    "Settlement_ID", "Settlement_Document_ID", "Settlement_Document_Number", "Ledger_Type",
    "Partner_Type", "Partner_Code", "Settlement_Date", "Settlement_Class", "Amount",
    "Currency", "Bank_Account_Code", "Bank_Name", "Branch_Name", "Journal_Transaction_ID",
    "Journal_Line_ID", "Status",
]
APPLICATION_FIELDS = [
    "Cash_Application_ID", "Ledger_Type", "Settlement_ID", "Open_Item_ID", "Application_Date",
    "Applied_Amount", "Cash_Amount", "Note_Amount", "Fee_Amount", "Discount_Amount",
    "Offset_Amount", "Other_Adjustment_Amount", "Status",
]
JOURNAL_LINK_FIELDS = [
    "Journal_Document_Link_ID", "Transaction_ID", "Line_ID", "Document_ID",
    "Relationship_Type", "Link_Amount", "Recognition_Basis",
]
TRANSACTION_LINK_FIELDS = [
    "scope", "partner_type", "partner_code", "invoice_month", "invoice_transaction_id",
    "invoice_line_id", "invoice_document_date", "invoice_document_id", "invoice_document_number",
    "settlement_month", "settlement_transaction_id", "settlement_line_id",
    "settlement_document_date", "settlement_document_id", "settlement_document_number",
    "amount", "relationship",
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


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def month_role(month: str) -> str:
    if month in REFERENCE_BEFORE:
        return "REFERENCE_BEFORE"
    if month in REFERENCE_AFTER:
        return "REFERENCE_AFTER"
    return "REPORTING_PERIOD"


def normalize_control_partner(row: dict[str, str]) -> dict[str, object]:
    result: dict[str, object] = dict(row)
    if row.get("JP06e_GE24_01") == "1200":
        result["JP05a_01"], result["JP05a_02"] = "C001", "Demo Customer Aurora"
    if row.get("JP06f_GE24_01") == "1200":
        result["JP05b_01"], result["JP05b_02"] = "C001", "Demo Customer Aurora"
    if row.get("JP06e_GE24_01") == "2000":
        result["JP05a_01"], result["JP05a_02"] = "V001", "Demo Vendor Keystone"
    if row.get("JP06f_GE24_01") == "2000":
        result["JP05b_01"], result["JP05b_02"] = "V001", "Demo Vendor Keystone"
    return result


def make_transaction(tx: str, voucher: str, posted: str, description: str,
                     debit_code: str, debit_name: str, credit_code: str,
                     credit_name: str, amount: int, partner_type: str = "",
                     partner_code: str = "", partner_name: str = "") -> list[dict[str, object]]:
    month = posted[:7]
    entry_key = json.dumps([posted.replace("-", ""), voucher], separators=(",", ":"))
    base = {field: "" for field in JOURNAL_FIELDS}
    rows: list[dict[str, object]] = []
    for line_id, side in enumerate(("D", "C"), start=1):
        row = dict(base)
        row.update({
            "JP07a": tx, "JP08a": str(line_id), "Month": month,
            "JP07a_GL03_03": posted, "JP07a_GL03_01": voucher,
            "JP08a_GL04_03": description, "Source_Row": f"REF-{tx}",
            "Entry_Key": entry_key, "Semantic_Path": "$.cor_AccountingEntries.cor_EntryHeader.cor_EntryDetail.cor_MonetaryAmount",
            "Binding_Path": json.dumps({"detail": f"{line_id}-{side}"}, separators=(",", ":")),
        })
        if side == "D":
            row.update({"JP06e_GE24_01": debit_code, "JP06e_GE24_02": debit_name,
                        "Debit_Amount": amount, "Credit_Amount": 0,
                        "Debit_Occurrence": f"{line_id}-D"})
            if partner_type and debit_code in {"1200", "2000"}:
                row.update({"JP05a_01": partner_code, "JP05a_02": partner_name})
        else:
            row.update({"JP06f_GE24_01": credit_code, "JP06f_GE24_02": credit_name,
                        "Debit_Amount": 0, "Credit_Amount": amount,
                        "Credit_Occurrence": f"{line_id}-C"})
            if partner_type and credit_code in {"1200", "2000"}:
                row.update({"JP05b_01": partner_code, "JP05b_02": partner_name})
        rows.append(row)
    return rows


def reference_before_journal(accounts: dict[str, str]) -> list[dict[str, object]]:
    specs = [
        ("RB001", "REF-BF-AR-001", "2021-02-10", "Reference-before customer invoice", "1200", "4000", 600000, "C", "C001", "Demo Customer Aurora"),
        ("RB002", "REF-BF-AP-001", "2021-02-15", "Reference-before vendor invoice", "6000", "2000", 1200000, "S", "V001", "Demo Vendor Keystone"),
        ("RB003", "REF-BF-AR-SET-001", "2021-03-05", "Reference-before customer receipt", "1120", "1200", 100000, "C", "C001", "Demo Customer Aurora"),
        ("RB004", "REF-BF-AP-SET-001", "2021-03-08", "Reference-before vendor payment", "2000", "1120", 200000, "S", "V001", "Demo Vendor Keystone"),
        ("RB005", "REF-BF-AR-002", "2021-03-15", "Reference-before customer invoice", "1200", "4000", 1000000, "C", "C001", "Demo Customer Aurora"),
        ("RB006", "REF-BF-AP-002", "2021-03-20", "Reference-before vendor invoice", "6000", "2000", 2423720, "S", "V001", "Demo Vendor Keystone"),
    ]
    rows: list[dict[str, object]] = []
    for tx, voucher, posted, desc, debit, credit, amount, ptype, pcode, pname in specs:
        rows.extend(make_transaction(tx, voucher, posted, desc, debit, accounts[debit], credit,
                                     accounts[credit], amount, ptype, pcode, pname))
    return rows


def control_event(row: dict[str, object]) -> tuple[str, str, int] | None:
    debit_code = str(row.get("JP06e_GE24_01", ""))
    credit_code = str(row.get("JP06f_GE24_01", ""))
    if debit_code == "1200":
        return "AR", "INVOICE", int(row["Debit_Amount"])
    if credit_code == "1200":
        return "AR", "SETTLEMENT", int(row["Credit_Amount"])
    if credit_code == "2000":
        return "AP", "INVOICE", int(row["Credit_Amount"])
    if debit_code == "2000":
        return "AP", "SETTLEMENT", int(row["Debit_Amount"])
    return None


def empty_structured_row() -> dict[str, object]:
    return {column: "" for column in STRUCTURED_FIELDS}


def append_subaccount_rows(result: list[dict[str, object]], coordinates: dict[str, object],
                           occurrences: list[tuple[str, object, object]]) -> None:
    ordinal = 0
    for kind, code, description in occurrences:
        if code in ("", None) and description in ("", None):
            continue
        ordinal += 1
        row = empty_structured_row()
        row.update(coordinates)
        row.update({"C5": f"{ordinal}-{kind}", "C14": code, "C15": description, "C16": kind})
        result.append(row)


def append_tax_rows(result: list[dict[str, object]], coordinates: dict[str, object],
                    occurrences: list[tuple[object, object]]) -> None:
    ordinal = 0
    for category, amount in occurrences:
        if category in ("", None) and amount in ("", None):
            continue
        ordinal += 1
        row = empty_structured_row()
        row.update(coordinates)
        row.update({"C6": str(ordinal), "C17": category, "C18": amount})
        result.append(row)


def structured_rows(rows: list[dict[str, object]]) -> list[dict[str, object]]:
    """Return one row per HMD class occurrence, with ancestor coordinates only."""
    by_header: dict[str, list[dict[str, object]]] = {}
    for posting in rows:
        by_header.setdefault(str(posting["Entry_Key"]), []).append(posting)

    result: list[dict[str, object]] = []
    for header_key, postings in by_header.items():
        first = postings[0]
        for field in ("JP07a_GL03_03", "JP07a_GL03_01", "JP08a_GL04_03"):
            if any(str(item[field]) != str(first[field]) for item in postings):
                raise ValueError(f"Header-grain source value differs inside {header_key}: {field}")
        header = empty_structured_row()
        header.update({"C1": "1", "C2": header_key, "C7": first["JP07a_GL03_03"],
                       "C8": first["JP07a_GL03_01"], "C9": first["JP08a_GL04_03"]})
        result.append(header)

        seen_details: set[str] = set()
        for posting in postings:
            debit = bool(str(posting.get("Debit_Occurrence", "")))
            detail_key = str(posting["Debit_Occurrence"] if debit else posting["Credit_Occurrence"])
            if not detail_key or detail_key in seen_details:
                raise ValueError(f"Duplicate or missing detail occurrence in {header_key}: {detail_key}")
            seen_details.add(detail_key)
            prefix = "JP06e" if debit else "JP06f"
            sub_prefix = "JP05a" if debit else "JP05b"
            tax_prefix = "JP02j" if debit else "JP02k"
            base = {"C1": "1", "C2": header_key, "C3": detail_key}

            detail = empty_structured_row()
            detail.update(base)
            detail.update({"C10": "D" if debit else "C",
                           "C11": posting["Debit_Amount"] if debit else posting["Credit_Amount"]})
            result.append(detail)

            account = empty_structured_row()
            account.update(base)
            account.update({"C4": "1", "C12": posting[f"{prefix}_GE24_01"],
                            "C13": posting[f"{prefix}_GE24_02"]})
            result.append(account)

            child_base = {**base, "C4": "1"}
            append_subaccount_rows(result, child_base, [
                ("department", posting["BS04fb_01"] if debit else posting["BS04fc_01"],
                 posting["BS04fb_02"] if debit else posting["BS04fc_02"]),
                ("auxiliary-account", posting[f"{sub_prefix}_01"], posting[f"{sub_prefix}_02"]),
            ])
            append_tax_rows(result, base, [
                (posting[f"{tax_prefix}_BS09_01"], posting["GE05kw_01"] if debit else posting["GE05kB_01"]),
            ])
    return result


def display_metadata(binding: list[dict[str, str]]) -> dict[str, object]:
    return {
        "schema_id": SCHEMA_ID,
        "schema_version": SCHEMA_VERSION,
        "format": "class-occurrence structured tidy data",
        "owner_class_rule": "deepest populated occurrence-coordinate column C1..C6",
        "columns": [{key: row[key] for key in (
            "structured_column", "ordinal", "role", "sequence", "level", "type", "name_ja", "name_en",
            "datatype", "multiplicity", "owner_class", "semantic_path", "binding_path", "occurrence_rule", "required",
        )} for row in binding],
    }


def xbrl_csv_metadata(month: str) -> dict[str, object]:
    year, number = (int(value) for value in month.split("-"))
    end = calendar.monthrange(year, number)[1]
    concepts = {
        "C7": "cor:entryDatePosted", "C8": "cor:entryId", "C9": "cor:entryDescription",
        "C10": "cor:debitCreditIndicator", "C11": "cor:monetaryAmount",
        "C12": "cor:accountNumber", "C13": "cor:accountDescription",
        "C14": "cor:subaccountId", "C15": "cor:subaccountDescription", "C16": "cor:type",
        "C17": "cor:taxCategory", "C18": "cor:amountOfTaxes",
    }
    columns: dict[str, object] = {column: {} for column in STRUCTURED_FIELDS[:6]}
    for column, concept in concepts.items():
        dimensions = {"concept": concept}
        if column in {"C11", "C18"}:
            dimensions["unit"] = "iso4217:JPY"
        columns[column] = {"dimensions": dimensions}
    return {
        "documentInfo": {
            "documentType": "https://xbrl.org/2021/xbrl-csv",
            "namespaces": {
                "cor": "https://www.xbrl.or.jp/taxonomy/xbrl-gl-next/experimental/cor/2026-12-31",
                "plt": "https://www.xbrl.or.jp/taxonomy/xbrl-gl-next/experimental/plt/2026-12-31",
                "iso4217": "http://www.xbrl.org/2003/iso4217",
                "scheme": "http://www.example.com",
                "xbrl": "https://xbrl.org/2021",
            },
            "taxonomy": ["../../../../XBRL-GL-Next/taxonomy/accounting-entries/oim/cor_accountingEntries/cor-all-oim-2026-12-31.xsd"],
        },
        "tables": {f"{DATASET_ID}-{month}": {"template": "structured-tidy", "url": f"{month}.csv"}},
        "tableTemplates": {"structured-tidy": {
            "dimensions": {
                "period": f"{year:04d}-{number:02d}-{end:02d}T00:00:00",
                "entity": "scheme:Harbor-Lantern-Demo",
                "plt:d_cor_accountingEntries": "$C1",
                "plt:d_cor_entryHeader": "$C2",
                "plt:d_cor_entryDetail": "$C3",
                "plt:d_cor_detailAccountIdentifier": "$C4",
                "plt:d_cor_subaccount": "$C5",
                "plt:d_cor_detailTax": "$C6",
            },
            "columns": columns,
        }},
    }


def build_reference_ledger(months: list[str], journal: list[dict[str, object]], accounts: dict[str, dict[str, str]],
                           starting: dict[str, int]) -> tuple[dict[str, list[dict[str, object]]], dict[str, int]]:
    balance = dict(starting)
    result: dict[str, list[dict[str, object]]] = {}
    for month in months:
        rows: list[dict[str, object]] = []
        for code in sorted(accounts):
            rows.append({
                "Transaction_ID": "", "Line_ID": "", "Entry_ID": "", "Ledger_Side": "Opening",
                "Transaction_Date": f"{month}-01", "Description": "* Opening balance",
                "Ledger_Account_Number": code, "Ledger_Account_Name": accounts[code]["account_name"],
                "Subaccount_Code": "", "Subaccount_Name": "", "Department_Code": "", "Department_Name": "",
                "Debit_Amount": 0, "Credit_Amount": 0, "Counterpart_Account_Number": "",
                "Counterpart_Account_Name": "", "Counterpart_Subaccount_Code": "", "Counterpart_Subaccount_Name": "",
                "Counterpart_Department_Code": "", "Counterpart_Department_Name": "", "Balance": balance[code],
                "Source_Row": "", "Entry_Key": "", "Occurrence": "", "Semantic_Path": "", "Month": month,
            })
        month_rows = [r for r in journal if r["Month"] == month]
        by_tx: dict[str, list[dict[str, object]]] = defaultdict(list)
        for row in month_rows:
            by_tx[str(row["JP07a"])].append(row)
        for row in month_rows:
            debit = bool(str(row.get("JP06e_GE24_01", ""))) and int(row.get("Debit_Amount") or 0) != 0
            code = str(row["JP06e_GE24_01"] if debit else row["JP06f_GE24_01"])
            amount = int(row["Debit_Amount"] if debit else row["Credit_Amount"])
            normal = accounts[code]["normal_balance"]
            delta = amount if ((normal == "D") == debit) else -amount
            balance[code] += delta
            peers = [p for p in by_tx[str(row["JP07a"])] if p is not row]
            counterpart = peers[0] if len(peers) == 1 else {}
            cp_code = str(counterpart.get("JP06e_GE24_01") or counterpart.get("JP06f_GE24_01") or "")
            rows.append({
                "Transaction_ID": row["JP07a"], "Line_ID": row["JP08a"], "Entry_ID": row["JP07a_GL03_01"],
                "Ledger_Side": "Debit" if debit else "Credit", "Transaction_Date": row["JP07a_GL03_03"],
                "Description": row["JP08a_GL04_03"], "Ledger_Account_Number": code,
                "Ledger_Account_Name": row["JP06e_GE24_02"] if debit else row["JP06f_GE24_02"],
                "Subaccount_Code": row["JP05a_01"] if debit else row["JP05b_01"],
                "Subaccount_Name": row["JP05a_02"] if debit else row["JP05b_02"],
                "Department_Code": row["BS04fb_01"] if debit else row["BS04fc_01"],
                "Department_Name": row["BS04fb_02"] if debit else row["BS04fc_02"],
                "Debit_Amount": amount if debit else 0, "Credit_Amount": 0 if debit else amount,
                "Counterpart_Account_Number": cp_code,
                "Counterpart_Account_Name": accounts.get(cp_code, {}).get("account_name", ""),
                "Counterpart_Subaccount_Code": "", "Counterpart_Subaccount_Name": "",
                "Counterpart_Department_Code": "", "Counterpart_Department_Name": "",
                "Balance": balance[code], "Source_Row": row["Source_Row"], "Entry_Key": row["Entry_Key"],
                "Occurrence": row["Debit_Occurrence"] if debit else row["Credit_Occurrence"],
                "Semantic_Path": row["Semantic_Path"], "Month": month,
            })
        result[month] = rows
    return result, balance


def due_date(posted: str) -> str:
    year, month, _day = map(int, posted.split("-"))
    month += 1
    if month == 13:
        year, month = year + 1, 1
    return date(year, month, 28).isoformat()


def build_document_model(all_journal: list[dict[str, object]]) -> dict[str, list[dict[str, object]]]:
    documents: list[dict[str, object]] = []
    parties: list[dict[str, object]] = []
    details: list[dict[str, object]] = []
    open_items: list[dict[str, object]] = []
    settlements: list[dict[str, object]] = []
    applications: list[dict[str, object]] = []
    journal_links: list[dict[str, object]] = []
    transaction_links: list[dict[str, object]] = []
    queues: dict[str, deque[dict[str, object]]] = {"AR": deque(), "AP": deque()}
    open_by_id: dict[str, dict[str, object]] = {}
    doc_by_id: dict[str, dict[str, object]] = {}
    app_seq = 0

    ordered = sorted(all_journal, key=lambda r: (str(r["JP07a_GL03_03"]), MONTHS.index(str(r["Month"])), str(r["JP07a"]), int(r["JP08a"])))
    for row in ordered:
        event = control_event(row)
        if not event:
            continue
        ledger, event_type, amount = event
        ptype, pcode, pname = ("C", "C001", "Demo Customer Aurora") if ledger == "AR" else ("S", "V001", "Demo Vendor Keystone")
        month = str(row["Month"])
        tx, line = str(row["JP07a"]), str(row["JP08a"])
        stable = f"{month.replace('-', '')}-{row['JP07a_GL03_01']}-{line}-{ledger}"
        if event_type == "INVOICE":
            doc_id = f"DOC-INV-{stable}"
            doc_no = f"INV-{stable}"
            open_id = f"OI-{stable}"
            code = "SALES_INVOICE" if ledger == "AR" else "PURCHASE_INVOICE"
            type_name = "Sales Invoice" if ledger == "AR" else "Purchase Invoice"
            doc = {
                "Document_ID": doc_id, "Document_Number": doc_no, "Document_Type_Code": code,
                "Document_Type_Name": type_name, "Document_Date": row["JP07a_GL03_03"],
                "Recognition_Date": row["JP07a_GL03_03"], "Recognition_Basis": "explicit-journal-link",
                "Partner_Type": ptype, "Partner_Code": pcode, "Currency": "JPY", "Gross_Amount": amount,
                "Journal_Transaction_ID": tx, "Journal_Line_ID": line, "Status": "OPEN_ITEM",
                "Source": "project-authored synthetic control record",
            }
            documents.append(doc); doc_by_id[doc_id] = doc
            item = {
                "Open_Item_ID": open_id, "Ledger_Type": ledger, "Invoice_Document_ID": doc_id,
                "Invoice_Document_Number": doc_no, "Partner_Type": ptype, "Partner_Code": pcode,
                "Recognition_Date": row["JP07a_GL03_03"], "Due_Date": due_date(str(row["JP07a_GL03_03"])),
                "Original_Amount": amount, "Applied_Amount": 0, "Open_Amount": amount, "Currency": "JPY",
                "Recognition_Basis": "control-account posting", "Journal_Transaction_ID": tx,
                "Journal_Line_ID": line, "Status": "UNSETTLED", "_remaining": amount,
            }
            open_items.append(item); open_by_id[open_id] = item; queues[ledger].append(item)
            details.append({
                "Document_ID": doc_id, "Document_Number": doc_no, "Document_Type": type_name,
                "Partner_Type": ptype, "Partner_Code": pcode, "Document_Month": month,
                "Transaction_ID": tx, "Line_ID": line, "Line_Number": 1,
                "Item_Code": f"SYN-{ledger}", "Item_Description": "Synthetic evaluation item",
                "Quantity": 1, "Unit": "EA", "Unit_Price": amount, "Net_Amount": amount,
                "Tax_Category": "", "Tax_Rate": "", "Tax_Amount": 0, "Gross_Amount": amount,
                "Tax_Rounding": "none", "Amount_Basis": "gross equals linked control posting",
            })
        else:
            doc_id = f"DOC-SET-{stable}"
            doc_no = f"SET-{stable}"
            settlement_id = f"ST-{stable}"
            code = "BANK_RECEIPT_NOTICE" if ledger == "AR" else "BANK_TRANSFER_RECEIPT"
            type_name = "Customer Receipt" if ledger == "AR" else "Vendor Payment"
            doc = {
                "Document_ID": doc_id, "Document_Number": doc_no, "Document_Type_Code": code,
                "Document_Type_Name": type_name, "Document_Date": row["JP07a_GL03_03"],
                "Recognition_Date": row["JP07a_GL03_03"], "Recognition_Basis": "explicit-journal-link",
                "Partner_Type": ptype, "Partner_Code": pcode, "Currency": "JPY", "Gross_Amount": amount,
                "Journal_Transaction_ID": tx, "Journal_Line_ID": line, "Status": "SETTLEMENT",
                "Source": "project-authored synthetic control record",
            }
            documents.append(doc); doc_by_id[doc_id] = doc
            settlements.append({
                "Settlement_ID": settlement_id, "Settlement_Document_ID": doc_id,
                "Settlement_Document_Number": doc_no, "Ledger_Type": ledger, "Partner_Type": ptype,
                "Partner_Code": pcode, "Settlement_Date": row["JP07a_GL03_03"],
                "Settlement_Class": "RECEIPT" if ledger == "AR" else "PAYMENT", "Amount": amount,
                "Currency": "JPY", "Bank_Account_Code": "1120", "Bank_Name": "Demo Harbor Bank",
                "Branch_Name": "Main", "Journal_Transaction_ID": tx, "Journal_Line_ID": line,
                "Status": "APPLIED",
            })
            remaining = amount
            while remaining:
                if not queues[ledger]:
                    raise ValueError(f"Settlement exceeds {ledger} open items at {row['JP07a_GL03_03']}: {amount}")
                item = queues[ledger][0]
                applied = min(remaining, int(item["_remaining"]))
                app_seq += 1
                application_id = f"APP-{app_seq:05d}"
                applications.append({
                    "Cash_Application_ID": application_id, "Ledger_Type": ledger,
                    "Settlement_ID": settlement_id, "Open_Item_ID": item["Open_Item_ID"],
                    "Application_Date": row["JP07a_GL03_03"], "Applied_Amount": applied,
                    "Cash_Amount": applied, "Note_Amount": 0, "Fee_Amount": 0,
                    "Discount_Amount": 0, "Offset_Amount": 0, "Other_Adjustment_Amount": 0,
                    "Status": "APPLIED",
                })
                item["_remaining"] = int(item["_remaining"]) - applied
                remaining -= applied
                invoice_doc = doc_by_id[str(item["Invoice_Document_ID"])]
                transaction_links.append({
                    "scope": month_role(month), "partner_type": ptype, "partner_code": pcode,
                    "invoice_month": str(invoice_doc["Document_Date"])[:7],
                    "invoice_transaction_id": item["Journal_Transaction_ID"], "invoice_line_id": item["Journal_Line_ID"],
                    "invoice_document_date": invoice_doc["Document_Date"], "invoice_document_id": invoice_doc["Document_ID"],
                    "invoice_document_number": invoice_doc["Document_Number"], "settlement_month": month,
                    "settlement_transaction_id": tx, "settlement_line_id": line,
                    "settlement_document_date": doc["Document_Date"], "settlement_document_id": doc_id,
                    "settlement_document_number": doc_no, "amount": applied, "relationship": "EXPLICIT_APPLICATION",
                })
                if int(item["_remaining"]) == 0:
                    queues[ledger].popleft()
        journal_links.append({
            "Journal_Document_Link_ID": f"JDL-{stable}", "Transaction_ID": tx, "Line_ID": line,
            "Document_ID": doc_id, "Relationship_Type": event_type, "Link_Amount": amount,
            "Recognition_Basis": "explicit control-account line",
        })
        parties.append({
            "Document_Party_Role_ID": f"DPR-{stable}", "Document_ID": doc_id,
            "Role_Code": "CUSTOMER" if ledger == "AR" else "SUPPLIER", "Party_ID": pcode,
            "Party_Name": pname, "Department_Name": "", "Person_Name": "", "Position_Name": "",
            "Contact": "", "Snapshot_Basis": "synthetic master",
        })

    for item in open_items:
        applied = int(item["Original_Amount"]) - int(item["_remaining"])
        item["Applied_Amount"] = applied
        item["Open_Amount"] = item["_remaining"]
        item["Status"] = "SETTLED" if int(item["_remaining"]) == 0 else ("PARTIAL" if applied else "UNSETTLED")
        item.pop("_remaining", None)
    return {
        "business_document": documents, "business_document_party": parties,
        "transaction_document_detail": details, "ar_ap_open_item": open_items,
        "cash_settlement": settlements, "cash_application": applications,
        "journal_document_link": journal_links, "transaction_document_link": transaction_links,
    }


def derive_post_reference_journal(reporting_journal: list[dict[str, object]], before: list[dict[str, object]],
                                  accounts: dict[str, str]) -> list[dict[str, object]]:
    model = build_document_model(before + reporting_journal)
    open_items = [r for r in model["ar_ap_open_item"] if int(r["Open_Amount"]) > 0]
    ar = next(r for r in open_items if r["Ledger_Type"] == "AR")
    ar_open = int(ar["Open_Amount"])
    ap_open = sum(int(r["Open_Amount"]) for r in open_items if r["Ledger_Type"] == "AP")
    ar_first = max(1, ar_open // 2)
    ap_first = max(1, ap_open // 2)
    specs = [
        ("RA001", "REF-AF-AR-SET-001", "2022-04-10", "Reference-after customer receipt", "1120", "1200", ar_first, "C", "C001", "Demo Customer Aurora"),
        ("RA002", "REF-AF-AP-SET-001", "2022-04-15", "Reference-after vendor payment", "2000", "1120", ap_first, "S", "V001", "Demo Vendor Keystone"),
        ("RA003", "REF-AF-AR-SET-002", "2022-05-10", "Reference-after customer receipt", "1120", "1200", ar_open - ar_first, "C", "C001", "Demo Customer Aurora"),
        ("RA004", "REF-AF-AP-SET-002", "2022-05-15", "Reference-after vendor payment", "2000", "1120", ap_open - ap_first, "S", "V001", "Demo Vendor Keystone"),
    ]
    rows: list[dict[str, object]] = []
    for tx, voucher, posted, desc, debit, credit, amount, ptype, pcode, pname in specs:
        rows.extend(make_transaction(tx, voucher, posted, desc, debit, accounts[debit], credit,
                                     accounts[credit], amount, ptype, pcode, pname))
    return rows


def build_index() -> dict[str, object]:
    structured = {
        "by": "month", "path": "structured/{month}.csv",
        "metadata_path": "structured/{month}.json",
        "display_metadata_path": "structured/columns.json",
        "available": MONTHS, "language_neutral": True,
        "schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION,
    }
    return {
        "generated_at": "2026-09-07T19:28:44+09:00", "dataset_id": DATASET_ID,
        "predecessor_dataset_id": "pca-synthetic-fy2021-v2-settlement-16m", "default_month": "2021-04",
        "company": {"business_id": "SYNTHETIC-DEMO", "name": "Harbor Lantern Demo", "synthetic": True},
        "lang": "en", "months": MONTHS, "reporting_months": REPORTING_MONTHS,
        "reference_before_months": REFERENCE_BEFORE, "reference_after_months": REFERENCE_AFTER,
        "features": {"business_documents": True, "as_of_settlement": True, "structured_tidy": True},
        "views": {
            "tidy": {**structured, "display_name": "Structured Tidy Data"},
            "journal": {"by": "month", "path": "{lang}/journal/{month}.csv", "available": MONTHS},
            "ledger": {"by": "month", "path": "{lang}/ledger/{month}.csv", "available": MONTHS},
            "trial_balance": {"by": "month", "path": "{lang}/trial_balance/{month}.csv", "available": REPORTING_MONTHS},
            "balance_sheet": {"by": "month", "path": "{lang}/balance_sheet/{month}.csv", "available": REPORTING_MONTHS},
            "pnl": {"by": "month", "path": "{lang}/pnl/{month}.csv", "available": REPORTING_MONTHS},
            "structured": structured,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--binding", type=Path, required=True)
    parser.add_argument("--hmd", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--validation-status", choices=("PENDING_DELTA_VALIDATION", "PASS"), default="PENDING_DELTA_VALIDATION")
    args = parser.parse_args()
    if args.output.exists() and any(args.output.iterdir()):
        raise ValueError(f"Output must be empty: {args.output}")
    shutil.copytree(args.source, args.output, dirs_exist_ok=True)
    binding = read_csv(args.binding)
    if [r["structured_column"] for r in binding] != STRUCTURED_FIELDS:
        raise ValueError("Structured Binding columns are not exact C1...C18")
    if {r["schema_id"] for r in binding} != {SCHEMA_ID} or {r["schema_version"] for r in binding} != {SCHEMA_VERSION}:
        raise ValueError("Structured Binding schema identity does not match the generator")
    accounts_rows = read_csv(args.source / "source" / "account_master.csv")
    accounts = {r["account_code"]: r for r in accounts_rows}
    account_names = {code: row["account_name"] for code, row in accounts.items()}
    openings = {r["account"]: int(r["opening_balance"]) for r in read_csv(args.source / "source" / "beginning_balance.csv")}

    reporting_journal: list[dict[str, object]] = []
    for month in REPORTING_MONTHS:
        reporting_journal.extend(normalize_control_partner(r) for r in read_csv(args.source / "ja" / "journal" / f"{month}.csv"))
    before = reference_before_journal(account_names)
    after = derive_post_reference_journal(reporting_journal, before, account_names)
    all_journal = before + reporting_journal + after
    if sorted({str(r["Month"]) for r in all_journal}) != MONTHS:
        raise ValueError("Actual journal month set is not the exact 16-month contract")

    # Reference ledger periods are isolated from the fiscal-report roll-forward.
    before_ledger, _ = build_reference_ledger(REFERENCE_BEFORE, before, accounts, {code: 0 for code in accounts})
    fy_ending = {r["Ledger_Account_Number"]: int(r["Ending_Balance"]) for r in read_csv(args.source / "ja" / "trial_balance" / "2022-03.csv")}
    after_ledger, _ = build_reference_ledger(REFERENCE_AFTER, after, accounts, fy_ending)

    for lang in ("ja", "en"):
        for month in REPORTING_MONTHS:
            month_rows = [r for r in reporting_journal if r["Month"] == month]
            write_csv(args.output / lang / "journal" / f"{month}.csv", JOURNAL_FIELDS, month_rows)
            ledger_rows = read_csv(args.source / lang / "ledger" / f"{month}.csv")
            for row in ledger_rows:
                if row["Ledger_Account_Number"] == "1200":
                    row["Subaccount_Code"], row["Subaccount_Name"] = "C001", "Demo Customer Aurora"
                elif row["Ledger_Account_Number"] == "2000":
                    row["Subaccount_Code"], row["Subaccount_Name"] = "V001", "Demo Vendor Keystone"
            write_csv(args.output / lang / "ledger" / f"{month}.csv", LEDGER_FIELDS, ledger_rows)
        for month in REFERENCE_BEFORE:
            write_csv(args.output / lang / "journal" / f"{month}.csv", JOURNAL_FIELDS, [r for r in before if r["Month"] == month])
            write_csv(args.output / lang / "ledger" / f"{month}.csv", LEDGER_FIELDS, before_ledger[month])
        for month in REFERENCE_AFTER:
            write_csv(args.output / lang / "journal" / f"{month}.csv", JOURNAL_FIELDS, [r for r in after if r["Month"] == month])
            write_csv(args.output / lang / "ledger" / f"{month}.csv", LEDGER_FIELDS, after_ledger[month])
        write_csv(args.output / lang / "journal" / "ALL.csv", JOURNAL_FIELDS, all_journal)
        all_ledger = []
        for month in MONTHS:
            all_ledger.extend(read_csv(args.output / lang / "ledger" / f"{month}.csv"))
        write_csv(args.output / lang / "ledger" / "ALL.csv", LEDGER_FIELDS, all_ledger)

    write_json(args.output / "structured" / "columns.json", display_metadata(binding))
    for month in MONTHS:
        month_rows = [r for r in all_journal if r["Month"] == month]
        write_csv(args.output / "structured" / f"{month}.csv", STRUCTURED_FIELDS, structured_rows(month_rows))
        write_json(args.output / "structured" / f"{month}.json", xbrl_csv_metadata(month))

    model = build_document_model(all_journal)
    for lang in ("ja", "en"):
        source = args.output / lang / "source"
        localized_documents = [dict(row) for row in model["business_document"]]
        if lang == "ja":
            names = {"Sales Invoice": "売上請求", "Purchase Invoice": "仕入請求", "Customer Receipt": "入金", "Vendor Payment": "支払"}
            for row in localized_documents:
                row["Document_Type_Name"] = names[str(row["Document_Type_Name"])]
        write_csv(source / "business_document.csv", BUSINESS_DOCUMENT_FIELDS, localized_documents)
        write_csv(source / "business_document_party.csv", BUSINESS_PARTY_FIELDS, model["business_document_party"])
        write_csv(source / "transaction_document_detail.csv", DOCUMENT_DETAIL_FIELDS, model["transaction_document_detail"])
        write_csv(source / "ar_ap_open_item.csv", OPEN_ITEM_FIELDS, model["ar_ap_open_item"])
        write_csv(source / "cash_settlement.csv", SETTLEMENT_FIELDS, model["cash_settlement"])
        write_csv(source / "cash_application.csv", APPLICATION_FIELDS, model["cash_application"])
        write_csv(source / "journal_document_link.csv", JOURNAL_LINK_FIELDS, model["journal_document_link"])
        write_csv(source / "transaction_document_link.csv", TRANSACTION_LINK_FIELDS, model["transaction_document_link"])
        partner_rows = [
            {"category": "得意先" if lang == "ja" else "Customer", "code": "C001", "name": "Demo Customer Aurora", "digital_transaction": "", "alias1": "", "alias2": ""},
            {"category": "仕入先" if lang == "ja" else "Supplier", "code": "V001", "name": "Demo Vendor Keystone", "digital_transaction": "", "alias1": "", "alias2": ""},
        ]
        partner_file = "trading_partner.csv" if lang == "ja" else "trading_partner_en.csv"
        write_csv(source / partner_file, ["category", "code", "name", "digital_transaction", "alias1", "alias2"], partner_rows)
        write_csv(source / "trading_partner_balance.csv", ["partner_type", "partner_code", "opening_balance"], [
            {"partner_type": "C", "partner_code": "C001", "opening_balance": 1500000},
            {"partner_type": "S", "partner_code": "V001", "opening_balance": 3423720},
        ])

    binding_dest = args.output / "bindings" / args.binding.name
    binding_dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.binding, binding_dest)
    write_json(args.output / "index.json", build_index())
    write_json(args.output / "STRUCTURED_CN_LINEAGE.json", {
        "dataset_id": DATASET_ID, "schema_id": SCHEMA_ID, "schema_version": SCHEMA_VERSION,
        "source_dataset_id": "pca-synthetic-fy2021-v2-settlement-16m", "source_binding_id": "PCA_Cn_SEMANTIC_PATH_BINDING",
        "target_binding": f"bindings/{args.binding.name}", "target_binding_sha256": sha256(args.binding),
        "hmd_sha256": sha256(args.hmd),
        "mapping_rule": "one owner-class occurrence per row; ancestor coordinates inherited; property values owner-only",
        "xbrl_csv_metadata": "structured/{month}.json",
        "display_metadata": "structured/columns.json",
        "legacy_wide_materialization": "ja|en/journal and reports are generated display projections and are not Canonical Structured Tidy",
        "control_partner_normalization": {"1200": "C001", "2000": "V001", "amount_effect": 0},
    })
    write_json(args.output / "SETTLEMENT_LINEAGE.json", {
        "dataset_id": DATASET_ID, "relation_basis": "explicit immutable IDs only",
        "cutoff_formula": "open_as_of(T)=original-sum(applied where application_date<=T)",
        "reference_values_source": "project-authored deterministic rules; no historical CSV values copied",
        "document_count": len(model["business_document"]), "open_item_count": len(model["ar_ap_open_item"]),
        "settlement_count": len(model["cash_settlement"]), "application_count": len(model["cash_application"]),
    })

    period_rows = []
    for month in MONTHS:
        files = []
        for rel in [f"structured/{month}.csv", f"structured/{month}.json", f"ja/journal/{month}.csv", f"en/journal/{month}.csv", f"ja/ledger/{month}.csv", f"en/ledger/{month}.csv"]:
            p = args.output / rel
            files.append({"path": rel, "rows": len(read_csv(p)) if p.suffix == ".csv" else 1, "sha256": sha256(p)})
        period_rows.append({
            "month": month, "role": month_role(month), "accounting_report_inclusion": month in REPORTING_MONTHS,
            "settlement_as_of_available": True, "files": files,
        })
    write_json(args.output / "DATASET_PERIOD_MANIFEST.json", {"dataset_id": DATASET_ID, "months": period_rows})
    (args.output / "README.md").write_text(
        "# PCA synthetic FY2021 16-month settlement sample\n\n"
        f"`{DATASET_ID}` is a wholly fictional public evaluation dataset.\n\n"
        "- Reporting period: 2021-04 through 2022-03.\n"
        "- Reference-before: 2021-02 and 2021-03. Reference-after: 2022-04 and 2022-05.\n"
        "- Structured Tidy output: C1...C18 under `structured/`, one HMD class occurrence per row.\n"
        "- EntryHeader properties occur once; descendants inherit only complete occurrence coordinates.\n"
        "- Monthly JSON files are xBRL-CSV primary metadata; `structured/columns.json` is separate display metadata.\n"
        "- Existing `{ja|en}/tidy/` files are unreferenced predecessor diagnostics and are not runtime authority.\n"
        "- Explicit document, open-item, settlement, application, and journal relations are under `{ja|en}/source/`.\n"
        "- Reference-period postings are excluded from the twelve-month trial balance, BS, and PL.\n",
        encoding="utf-8", newline="\n",
    )

    for name in ("manifest.csv", "PUBLIC_MANIFEST.csv"):
        candidate = args.output / name
        if candidate.exists():
            candidate.unlink()
    manifest = []
    for path in sorted(p for p in args.output.rglob("*") if p.is_file()):
        manifest.append({"relative_path": path.relative_to(args.output).as_posix(), "bytes": path.stat().st_size, "sha256": sha256(path)})
    write_csv(args.output / "manifest.csv", ["relative_path", "bytes", "sha256"], manifest)
    public = [{**row, "dataset_id": DATASET_ID, "source_authority": "project-authored synthetic data and accepted public predecessor", "license": "repository content license", "generator": "tools/generate_pca_synthetic_16month_dataset.py", "validation_status": args.validation_status} for row in manifest]
    write_csv(args.output / "PUBLIC_MANIFEST.csv", ["relative_path", "bytes", "sha256", "dataset_id", "source_authority", "license", "generator", "validation_status"], public)
    print(json.dumps({"dataset_id": DATASET_ID, "months": len(MONTHS), "documents": len(model["business_document"]), "applications": len(model["cash_application"])}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
