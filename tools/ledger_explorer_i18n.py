"""
Ledger Explorer — Tkinter + Web viewer for accounting ledgers (i18n)

Copyright (c) 2024–2026 SAMBUICHI, Nobuyuki
(Sambuichi Professional Engineers Office)

Licence
- Executable program logic: MIT License
- Original LHM definitions, join/mapping tables, semantic labels, documentation,
  screenshots, and publishable sample data: CC BY-SA 4.0, including when these
  items are embedded in this source file
- Third-party standards and code lists retain their original rights and terms

Overview
This script reads a `parameters.json` file that points to one or more input CSV files
(Journal, General Ledger, Trial Balance, Balance Sheet, Profit and Loss, and
monthly Structured CSV / “hierarchical tidy data”).

It provides:
- A Tkinter GUI to browse and search the ledgers with language switching (ja/en).
- An optional export mode (`--export-dir`) to generate server-ready CSV files and
  index metadata for the web demo under `ledger/data/{lang}/...`.

Important note about Structured CSV
Monthly split output for Structured CSV is produced by splitting the *input CSV file*
by month boundaries (based on the configured month-start date), not by re-exporting
from a DataFrame, so that the original structured layout is preserved.

CLI usage
  python Ledger_explorer_i18n.py parameters.json
  python Ledger_explorer_i18n.py parameters.json --export-dir <OUT_DIR>
  python Ledger_explorer_i18n.py parameters.json --export-dir <OUT_DIR> --no-gui
"""

import argparse
import pandas as pd
import numpy as np
import csv
import json
import re
from collections import OrderedDict
from datetime import datetime
import sys
import os
import webbrowser
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, font
from typing import Dict, Any, Optional, List
from threading import Thread
import time

DEBUG = True
TRACE = True


# ---------------------------------------------------------------------------
# Application metadata (single source of truth for UI/footer strings)
# ---------------------------------------------------------------------------
APP_NAME = "Ledger Explorer"
APP_AUTHOR = "SAMBUICHI, Nobuyuki"
APP_ORG = "Sambuichi Professional Engineers Office"
APP_WEBSITE = "https://www.sambuichi.jp/"

# Licensing note:
# - Executable Python logic is distributed under the MIT License.
# - Original LHM definitions, join/mapping tables, semantic labels, translation
#   dictionaries, documentation text, and publishable sample data are shared
#   under CC BY-SA 4.0, including when embedded in this source file.
# - Third-party standards and code lists retain their original rights and terms.
# - See LICENSE-SCOPE.md for the detailed boundary.
APP_CODE_LICENCE = "MIT License"
APP_CONTENT_LICENCE = "CC BY-SA 4.0"
APP_CONTENT_LICENCE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"

APP_COPYRIGHT_YEAR = "2024-2026"
APP_COPYRIGHT_TEXT = (
    f"© {APP_COPYRIGHT_YEAR} {APP_AUTHOR} ({APP_ORG}) · {APP_CONTENT_LICENCE}"
)

# Short description (used for ABOUT text / logs if needed)
APP_DESC = {
    "en": "Viewer and exporter for accounting ledgers and monthly Structured CSV (hierarchical tidy data).",
    "ja": "会計帳簿と月次の構造化CSV（階層型 tidy data）を閲覧・出力するビューア。"
}

def debug_print(message):
    if DEBUG:
        print(message)


def trace_print(message):
    if TRACE:
        print(message)


def save_dataframe_to_csv(df, filename="output.csv", folder="debug_output"):
    """
    指定したDataFrameをCSVファイルに保存する関数
    Parameters:
    df (pd.DataFrame): 出力するDataFrame
    filename (str): 保存するCSVのファイル名（デフォルト: "output.csv"）
    folder (str): CSVを保存するフォルダ名（デフォルト: "debug_output"）
    Returns:
    str: 保存したファイルのパス
    """
    # フォルダが存在しなければ作成
    if not os.path.exists(folder):
        os.makedirs(folder)
    # フルパスを作成
    filename = f'{datetime.now().strftime("%m-%d_%H%M%S%f")[:-3]}_{filename[:-4]}.csv'
    file_path = os.path.join(folder, filename)
    # DataFrameをCSVに保存
    df.to_csv(file_path, index=False, encoding="utf-8-sig")  # UTF-8 (BOM付き) でエクスポート
    print(f"DataFrame saved to {file_path}")
    return file_path


MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


def _fiscal_month_key(dt: datetime, month_start_day: int = 1) -> str:
    """Return fiscal-month key 'YYYY-MM' using an arbitrary month start day.

    If month_start_day=1, this is the calendar month.
    If month_start_day=24, then e.g. 2026-02-01 belongs to 2026-01.
    """
    try:
        start_day = int(month_start_day)
    except Exception:
        start_day = 1
    if start_day < 1:
        start_day = 1
    if start_day > 31:
        start_day = 31

    y, m = dt.year, dt.month
    if dt.day < start_day:
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return f"{y:04d}-{m:02d}"


def _parse_date_str(s: str) -> Optional[datetime]:
    """Parse a date string into datetime (best-effort)."""
    if s is None:
        return None
    s = str(s).strip()
    if not s:
        return None

    m = re.match(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except Exception:
            pass

    try:
        ts = pd.to_datetime(s, errors="coerce")
        if pd.isna(ts):
            return None
        return ts.to_pydatetime()
    except Exception:
        return None


def _write_tidy_csv_by_fiscal_month_raw(
    input_path: str,
    *,
    out_dir: str,
    view_key: str = "tidy",
    date_col_candidates: Optional[List[str]] = None,
    month_start_day: int = 1,
) -> Dict[str, str]:
    """Split *raw* structured (hierarchical) tidy CSV into fiscal-month files.

    IMPORTANT:
      - This function does NOT load the CSV into a DataFrame.
      - It preserves the original row order and raw CSV layout by copying
        input lines verbatim into each output file.

    The fiscal month is determined by `month_start_day`.
    Rows with blank date are assigned to the most recent fiscal month (context).
    Rows before the first dated row are treated as a "preamble" and are copied
    into every month file so that each split file remains self-contained.
    """
    if date_col_candidates is None:
        date_col_candidates = [
            "Transaction_Date",
            "Transaction Date",
            "Date",
            "date",
            "Posting_Date",
            "Posting Date",
        ]

    if not input_path or not os.path.exists(input_path):
        return {}

    view_dir = os.path.join(out_dir, view_key)
    os.makedirs(view_dir, exist_ok=True)

    with open(input_path, "r", encoding="utf-8-sig", newline="") as f:
        header_line = f.readline()
        if not header_line:
            return {}

        header_row = next(csv.reader([header_line]))
        date_idx = None
        for cand in date_col_candidates:
            if cand in header_row:
                date_idx = header_row.index(cand)
                break

        if date_idx is None:
            out_all = os.path.join(view_dir, "ALL.csv")
            with open(out_all, "w", encoding="utf-8-sig", newline="") as w:
                w.write(header_line)
                w.write(f.read())
            return {"ALL": out_all}

        preamble_lines: List[str] = []
        buckets: "OrderedDict[str, List[str]]" = OrderedDict()
        current_month: Optional[str] = None

        for raw_line in f:
            if raw_line.strip() == "":
                if current_month is None:
                    preamble_lines.append(raw_line)
                else:
                    buckets.setdefault(current_month, []).append(raw_line)
                continue

            try:
                row = next(csv.reader([raw_line]))
            except Exception:
                if current_month is None:
                    preamble_lines.append(raw_line)
                else:
                    buckets.setdefault(current_month, []).append(raw_line)
                continue

            dval = row[date_idx] if date_idx < len(row) else ""
            dt = _parse_date_str(dval)
            if dt is not None:
                current_month = _fiscal_month_key(dt, month_start_day=month_start_day)

            if current_month is None:
                preamble_lines.append(raw_line)
            else:
                buckets.setdefault(current_month, []).append(raw_line)

    if not buckets:
        out_all = os.path.join(view_dir, "ALL.csv")
        with open(out_all, "w", encoding="utf-8-sig", newline="") as w:
            w.write(header_line)
            for ln in preamble_lines:
                w.write(ln)
        return {"ALL": out_all}

    mapping: Dict[str, str] = {}
    for m, lines in buckets.items():
        if not MONTH_RE.match(m):
            continue
        out_path = os.path.join(view_dir, f"{m}.csv")
        with open(out_path, "w", encoding="utf-8-sig", newline="") as w:
            w.write(header_line)
            for ln in preamble_lines:
                w.write(ln)
            for ln in lines:
                w.write(ln)
        mapping[m] = out_path

    if not mapping:
        out_all = os.path.join(view_dir, "ALL.csv")
        with open(out_all, "w", encoding="utf-8-sig", newline="") as w:
            w.write(header_line)
            for ln in preamble_lines:
                w.write(ln)
        return {"ALL": out_all}

    return mapping


def dict_to_csv(output_file: str, data_dict: dict) -> None:
    """Save a dict-of-dicts as CSV (utf-8-sig)."""
    if not data_dict:
        raise ValueError("data_dict is empty.")
    # Determine columns from first row
    first = next(iter(data_dict.values()))
    cols = list(first.keys()) if isinstance(first, dict) else []
    with open(output_file, mode="w", encoding="utf-8-sig", newline="") as f:
        import csv
        writer = csv.writer(f)
        header = ["Ledger_Account_Number"] + cols
        writer.writerow(header)
        for key, values in data_dict.items():
            row = [key] + [values.get(col, "") for col in cols]
            writer.writerow(row)


def _ensure_month_column(
    df: pd.DataFrame,
    *,
    month_col: str = "Month",
    date_col_candidates: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    Ensure df has a month_col as 'YYYY-MM'. If missing, try to derive from a date column.
    Returns a (shallow) copy only when needed.
    """
    if month_col in df.columns:
        # normalise if it looks like YYYY-MM already
        s = df[month_col].astype(str).str.slice(0, 7)
        if (s.map(lambda x: bool(MONTH_RE.match(x))).all()):
            if (s != df[month_col].astype(str)).any():
                df = df.copy()
                df[month_col] = s
            return df

    if date_col_candidates is None:
        date_col_candidates = [
            "Transaction_Date",
            "Transaction Date",
            "Date",
            "date",
            "Posting_Date",
            "Posting Date",
        ]

    date_col = None
    for c in date_col_candidates:
        if c in df.columns:
            date_col = c
            break

    if date_col is None:
        # Can't derive; leave as-is (caller can fallback to single file)
        return df

    df = df.copy()
    # robust parse: works for 'YYYY-MM-DD', ISO, datetime, etc.
    dt = pd.to_datetime(df[date_col], errors="coerce")
    df[month_col] = dt.dt.strftime("%Y-%m")
    return df


def _write_df_by_month(
    df: pd.DataFrame,
    *,
    out_dir: str,
    view_key: str,
    month_col: str = "Month",
    date_col_candidates: Optional[List[str]] = None,
) -> Dict[str, str]:
    """
    Write df split by month into out_dir/view_key/YYYY-MM.csv

    - If df already has month_col in YYYY-MM, use it.
    - Otherwise, try to derive month_col from one of date_col_candidates.
    Returns {YYYY-MM: filepath} or {"ALL": filepath} if month cannot be determined.
    """
    df2 = _ensure_month_column(df, month_col=month_col, date_col_candidates=date_col_candidates)
    if month_col not in df2.columns or df2[month_col].isna().all():
        # No month info => single file fallback
        path = os.path.join(out_dir, f"{view_key}.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return {"ALL": path}

    view_dir = os.path.join(out_dir, view_key)
    os.makedirs(view_dir, exist_ok=True)

    mapping: Dict[str, str] = {}
    # drop NaN months
    df2 = df2[df2[month_col].notna()].copy()
    for m, g in df2.groupby(month_col, sort=True):
        m = str(m)
        if not MONTH_RE.match(m):
            continue
        p = os.path.join(view_dir, f"{m}.csv")
        g.to_csv(p, index=False, encoding="utf-8-sig")
        mapping[m] = p

    # If nothing matched the regex, fallback to single
    if not mapping:
        path = os.path.join(out_dir, f"{view_key}.csv")
        df.to_csv(path, index=False, encoding="utf-8-sig")
        return {"ALL": path}

    return mapping
def _split_dict_by_month_columns(data_dict: dict) -> Dict[str, dict]:
    """
    If dict-of-dicts values have month-like keys (YYYY-MM), split into {month: dict}.
    Each month dict keeps same outer keys, and inner dict contains only that month.
    """
    if not data_dict:
        return {}

    first_val = next(iter(data_dict.values()))
    if not isinstance(first_val, dict):
        return {}

    month_cols = [k for k in first_val.keys() if isinstance(k, str) and MONTH_RE.match(k)]
    if not month_cols:
        return {}

    out: Dict[str, dict] = {}
    for m in month_cols:
        out[m] = {acc: {m: vals.get(m, "")} for acc, vals in data_dict.items() if isinstance(vals, dict)}
    return out


def _guess_lang_subdir_from_input_path(input_path: str, default: str = "ja") -> str:
    """
    Decide export language subdir by input filename.

    Rules (future-proof for multiple languages):
      - *_{lang}.csv -> "{lang}" (lowercase 2..8 letters)
      - otherwise   -> default (ja)

    Examples:
      - tidyGLeTax_ja.csv -> ja/
      - tidyGLeTax_en.csv -> en/
      - tidyGLeTax_fr.csv -> fr/
      - tidyGLeTax.csv    -> ja/
    """
    base = os.path.basename(input_path or "")
    stem = base.rsplit(".", 1)[0]  # drop extension
    m = re.search(r"_([a-z]{2,8})$", stem.lower())
    return m.group(1) if m else default


def export_web_csv(tidy_data, out_dir: str) -> dict:
    """
    Export datasets needed for Web UI, divided into month files.

    Output layout:
      out_dir/{lang}/
        index.json
        tidy/YYYY-MM.csv (raw / hierarchical tidy)
        journal/YYYY-MM.csv
        ledger/YYYY-MM.csv
        trial_balance/YYYY-MM.csv
        balance_sheet/ALL.csv (or YYYY-MM.csv if month columns exist)
        pnl/ALL.csv          (or YYYY-MM.csv if month columns exist)

    Language directory is decided from input filename:
      - *_ja.csv -> ja/
      - *_en.csv -> en/
      - *_fr.csv -> fr/ ... (future)
      - otherwise -> ja/
    """
    os.makedirs(out_dir, exist_ok=True)

    # Determine input file path
    try:
        input_path = tidy_data.get_file_path()
    except Exception:
        input_path = getattr(tidy_data, "file_path", "") or ""

    lang = _guess_lang_subdir_from_input_path(input_path, default="ja")
    out_lang_dir = os.path.join(out_dir, lang)
    os.makedirs(out_lang_dir, exist_ok=True)

    paths: Dict[str, Any] = {}

    # 0) Tidy (raw / hierarchical tidy data) -> month split
    # NOTE: Do NOT convert to DataFrame here. For structured/hierarchical CSV,
    # DataFrame round-trips can break the original sparse layout. We split the
    # *input file* line-by-line and copy lines verbatim.
    date_candidates: List[str] = []
    # Add mapped voucher-date column (if available)
    try:
        cols = tidy_data.get_columns()
    except Exception:
        cols = getattr(tidy_data, "columns", {}) or {}
    if isinstance(cols, dict):
        vdate = cols.get("伝票日付")
        if vdate:
            date_candidates.append(vdate)

    date_candidates += [
        "Transaction_Date",
        "Transaction Date",
        "Date",
        "date",
        "Posting_Date",
        "Posting Date",
    ]

    # month start day (default 1). Can be configured in the parameter JSON as:
    #   "month_start_day": 24
    try:
        params = getattr(tidy_data, "params", None) or {}
        month_start_day = int(params.get("month_start_day", 1))
    except Exception:
        month_start_day = 1

    if input_path:
        paths["tidy"] = _write_tidy_csv_by_fiscal_month_raw(
            input_path,
            out_dir=out_lang_dir,
            view_key="tidy",
            date_col_candidates=date_candidates,
            month_start_day=month_start_day,
        )

    # 1) Journal rows (amount) -> month split
    amount_df = pd.DataFrame(tidy_data.get_amount_rows()).copy()
    paths["journal"] = _write_df_by_month(amount_df, out_dir=out_lang_dir, view_key="journal")

    # 2) General ledger -> month split
    ledger_df = tidy_data.get_general_ledger_df().copy()
    paths["ledger"] = _write_df_by_month(ledger_df, out_dir=out_lang_dir, view_key="ledger")

    # 3) Trial balance -> month split
    summary_df = tidy_data.get_summary_df().copy()
    paths["trial_balance"] = _write_df_by_month(summary_df, out_dir=out_lang_dir, view_key="trial_balance")

    # 4) BS / PL (period-end statements; not month-based)
    bs_dict = getattr(tidy_data, "bs_dict", None) or {}
    pl_dict = getattr(tidy_data, "pl_dict", None) or {}

    # If BS/PL dicts are not prepared yet, try building once
    if (not bs_dict) or (not pl_dict):
        if hasattr(tidy_data, "bs_pl"):
            try:
                tidy_data.bs_pl()
            except Exception as e:
                trace_print(f"[WARN] bs_pl() failed: {e}")
        bs_dict = getattr(tidy_data, "bs_dict", None) or {}
        pl_dict = getattr(tidy_data, "pl_dict", None) or {}

    # Balance sheet (ALL)
    bs_dir = os.path.join(out_lang_dir, "balance_sheet")
    os.makedirs(bs_dir, exist_ok=True)
    if bs_dict:
        p = os.path.join(bs_dir, "ALL.csv")
        dict_to_csv(p, bs_dict)
        paths["balance_sheet"] = {"ALL": p}
    else:
        trace_print("[WARN] bs_dict is empty; skip balance_sheet export")
        paths["balance_sheet"] = {}

    # Profit & loss (ALL)
    pl_dir = os.path.join(out_lang_dir, "pnl")
    os.makedirs(pl_dir, exist_ok=True)
    if pl_dict:
        p = os.path.join(pl_dir, "ALL.csv")
        dict_to_csv(p, pl_dict)
        paths["pnl"] = {"ALL": p}
    else:
        trace_print("[WARN] pl_dict is empty; skip pnl export")
        paths["pnl"] = {}

    # ---- Build index.json for the Web UI ----
    months = sorted(
        {m for v in paths.values() if isinstance(v, dict) for m in v.keys() if m != "ALL"}
    )

    def _avail(view_key: str) -> List[str]:
        v = paths.get(view_key)
        if isinstance(v, dict):
            return sorted(v.keys())
        return ["ALL"]

    index = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "lang": lang,
        "months": months,
        "views": {
            "tidy": {
                "by": "month",
                "path": "tidy/{month}.csv",
                "fallback": "tidy/ALL.csv",
                "available": _avail("tidy"),
            },
            "journal": {
                "by": "month",
                "path": "journal/{month}.csv",
                "fallback": "journal/ALL.csv",
                "available": _avail("journal"),
            },
            "ledger": {
                "by": "month",
                "path": "ledger/{month}.csv",
                "fallback": "ledger/ALL.csv",
                "available": _avail("ledger"),
            },
            "trial_balance": {
                "by": "month",
                "path": "trial_balance/{month}.csv",
                "fallback": "trial_balance/ALL.csv",
                "available": _avail("trial_balance"),
            },
            "balance_sheet": {
                "by": "month",
                "path": "balance_sheet/{month}.csv",
                "fallback": "balance_sheet/ALL.csv",
                "available": _avail("balance_sheet"),
            },
            "pnl": {
                "by": "month",
                "path": "pnl/{month}.csv",
                "fallback": "pnl/ALL.csv",
                "available": _avail("pnl"),
            },
        },
    }

    write_language_index = bool(params.get("write_language_index", True))
    meta_path = None
    if write_language_index:
        meta_path = os.path.join(out_lang_dir, "index.json")
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)
        paths["index"] = meta_path
    paths["output_root"] = out_lang_dir

    # Flatten file list for convenience
    def _rel(p: str) -> str:
        return os.path.relpath(p, out_lang_dir)

    file_list: List[str] = [_rel(meta_path)] if meta_path else []
    for k, v in paths.items():
        if k in ("index", "output_root"):
            continue
        if isinstance(v, dict):
            for fp in v.values():
                file_list.append(_rel(fp))
        elif isinstance(v, str):
            file_list.append(_rel(v))

    paths["_files"] = sorted(set(file_list))
    return paths

# ---------------------------------------------------------------------------
# Localisation (i18n)
# ---------------------------------------------------------------------------
# Use stable keys and map them to language-specific labels.
# This keeps UI text consistent and makes customisation easy.
UI_LABELS = {
    # Title
    "app_title": {"ja": "会計帳簿", "en": "Ledger Explorer"},
    # Bottom buttons
    "entity_customer": {"ja": "得意先", "en": "Customer"},
    "entity_supplier": {"ja": "仕入先", "en": "Supplier"},
    "entity_bank": {"ja": "銀行", "en": "Bank"},
    # Top controls
    "show": {"ja": "表示", "en": "Show"},
    "account_title": {"ja": "科目:", "en": "Account:"},
    "month_title": {"ja": "対象月:", "en": "Month:"},
    "load_params": {"ja": "パラメタファイル", "en": "Load Parameters"},
    "reset_selection": {"ja": "選択解除", "en": "Reset Selection"},
    # Window title / entities
    "app_title": {"ja": "会計帳簿", "en": "Accounting Ledger"},
    "entity_customer": {"ja": "得意先", "en": "Customer"},
    "entity_supplier": {"ja": "仕入先", "en": "Supplier"},
    "entity_bank": {"ja": "銀行", "en": "Bank"},
    # Search / actions
    "search_term": {"ja": "摘要文検索語:", "en": "Search Term:"},
    "search": {"ja": "検索", "en": "Search"},
    "reset_search": {"ja": "検索解除", "en": "Reset Search"},
    "view_data": {"ja": "データ参照", "en": "View Data"},
    "toggle_code_cols": {"ja": "コード列表示", "en": "Toggle Columns"},
    "save_csv": {"ja": "CSV保存", "en": "Save CSV"},
    "toggle_lang": {"ja": "日本語/English", "en": "日本語/English"},

    # Message box titles
    "msg_warning": {"ja": "警告", "en": "Warning"},
    "msg_error": {"ja": "エラー", "en": "Error"},
    "msg_success": {"ja": "成功", "en": "Success"},

    # Log messages (add as needed)
    "log_processing": {"ja": "処理中...", "en": "Processing..."},
}

# Menu items for the main view selector.
# Use internal keys to avoid having to translate back-and-forth.
UI_MENU = OrderedDict([
    ("journal", {"ja": "仕訳帳", "en": "Journal Entry"}),
    ("ledger", {"ja": "総勘定元帳画面", "en": "General Ledger"}),
    ("trial_balance", {"ja": "試算表画面", "en": "Trial Balance"}),
    ("balance_sheet", {"ja": "貸借対照表", "en": "Balance Sheet"}),
    ("pnl", {"ja": "損益計算書", "en": "Profit and Loss"}),
])

# Treeview column headings (term-by-term; independent of frame)
UI_COL_LABELS = {
    # Journal
    "Journal": {"ja": "伝票", "en": "JNL"},
    "DetailRow": {"ja": "行", "en": "LN"},
    "TransactionDate": {"ja": "取引日", "en": "Date"},
    "Description": {"ja": "摘要文", "en": "Description"},
    "DebitAccountCode": {"ja": "コード", "en": "Code"},
    "DebitAccountName": {"ja": "借方科目", "en": "Debit Acct"},
    "Debit_Amount": {"ja": "借方金額", "en": "Debit Amount"},
    "DebitTaxCode": {"ja": "コード", "en": "Code"},
    "DebitTaxName": {"ja": "借方税区分", "en": "Debit Tax"},
    "DebitTaxAmount": {"ja": "借方消費税額", "en": "Debit Tax Amount"},
    "DebitSubaccountCode": {"ja": "コード", "en": "Code"},
    "DebitSubaccountName": {"ja": "借方補助科目", "en": "Debit Sub"},
    "DebitDepartmentCode": {"ja": "コード", "en": "Code"},
    "DebitDepartmentName": {"ja": "借方部門", "en": "Debit Dept."},
    "CreditAccountCode": {"ja": "コード", "en": "Code"},
    "CreditAccountName": {"ja": "貸方科目", "en": "Credit Acct"},
    "Credit_Amount": {"ja": "貸方金額", "en": "Credit Amount"},
    "CreditTaxCode": {"ja": "コード", "en": "Code"},
    "CreditTaxName": {"ja": "貸方税区分", "en": "Credit Tax"},
    "CreditTaxAmount": {"ja": "貸方消費税額", "en": "Credit Tax Amount"},
    "CreditSubaccountCode": {"ja": "コード", "en": "Code"},
    "CreditSubaccountName": {"ja": "貸方補助科目", "en": "Credit Sub"},
    "CreditDepartmentCode": {"ja": "コード", "en": "Credit Dept. Code"},
    "CreditDepartmentName": {"ja": "貸方部門", "en": "Credit Dept."},

    # Ledger
    "Transaction_Date": {"ja": "伝票日付", "en": "Transaction Date"},
    "Ledger_Account_Number": {"ja": "コード", "en": "Ledger Account Number"},
    "Ledger_Account_Name": {"ja": "科目", "en": "Ledger Account Name"},
    "Subaccount_Code": {"ja": "コード", "en": "Subaccount Code"},
    "Subaccount_Name": {"ja": "補助科目", "en": "Subaccount Name"},
    "Department_Code": {"ja": "コード", "en": "Department Code"},
    "Department_Name": {"ja": "部門", "en": "Department Name"},
    "Balance": {"ja": "残高", "en": "Balance"},
    "Counterpart_Account_Number": {"ja": "コード", "en": "Counterpart Account Number"},
    "Counterpart_Account_Name": {"ja": "相手科目", "en": "Counterpart Account Name"},
    "Counterpart_Subaccount_Code": {"ja": "コード", "en": "Counterpart Subaccount Code"},
    "Counterpart_Subaccount_Name": {"ja": "相手補助科目", "en": "Counterpart Subaccount Name"},
    "Counterpart_Department_Code": {"ja": "コード", "en": "Counterpart Department Code"},
    "Counterpart_Department_Name": {"ja": "相手部門", "en": "Counterpart Department Name"},

    # Reports
    "Month": {"ja": "年月", "en": "Month"},
    "Account_Number": {"ja": "コード", "en": "Account Number"},
    "Account_Name": {"ja": "科目", "en": "Account Name"},
    "eTax_Category": {"ja": "勘定科目区分", "en": "Account Category"},
    "eTax_Account_Name": {"ja": "勘定科目名", "en": "Account Name"},
    "Beginning_Balance": {"ja": "月初残高", "en": "Starting Balance"},
    "Ending_Balance": {"ja": "月末残高", "en": "Ending Balance"},
    "Debit_Amount": {"ja": "借方金額", "en": "Debit Amount"},
    "Credit_Amount": {"ja": "貸方金額", "en": "Credit Amount"},
    "Level": {"ja": "レベル", "en": "Level"},
    "seq": {"ja": "順序", "en": "Seq"},
}


class LogTracker:
    def __init__(self):
        self.line = "0.0"
        self.start_time = None
        self.current_time = None
        self.start_time = datetime.now()
        with open(param_file_path, "r", encoding="utf-8-sig") as param_file:
            params = json.load(param_file)
        self.params = params
        self.DEBUG = 1 == params["DEBUG"]
        self.TRACE = 1 == params["TRACE"]
        self.lang = params["lang"]
        self.buffer = []

    def debug_print(self, message):
        if self.DEBUG:
            print(message)

    def trace_print(self, message):
        if self.TRACE:
            print(message)

    def get_elapsed(self):
        start = self.get_start()
        current = self.get_current()
        if start and current:
            elapsed_time = current - start
            total_seconds = elapsed_time.total_seconds()
            minutes = int(total_seconds // 60)
            seconds = total_seconds % 60
            return f"{minutes}:{seconds:.1f}"
        return None

    def get_start(self):
        return self.start_time

    def get_current(self):
        self.current_time = datetime.now()
        return self.current_time

    def write_log_text(self, message):
        line = self.line
        line = f"{int(line[:line.index('.')]) + 1}.0"
        self.line = line
        elapsed = self.get_elapsed()
        text = f"{elapsed} {message}\n"
        self.trace_print(text)
        g = globals().get("gui", None)
        if g is None or not getattr(g, "log_text", None):
            self.buffer.append(text)
            self.line = '1.0'
        else:
            for i in range(len(self.buffer)):
                _text = self.buffer[i]
                line = f"{1+i}.0"
                g.log_text.insert(line, _text)
            g.log_text.insert(line, text)
            # 最後の行にスクロールする
            g.scroll_to_end()


class ExecutionMessage:
    """ 処理中メッセージウィンドウを管理するクラス """
    custom_window = None
    @staticmethod
    def start(root, callback, *args):
        """ メッセージウィンドウを表示し、指定された処理を非同期に実行 """
        if ExecutionMessage.custom_window is None:
            ExecutionMessage.custom_window = tk.Toplevel(root)
            ExecutionMessage.custom_window.title("処理中")
            ExecutionMessage.custom_window.geometry("300x100")
            # ウィンドウを画面中央に配置
            root.update_idletasks()  # ウィンドウサイズの更新を確定
            x = root.winfo_x() + (root.winfo_width() // 2) - (300 // 2)
            y = root.winfo_y() + (root.winfo_height() // 2) - (100 // 2)
            ExecutionMessage.custom_window.geometry(f"300x100+{x}+{y}")
            tk.Label(ExecutionMessage.custom_window, text="処理中...しばらくお待ちください。", padx=20, pady=20).pack()
            ExecutionMessage.custom_window.protocol("WM_DELETE_WINDOW", lambda: None)  # 閉じる操作を無効化
        # 非同期処理の開始
        root.after(100, lambda: callback(*args))

    @staticmethod
    def end():
        """ メッセージウィンドウを閉じる """
        if ExecutionMessage.custom_window:
            ExecutionMessage.custom_window.destroy()
            ExecutionMessage.custom_window = None


class TidyData:
    def __init__(self):
        self.DEBUG = False
        self.TRACE = False
        self.params = None
        self.columns = None
        self.file_path = None
        self.BS_path = None,
        self.PL_path = None,
        self.trading_partner_path = None
        self.trading_partner_dict = None
        self.LHM_path = None,
        self.LHM_dict = None,
        # self.account_category_dict = None
        self.beginning_balances = None
        self.amount_rows = None
        self.general_ledger_df = None
        self.summary_df = None
        self.bs_data_df = None
        self.pl_data_df = None
        self.account_dict = None
        # self.account_dict2 = None
        self.bs_dict = None
        self.pl_dict = None
        # 勘定科目ごとの貸借の増減方向を持つ辞書を定義
        self.account_direction_dict = {
            "資産": "借方増",
            "負債": "貸方増",
            "純資産": "貸方増",
            "費用": "借方増",
            "収益": "貸方増",
        }
        self.account_direction_dict_en = {
            "Assets": "Increase on Debit",
            "Liabilities": "Increase on Credit",
            "Net assets": "Increase on Credit",
            "Equity": "Increase on Credit",
            "Expenses": "Increase on Debit",
            "Revenue": "Increase on Credit",
        }
        parameter_path = os.path.abspath(param_file_path)
        parameter_dir = os.path.dirname(parameter_path)
        with open(parameter_path, "r", encoding="utf-8-sig") as param_file:
            params = json.load(param_file)

        def resolve_input_path(value):
            """Resolve configured input paths relative to the parameter file."""
            path = os.path.expanduser(str(value))
            if not os.path.isabs(path):
                path = os.path.join(parameter_dir, path)
            return os.path.normpath(os.path.abspath(path))

        self.params = params
        self.parameter_path = parameter_path
        self.DEBUG = 1 == params["DEBUG"]
        self.TRACE = 1 == params["TRACE"]
        self.file_path = resolve_input_path(params["e-tax_file_path"])
        self.structural_file_path = resolve_input_path(
            params.get("structural_file_path", params["e-tax_file_path"])
        )
        self.etax_beginning_balance_path = resolve_input_path(params["e-tax_beginning_balance_path"])
        self.account_path = resolve_input_path(params["account_path"])
        self.structural_account_path = resolve_input_path(
            params.get("structural_account_path", params["account_path"])
        )
        self.tax_category_path = resolve_input_path(params["tax_category_path"])
        self.trading_partner_path = resolve_input_path(params["trading_partner_path"])
        self.LHM_path = resolve_input_path(params["LHM_path"])
        self.BS_path = resolve_input_path(params["HOT010_3.0_BS_10"])
        self.PL_path = resolve_input_path(params["HOT010_3.0_PL_10"])
        self.columns  = params["columns"]
        self.account_category = params["account_category"]
        self.lang = params["lang"]
        self.english_pattern = re.compile(r'^[a-zA-Z \-]+$')

    def debug_print(self, message):
        if self.DEBUG:
            print(message)

    def trace_print(self, message):
        if self.TRACE:
            print(message)

    def get_file_path(self):
        return self.file_path

    def get_columns(self):
        return self.columns

    def get_amount_rows(self):
        return self.amount_rows

    def replace_name(self, row):
        if "en"== self.lang:
            if not re.search(self.english_pattern, row['Ledger_Account_Name']):
                # `bs_template_df` から置き換え値を取得
                # Get replacement values from 'bs_template_df'
                replacement = self.bs_template_df.loc[
                        self.bs_template_df['Ledger_Account_Number'] == row['Ledger_Account_Number'], 'Account_Name'
                    ]
                if replacement.empty:
                    # `pl_template_df` から置き換え値を取得
                    # Get replacement values from 'pl_template_df'
                    replacement = self.pl_template_df.loc[
                            self.pl_template_df['Ledger_Account_Number'] == row['Ledger_Account_Number'], 'Account_Name'
                        ]
                if not replacement.empty:
                    row['Ledger_Account_Name'] = replacement.values[0]
            if 'Counterpart_Account_Name' in row and not re.search(self.english_pattern, row['Counterpart_Account_Name']):
                # `bs_template_df` から置き換え値を取得
                # Get replacement values from 'bs_template_df'
                replacement = self.bs_template_df.loc[
                    self.bs_template_df['Ledger_Account_Number'] == row['Counterpart_Account_Number'], 'Account_Name'
                ]
                if replacement.empty:
                    # `pl_template_df` から置き換え値を取得
                    # Get replacement values from 'pl_template_df'
                    replacement = self.pl_template_df.loc[
                        self.pl_template_df['Ledger_Account_Number'] == row['Counterpart_Account_Number'], 'Account_Name'
                    ]
                if not replacement.empty:
                    row['Counterpart_Account_Name'] = replacement.values[0]
        return row

    def get_general_ledger_df(self):
        self.general_ledger_df = self.general_ledger_df.apply(self.replace_name, axis=1)
        return self.general_ledger_df

    def replace_category(self, row):
        if "en"== self.lang:
            if 'eTax_Category' in row and not re.search(self.english_pattern, row['eTax_Category']):
                category = row['eTax_Category']
                if not re.search(self.english_pattern, category):
                    if category and category in self.account_category:
                        row['eTax_Category'] = self.account_category[category]
            if 'eTax_Account_Name' in row and not re.search(self.english_pattern, row['eTax_Account_Name']):
                # `bs_template_df` から置き換え値を取得
                # Get replacement values from 'bs_template_df'
                replacement = self.bs_template_df.loc[
                    self.bs_template_df['Ledger_Account_Number'] == row['Ledger_Account_Number'], 'Account_Name'
                ]
                if replacement.empty:
                    # `pl_template_df` から置き換え値を取得
                    # Get replacement values from 'pl_template_df'
                    replacement = self.pl_template_df.loc[
                        self.pl_template_df['Ledger_Account_Number'] == row['Ledger_Account_Number'], 'Account_Name'
                    ]
                if not replacement.empty:
                    row['eTax_Account_Name'] = replacement.values[0]
            if 'Ledger_Account_Name' in row and not re.search(self.english_pattern, row['Ledger_Account_Name']):
                # `bs_template_df` から置き換え値を取得
                # Get replacement values from 'bs_template_df'
                replacement = self.bs_template_df.loc[
                    self.bs_template_df['Ledger_Account_Number'] == row['Ledger_Account_Number'], 'Account_Name'
                ]
                if replacement.empty:
                    # `pl_template_df` から置き換え値を取得
                    # Get replacement values from 'pl_template_df'
                    replacement = self.pl_template_df.loc[
                        self.pl_template_df['Ledger_Account_Number'] == row['Ledger_Account_Number'], 'Account_Name'
                    ]
                if not replacement.empty:
                    row['Ledger_Account_Name'] = replacement.values[0]
        return row

    def get_summary_df(self):
        self.summary_df = self.summary_df.apply(self.replace_category, axis=1)
        return self.summary_df

    def get_account_dict(self):
        account_dict = {}
        for id, d in self.account_dict.items():
            if len(id) > 3:
                match = re.match(r"([ 0-9a-zA-Z\-]+)", id[11:])
                if 'en' == self.lang:
                    if match:
                        account_dict[id] = d
                else:
                    if not match:
                        account_dict[id] = d
        return account_dict

    # 月初日を取得する関数
    def get_month_start(self, date):
        return pd.Timestamp(date.year, date.month, 1)

    def etax_template(self):
        # e-Tax CSV Sheet for BS
        input_BS_path = self.BS_path  # BS Template CSV
        # Load the CSV file and use the first row as the header
        self.bs_template_df = pd.read_csv(input_BS_path, header=0)
        # カラム名に余分なスペースがある場合の対応
        self.bs_template_df.columns = self.bs_template_df.columns.str.strip()
        # Ensure the Ledger_Account_Number column is present by checking its existence
        if "Ledger_Account_Number" not in self.bs_template_df.columns:
            raise KeyError("Ledger_Account_Number column is missing. Check the CSV file structure.")
        # Select only the desired columns and drop rows where "Ledger_Account_Number" is NaN
        self.bs_template_df = self.bs_template_df[["name", "category", "seq", "account_name", "type", "level", "Ledger_Account_Number", "English_Label"]].dropna(subset=["Ledger_Account_Number"])
        if "en" == self.lang:
            self.bs_template_df.rename(
                columns={
                    "name": "Name",
                    "category": "Category",
                    "English_Label": "Account_Name",
                    "type": "Type",
                    "level": "Level"
                },
                inplace=True
            )
        else:
            self.bs_template_df.rename(
                columns={
                    "name": "Name",
                    "category": "Category",
                    "account_name": "Account_Name",
                    "type": "Type",
                    "level": "Level"
                },
                inplace=True
            )
        self.bs_template_df["Level"] = self.bs_template_df["Level"].fillna(0).astype(int)
        # # if "en" == self.lang:
        # # Replacing the "Category" values using the mapping dictionary
        # _orig = self.bs_template_df["Category"]
        # _mapped = _orig.map(self.account_category)
        # # Keep original when no mapping exists (e.g., already English)
        # self.bs_template_df["Category"] = _mapped.fillna(_orig)
        # Category i18n:
        # - English: map the source category (typically Japanese) to English using parameters.json `account_category`.
        # - Japanese/others: keep the source category as-is. If the source is already English, try reverse-mapping.
        if "en" == self.lang:
            self.bs_template_df["Category"] = (
                self.bs_template_df["Category"]
                .map(self.account_category)
                .fillna(self.bs_template_df["Category"])
            )
        else:
            try:
                rev = {v: k for k, v in (self.account_category or {}).items()}
            except Exception:
                rev = {}
            if rev:
                self.bs_template_df["Category"] = (
                    self.bs_template_df["Category"]
                    .map(rev)
                    .fillna(self.bs_template_df["Category"])
                )
        # Preserve the leading full-width spaces while replacing the rest of the "Name" with "Account_Name"
        self.bs_template_df["Name"] = self.bs_template_df.apply(
            lambda row: ''.join(ch for ch in row["Name"] if ch == '\u3000' or ch == ' ') + row["Account_Name"],
            axis=1
        )
        # Display the modified DataFrame
        self.debug_print(self.bs_template_df.head())
        # e-Tax CSV Sheet for PL
        input_PL_path = self.PL_path  # Replace with your input CSV file path
        # Load the CSV file
        self.pl_template_df = pd.read_csv(input_PL_path, header=0)
        # カラム名に余分なスペースがある場合の対応
        self.pl_template_df.columns = self.pl_template_df.columns.str.strip()
        # Ensure the Ledger_Account_Number column is present by checking its existence
        if "Ledger_Account_Number" not in self.pl_template_df.columns:
            raise KeyError("Ledger_Account_Number column is missing. Check the CSV file structure.")
        # Drop rows where Ledger_Account_Number is missing (i.e., NaN values in that column)
        self.pl_template_df = self.pl_template_df[["name", "category", "seq", "account_name", "type", "level", "Ledger_Account_Number", "English_Label"]].dropna(subset=["Ledger_Account_Number"])
        if "en" == self.lang:
            self.pl_template_df.rename(
                columns={
                    "name": "Name",
                    "category": "Category",
                    "English_Label": "Account_Name",
                    "type": "Type",
                    "level": "Level"
                },
                inplace=True
            )
        else:
            self.pl_template_df.rename(
                columns={
                    "name": "Name",
                    "category": "Category",
                    "account_name": "Account_Name",
                    "type": "Type",
                    "level": "Level"
                },
                inplace=True
            )
        self.pl_template_df["Level"] = self.pl_template_df["Level"].fillna(0).astype(int)
        # if "en" == self.lang:
        # # Replacing the "Category" values using the mapping dictionary
        # _orig = self.pl_template_df["Category"]
        # _mapped = _orig.map(self.account_category)
        # # Keep original when no mapping exists (e.g., already English)
        # self.pl_template_df["Category"] = _mapped.fillna(_orig)
        # Category i18n:
        # - English: map the source category (typically Japanese) to English using parameters.json `account_category`.
        # - Japanese/others: keep the source category as-is. If the source is already English, try reverse-mapping.
        if "en" == self.lang:
            self.pl_template_df["Category"] = (
                self.pl_template_df["Category"]
                .map(self.account_category)
                .fillna(self.pl_template_df["Category"])
            )
        else:
            try:
                rev = {v: k for k, v in (self.account_category or {}).items()}
            except Exception:
                rev = {}
            if rev:
                self.pl_template_df["Category"] = (
                    self.pl_template_df["Category"]
                    .map(rev)
                    .fillna(self.pl_template_df["Category"])
                )
        # Preserve the leading full-width spaces while replacing the rest of the "Name" with "Account_Name"
        self.pl_template_df["Name"] = self.pl_template_df.apply(
            lambda row: ''.join(ch for ch in row["Name"] if ch == '\u3000' or ch == ' ') + row["Account_Name"],
            axis=1
        )
        # Display the modified DataFrame
        self.debug_print(self.pl_template_df.head())

    def _general_ledger_legacy(self):
        # 伝票単位で処理を行う
        df_temp = pd.DataFrame(self.amount_rows).copy()
        for transaction_id, group in df_temp.groupby(self.columns["伝票"]):
            # グループ内の先頭行の借方金額と貸方金額、摘要文を取得
            first_row = group.iloc[0]
            transction_date = first_row[self.columns["伝票日付"]]
            first_entry_id = first_row[self.columns["伝票番号"]]
            first_description = first_row[self.columns["摘要文"]]
            # 借方
            first_debit_acct_number = first_row[self.columns["借方科目コード"]]
            first_debit_acct_name = first_row[self.columns["借方科目名"]]
            first_debit_subacct_code = first_row[self.columns["借方補助科目コード"]]
            first_debit_subacct_name = first_row[self.columns["借方補助科目名"]]
            first_debit_department_code = first_row[self.columns["借方部門コード"]]
            first_debit_department_name = first_row[self.columns["借方部門名"]]
            first_debit_amount = first_row["Debit_Amount"] if pd.notna(first_row["Debit_Amount"]) else 0
            # 貸方
            first_credit_acct_number = first_row[self.columns["貸方科目コード"]]
            first_credit_acct_name = first_row[self.columns["貸方科目名"]]
            first_credit_subacct_code = first_row[self.columns["貸方補助科目コード"]]
            first_credit_subacct_name = first_row[self.columns["貸方補助科目名"]]
            first_credit_department_code = first_row[self.columns["貸方部門コード"]]
            first_credit_department_name = first_row[self.columns["貸方部門名"]]
            first_credit_amount = first_row["Credit_Amount"] if pd.notna(first_row["Credit_Amount"]) else 0
            # 借方と貸方の合計金額を計算
            total_debit_amount = total_credit_amount = 0
            for idx, row in group.iterrows():
                if pd.notna(row["Debit_Amount"]):
                    total_debit_amount += row["Debit_Amount"]
                if pd.notna(row["Credit_Amount"]):
                    total_credit_amount += row["Credit_Amount"]
            # 借方と貸方の合計金額が一致するか確認
            if total_debit_amount != total_credit_amount:
                self.trace_print(f"伝票貸借不一致 {transction_date} {transaction_id}: 借方金額 {total_debit_amount} 貸方金額 {total_credit_amount}")
            # 金額と摘要文の転記を行う
            for idx, row in group.iterrows():
                if first_debit_amount > first_credit_amount:
                    df_temp.at[idx, self.columns["借方科目コード"]] = first_debit_acct_number
                    df_temp.at[idx, self.columns["借方科目名"]] = first_debit_acct_name
                    df_temp.at[idx, "Debit_Amount"] = df_temp.at[idx, "Credit_Amount"]
                    df_temp.at[idx, self.columns["借方補助科目コード"]] = first_debit_subacct_code
                    df_temp.at[idx, self.columns["借方補助科目名"]] = first_debit_subacct_name
                    df_temp.at[idx, self.columns["借方部門コード"]] = first_debit_department_code
                    df_temp.at[idx, self.columns["借方部門名"]] = first_debit_department_name
                else:
                    df_temp.at[idx, self.columns["貸方科目コード"]] = first_credit_acct_number
                    df_temp.at[idx, self.columns["貸方科目名"]] = first_credit_acct_name
                    df_temp.at[idx, "Credit_Amount"] =  df_temp.at[idx, "Debit_Amount"]
                    df_temp.at[idx, self.columns["貸方補助科目コード"]] = first_credit_subacct_code
                    df_temp.at[idx, self.columns["貸方補助科目名"]] = first_credit_subacct_name
                    df_temp.at[idx, self.columns["貸方部門コード"]] = first_credit_department_code
                    df_temp.at[idx, self.columns["貸方部門名"]] = first_credit_department_name
                if pd.isna(row[self.columns["伝票番号"]]):
                    df_temp.at[idx, self.columns["伝票番号"]] = first_entry_id
                if pd.isna(row[self.columns["摘要文"]]):
                    df_temp.at[idx, self.columns["摘要文"]] = first_description
        self.debug_print("\n4. 最終的なDataFrame:")
        self.debug_print(df_temp.head())
        # 借方金額転記 Debit_Amountが記載されているエントリを選択し、コピーする
        debit_entry = df_temp[df_temp["Debit_Amount"].notna()].copy()
        # フィールド名を変更する
        debit_entry.rename(
            columns={
                self.columns["伝票日付"]: "Transaction_Date",
                self.columns["伝票番号"]: "Entry_ID",
                self.columns["摘要文"]: "Description",
                self.columns["借方科目コード"]: "Ledger_Account_Number",
                self.columns["借方科目名"]: "Ledger_Account_Name",
                self.columns["借方補助科目コード"]: "Subaccount_Code",
                self.columns["借方補助科目名"]: "Subaccount_Name",
                self.columns["借方部門コード"]: "Department_Code",
                self.columns["借方部門名"]: "Department_Name",
                self.columns["貸方科目コード"]: "Counterpart_Account_Number",
                self.columns["貸方科目名"]: "Counterpart_Account_Name",
                self.columns["貸方補助科目コード"]: "Counterpart_Subaccount_Code",
                self.columns["貸方補助科目名"]: "Counterpart_Subaccount_Name",
                self.columns["貸方部門コード"]: "Counterpart_Department_Code",
                self.columns["貸方部門名"]: "Counterpart_Department_Name",
            },
            inplace=True,
        )
        # Credit_Amountを0にする
        debit_entry["Credit_Amount"] = 0
        # 必要なカラムだけを選択
        debit_entry = debit_entry[
            [
                "Transaction_Date",
                "Entry_ID",
                "Description",
                "Ledger_Account_Number",
                "Ledger_Account_Name",
                "Subaccount_Code",
                "Subaccount_Name",
                "Department_Code",
                "Department_Name",
                "Debit_Amount",
                "Credit_Amount",
                "Counterpart_Account_Number",
                "Counterpart_Account_Name",
                "Counterpart_Subaccount_Code",
                "Counterpart_Subaccount_Name",
                "Counterpart_Department_Code",
                "Counterpart_Department_Name",
            ]
        ]
        self.debug_print("\n5D. 借方の転記結果:")
        self.debug_print(debit_entry.head())
        # 貸方金額転記 Creditt_Amountが記載されているエントリを選択し、コピーする
        credit_entry = df_temp[df_temp["Credit_Amount"].notna()].copy()
        # フィールド名を変更する
        credit_entry.rename(
            columns={
                self.columns["伝票日付"]: "Transaction_Date",
                self.columns["伝票番号"]: "Entry_ID",
                self.columns["摘要文"]: "Description",
                self.columns["貸方科目コード"]: "Ledger_Account_Number",
                self.columns["貸方科目名"]: "Ledger_Account_Name",
                self.columns["貸方補助科目コード"]: "Subaccount_Code",
                self.columns["貸方補助科目名"]: "Subaccount_Name",
                self.columns["貸方部門コード"]: "Department_Code",
                self.columns["貸方部門名"]: "Department_Name",
                self.columns["借方科目コード"]: "Counterpart_Account_Number",
                self.columns["借方科目名"]: "Counterpart_Account_Name",
                self.columns["借方補助科目コード"]: "Counterpart_Subaccount_Code",
                self.columns["借方補助科目名"]: "Counterpart_Subaccount_Name",
                self.columns["借方部門コード"]: "Counterpart_Department_Code",
                self.columns["借方部門名"]: "Counterpart_Department_Name",
            },
            inplace=True,
        )
        # Debit_Amountを0にする
        credit_entry["Debit_Amount"] = 0
        # 必要なカラムだけを選択
        credit_entry = credit_entry[
            [
                "Transaction_Date",
                "Entry_ID",
                "Description",
                "Ledger_Account_Number",
                "Ledger_Account_Name",
                "Subaccount_Code",
                "Subaccount_Name",
                "Department_Code",
                "Department_Name",
                "Debit_Amount",
                "Credit_Amount",
                "Counterpart_Account_Number",
                "Counterpart_Account_Name",
                "Counterpart_Subaccount_Code",
                "Counterpart_Subaccount_Name",
                "Counterpart_Department_Code",
                "Counterpart_Department_Name",
            ]
        ]
        self.debug_print("\n5C. 貸方の転記結果:")
        self.debug_print(credit_entry.head())
        # データフレームの数値列に対してNaNを0に変換
        debit_entry["Debit_Amount"] = debit_entry["Debit_Amount"].fillna(0).astype(int)
        debit_entry["Credit_Amount"] = debit_entry["Credit_Amount"].fillna(0).astype(int)
        credit_entry["Debit_Amount"] = credit_entry["Debit_Amount"].fillna(0).astype(int)
        credit_entry["Credit_Amount"] = credit_entry["Credit_Amount"].fillna(0).astype(int)
        # 借方と貸方の金額を集計する
        final_entry = pd.concat([debit_entry, credit_entry], ignore_index=True)
        # 月と科目番号でソート
        final_entry = final_entry.dropna(
            subset=[
                "Transaction_Date",
                "Entry_ID",
                "Ledger_Account_Number"
            ]
        ).sort_values(by=["Transaction_Date", "Entry_ID", "Ledger_Account_Number"])
        # 残高の計算
        balances = []
        balance_dict = {}  # 各勘定科目の残高を保持
        current_month = None
        for _, row in final_entry.iterrows():
            account_number = row["Ledger_Account_Number"]
            # 初期残高が存在しない場合、初期化
            if not account_number:
                self.trace_print(f"** general_ledger empty account_number{row}")
                continue
            if account_number not in balance_dict:
                balance_dict[account_number] = self.beginning_balances.get(account_number, 0)
        for _, row in final_entry.iterrows():
            account_number = row["Ledger_Account_Number"]
            if not account_number:
                self.trace_print(f"** general_ledger empty account_number{row}")
                continue
            transaction_date  = pd.Timestamp(row["Transaction_Date"])  # 日付をTimestamp型に変換
            transaction_month = transaction_date.strftime('%Y-%m')  # YYYY-MM形式の文字列に変換
            debit  = row["Debit_Amount"]
            credit = row["Credit_Amount"]
            # 月が変わったら月初残高を追加
            if current_month != transaction_month:
                current_month = transaction_month
                for acc_number in balance_dict:
                    # 各勘定科目について、月初残高を追加
                    account_info = self.etax_code_mapping_dict.get(acc_number, {"eTax_Account_Name": "❌ NOT FOUND"})
                    account_name = account_info["eTax_Account_Name"]
                    # account_name = self.etax_code_mapping_dict[acc_number]["eTax_Account_Name"]
                    if "en"== self.lang:
                        # `bs_template_df` から置き換え値を取得
                        replacement = self.bs_template_df.loc[
                            self.bs_template_df['Ledger_Account_Number'] == acc_number, 'Account_Name'
                        ]
                        if replacement.empty:
                            # `pl_template_df` から置き換え値を取得
                            replacement = self.pl_template_df.loc[
                                self.pl_template_df['Ledger_Account_Number'] == acc_number, 'Account_Name'
                            ]
                        if not replacement.empty:
                            account_name = replacement.values[0]
                    balances.append({
                        "Transaction_Date": self.get_month_start(transaction_date).strftime('%Y-%m-%d'),  # 月初日をYYYY-MM-DD形式で設定
                        "Description": "* beginning-of-month balance" if "en"==self.lang else "* 月初残高",
                        "Ledger_Account_Number": acc_number,
                        "Ledger_Account_Name": account_name,
                        "Subaccount_Code": "",
                        "Subaccount_Name": "",
                        "Department_Code": "",
                        "Department_Name": "",
                        "Debit_Amount": 0,
                        "Credit_Amount": 0,
                        "Counterpart_Account_Number": "",
                        "Counterpart_Account_Name": "",
                        "Counterpart_Subaccount_Code": "",
                        "Counterpart_Subaccount_Name": "",
                        "Counterpart_Department_Code": "",
                        "Counterpart_Department_Name": "",
                        "Balance": balance_dict[acc_number]
                    })
            # 勘定科目の種類に応じた残高計算
            account_category = self.etax_code_mapping_dict[account_number]['Category']
            # 勘定科目の方向に応じて残高を計算
            if "en"==self.lang:
                account_direction = self.account_direction_dict_en.get(account_category)
                if account_direction == "Increase on Debit":
                    balance_dict[account_number] += debit - credit
                elif account_direction == "Increase on Credit":
                    balance_dict[account_number] += credit - debit
                else:
                    # Define exception handling or default behaviour
                    self.trace_print(f"Unclassified account: {account_number}")
            else:
                account_direction = self.account_direction_dict.get(account_category)
                if account_direction == "借方増":
                    balance_dict[account_number] += debit - credit
                elif account_direction == "貸方増":
                    balance_dict[account_number] += credit - debit
                else:
                    # 例外処理やデフォルト動作を定義
                    self.trace_print(f"未分類の勘定科目: {account_number}")
            balances.append({
                "Transaction_Date": transaction_date.strftime('%Y-%m-%d'),
                "Description": row["Description"],
                "Ledger_Account_Number": account_number,
                "Ledger_Account_Name": row["Ledger_Account_Name"],
                "Subaccount_Code": row["Subaccount_Code"],
                "Subaccount_Name": row["Subaccount_Name"],
                "Department_Code": row["Department_Code"],
                "Department_Name": row["Department_Name"],
                "Debit_Amount": debit,
                "Credit_Amount": credit,
                "Counterpart_Account_Number": row["Counterpart_Account_Number"],
                "Counterpart_Account_Name": row["Counterpart_Account_Name"],
                "Counterpart_Subaccount_Code": row["Counterpart_Subaccount_Code"],
                "Counterpart_Subaccount_Name": row["Counterpart_Subaccount_Name"],
                "Counterpart_Department_Code": row["Counterpart_Department_Code"],
                "Counterpart_Department_Name": row["Counterpart_Department_Name"],
                "Balance": balance_dict[account_number]
            })
        self.balances = balances
        final_entry = pd.DataFrame(balances)
        for column in final_entry:
            if pd.api.types.is_numeric_dtype(final_entry[column]):
                final_entry[column] = final_entry[column].fillna(0)
            else:
                final_entry[column] = final_entry[column].fillna("")
        # 最終結果を表示
        self.debug_print("\n6. 総勘定元帳最終結果:")
        self.debug_print(final_entry.head())
        self.general_ledger_df = final_entry.copy()

    def general_ledger(self):
        """Build ledger rows from each journal detail's own account metadata."""
        source = pd.DataFrame(self.amount_rows).copy()
        transaction_col = "JP07a"
        line_col = "JP08a"
        date_col = "JP07a_GL03_03"
        entry_col = "JP07a_GL03_01"
        description_col = "JP08a_GL04_03"
        debit = {
            "account": "JP06e_GE24_01", "name": "JP06e_GE24_02",
            "subaccount": "JP05a_01", "subaccount_name": "JP05a_02",
            "department": "BS04fb_01", "department_name": "BS04fb_02",
            "amount": "Debit_Amount", "side": "Debit",
        }
        credit = {
            "account": "JP06f_GE24_01", "name": "JP06f_GE24_02",
            "subaccount": "JP05b_01", "subaccount_name": "JP05b_02",
            "department": "BS04fc_01", "department_name": "BS04fc_02",
            "amount": "Credit_Amount", "side": "Credit",
        }

        def clean(value):
            return "" if pd.isna(value) else str(value).strip()

        def amount(value):
            text = clean(value)
            return 0 if not text else int(float(text))

        def first_value(group, column):
            for value in group[column]:
                text = clean(value)
                if text:
                    return text
            return ""

        def side_has_amount(row, spec):
            return bool(clean(row[spec["account"]])) and amount(row[spec["amount"]]) != 0

        amount_mask = source.apply(
            lambda row: side_has_amount(row, debit) or side_has_amount(row, credit), axis=1
        )
        for column, label in ((transaction_col, "Transaction_ID"), (line_col, "Line_ID")):
            missing_mask = source.loc[amount_mask, column].apply(clean) == ""
            if missing_mask.any():
                rows = source.loc[amount_mask].loc[missing_mask].index.tolist()[:10]
                raise ValueError(f"Missing {label} on amount-bearing journal rows: {rows}")

        entries = []
        for transaction_id, group in source.groupby(transaction_col, sort=False):
            transaction_date = first_value(group, date_col)
            source_entry_id = first_value(group, entry_col)
            description = first_value(group, description_col)
            if not transaction_date:
                raise ValueError(f"Missing transaction date: Transaction_ID={transaction_id}")

            def opposite_row(current_row, opposite):
                if side_has_amount(current_row, opposite):
                    return current_row
                mask = group.apply(lambda candidate: side_has_amount(candidate, opposite), axis=1)
                candidates = group.loc[mask]
                return candidates.iloc[0] if not candidates.empty else None

            for _, row in group.iterrows():
                for own, opposite in ((debit, credit), (credit, debit)):
                    own_amount = amount(row[own["amount"]])
                    own_account = clean(row[own["account"]])
                    if not own_account or own_amount == 0:
                        continue
                    other = opposite_row(row, opposite)
                    entries.append({
                        "Transaction_ID": clean(row[transaction_col]),
                        "Line_ID": clean(row[line_col]),
                        "Entry_ID": clean(row[entry_col]) or source_entry_id,
                        "Ledger_Side": own["side"],
                        "Transaction_Date": clean(row[date_col]) or transaction_date,
                        "Description": clean(row[description_col]) or description,
                        "Ledger_Account_Number": own_account,
                        "Ledger_Account_Name": clean(row[own["name"]]),
                        "Subaccount_Code": clean(row[own["subaccount"]]),
                        "Subaccount_Name": clean(row[own["subaccount_name"]]),
                        "Department_Code": clean(row[own["department"]]),
                        "Department_Name": clean(row[own["department_name"]]),
                        "Debit_Amount": own_amount if own["side"] == "Debit" else 0,
                        "Credit_Amount": own_amount if own["side"] == "Credit" else 0,
                        "Counterpart_Account_Number": clean(other[opposite["account"]]) if other is not None else "",
                        "Counterpart_Account_Name": clean(other[opposite["name"]]) if other is not None else "",
                        "Counterpart_Subaccount_Code": clean(other[opposite["subaccount"]]) if other is not None else "",
                        "Counterpart_Subaccount_Name": clean(other[opposite["subaccount_name"]]) if other is not None else "",
                        "Counterpart_Department_Code": clean(other[opposite["department"]]) if other is not None else "",
                        "Counterpart_Department_Name": clean(other[opposite["department_name"]]) if other is not None else "",
                    })

        final_entry = pd.DataFrame(entries)
        if final_entry.empty:
            raise ValueError("No amount-bearing journal rows were available for the general ledger")
        final_entry["_transaction_sort"] = pd.to_numeric(final_entry["Transaction_ID"], errors="coerce")
        final_entry["_line_sort"] = pd.to_numeric(final_entry["Line_ID"], errors="coerce")
        final_entry["_side_sort"] = final_entry["Ledger_Side"].map({"Debit": 0, "Credit": 1})
        final_entry.sort_values(
            by=["Transaction_Date", "_transaction_sort", "Transaction_ID", "_line_sort", "Line_ID", "_side_sort"],
            inplace=True,
            kind="stable",
        )
        final_entry.drop(columns=["_transaction_sort", "_line_sort", "_side_sort"], inplace=True)

        balance_dict = {
            account: self.beginning_balances.get(account, 0)
            for account in final_entry["Ledger_Account_Number"].unique()
        }
        balances = []
        current_month = None
        for _, row in final_entry.iterrows():
            account_number = row["Ledger_Account_Number"]
            transaction_date = pd.Timestamp(row["Transaction_Date"])
            transaction_month = transaction_date.strftime("%Y-%m")
            if current_month != transaction_month:
                current_month = transaction_month
                for acc_number in sorted(balance_dict):
                    account_info = self.etax_code_mapping_dict.get(
                        acc_number, {"eTax_Account_Name": "NOT FOUND"}
                    )
                    account_name = account_info["eTax_Account_Name"]
                    if self.lang == "en":
                        replacement = self.bs_template_df.loc[
                            self.bs_template_df["Ledger_Account_Number"] == acc_number, "Account_Name"
                        ]
                        if replacement.empty:
                            replacement = self.pl_template_df.loc[
                                self.pl_template_df["Ledger_Account_Number"] == acc_number, "Account_Name"
                            ]
                        if not replacement.empty:
                            account_name = replacement.values[0]
                    balances.append({
                        "Transaction_ID": "", "Line_ID": "", "Entry_ID": "",
                        "Ledger_Side": "Opening",
                        "Transaction_Date": self.get_month_start(transaction_date).strftime("%Y-%m-%d"),
                        "Description": "* beginning-of-month balance" if self.lang == "en" else "* \u6708\u521d\u6b8b\u9ad8",
                        "Ledger_Account_Number": acc_number,
                        "Ledger_Account_Name": account_name,
                        "Subaccount_Code": "", "Subaccount_Name": "",
                        "Department_Code": "", "Department_Name": "",
                        "Debit_Amount": 0, "Credit_Amount": 0,
                        "Counterpart_Account_Number": "", "Counterpart_Account_Name": "",
                        "Counterpart_Subaccount_Code": "", "Counterpart_Subaccount_Name": "",
                        "Counterpart_Department_Code": "", "Counterpart_Department_Name": "",
                        "Balance": balance_dict[acc_number],
                    })

            debit_amount = int(row["Debit_Amount"])
            credit_amount = int(row["Credit_Amount"])
            account_category = self.etax_code_mapping_dict[account_number]["Category"]
            if self.lang == "en":
                direction = self.account_direction_dict_en.get(account_category)
                debit_direction = "Increase on Debit"
            else:
                asset_category = self.etax_code_mapping_dict["10A100020"]["Category"]
                direction = self.account_direction_dict.get(account_category)
                debit_direction = self.account_direction_dict.get(asset_category)
            if direction == debit_direction:
                balance_dict[account_number] += debit_amount - credit_amount
            elif direction:
                balance_dict[account_number] += credit_amount - debit_amount
            else:
                raise ValueError(f"Unclassified account direction: {account_number} {account_category}")
            output_row = row.to_dict()
            output_row["Balance"] = balance_dict[account_number]
            balances.append(output_row)

        self.balances = balances
        self.general_ledger_df = pd.DataFrame(balances).fillna("")

    def fill_account_dict(self):
        # 科目コードと科目名の対応辞書を作成
        account_dict = {
            f"{row['Ledger_Account_Number']} {row['Ledger_Account_Name']}": row[
                "Ledger_Account_Number"
            ]
            for _, row in self.general_ledger_df.iterrows()
            if pd.notna(row["Ledger_Account_Number"]) and pd.notna(row["Ledger_Account_Name"])
        }
        # Credit_Accountも含める
        account_dict.update(
            {
                f"{row['Counterpart_Account_Number']} {row['Counterpart_Account_Name']}": row[
                    "Counterpart_Account_Number"
                ]
                for _, row in self.general_ledger_df.iterrows()
                if pd.notna(row["Counterpart_Account_Number"]) and pd.notna(row["Counterpart_Account_Name"])
            }
        )
        self.account_dict = OrderedDict(sorted(account_dict.items()))

    def trial_balance_carried_forward(self):
        # 借方の金額を集計する
        debit_summary = (
            self.amount_rows.groupby(
                [
                    "Month",
                    self.columns["借方科目コード"],
                    self.columns["借方科目名"],
                ]
            )["Debit_Amount"]
            .sum()
            .reset_index()
        )
        debit_summary.rename(
            columns={
                self.columns["借方科目コード"]: "Ledger_Account_Number",
                self.columns["借方科目名"]: "Ledger_Account_Name",
            },
            inplace=True,
        )
        self.debug_print("\n4. 借方の集計結果:")
        self.debug_print(debit_summary.head())
        # 貸方の金額を集計する
        credit_summary = (
            self.amount_rows.groupby(
                [
                    "Month",
                    self.columns["貸方科目コード"],
                    self.columns["貸方科目名"],
                ]
            )["Credit_Amount"]
            .sum()
            .reset_index()
        )
        credit_summary.rename(
            columns={
                self.columns["貸方科目コード"]: "Ledger_Account_Number",
                self.columns["貸方科目名"]: "Ledger_Account_Name",
            },
            inplace=True,
        )
        self.debug_print("\n5. 貸方の集計結果:")
        self.debug_print(credit_summary.head())
        # データフレームの数値列に対してNaNを0に変換
        debit_summary["Debit_Amount"] = debit_summary["Debit_Amount"].fillna(0).astype(int)
        credit_summary["Credit_Amount"] = credit_summary["Credit_Amount"].fillna(0).astype(int)
        # 借方と貸方の金額を集計する
        temp_summary = pd.merge(
            debit_summary,
            credit_summary,
            on=[
                "Month",
                "Ledger_Account_Number",
            ],
            how="outer",
        )
        # NaNを0に変換し、借方金額および貸方金額の両方が0でない行のみを含める
        temp_summary["Debit_Amount"] = temp_summary["Debit_Amount"].fillna(0).astype(int)
        temp_summary["Credit_Amount"] = temp_summary["Credit_Amount"].fillna(0).astype(int)
        temp_summary = temp_summary[(temp_summary["Debit_Amount"] != 0) | (temp_summary["Credit_Amount"] != 0)]
        # Ledger_Account_Name列を統一
        if "Ledger_Account_Name_x" in temp_summary.columns and "Ledger_Account_Name_y" in temp_summary.columns:
            temp_summary["Ledger_Account_Name"] = temp_summary["Ledger_Account_Name_x"].fillna(temp_summary["Ledger_Account_Name_y"])
            temp_summary.drop(columns=["Ledger_Account_Name_x", "Ledger_Account_Name_y"], inplace=True)
        # 月と科目番号でソート
        temp_summary = temp_summary.sort_values(by=["Month", "Ledger_Account_Number"])
        # temp_summaryを表示する
        self.debug_print("\ntemp_summary:")
        self.debug_print(temp_summary.head())
        # temp_summary_dict = temp_summary.to_dict(orient="records")
        # 月ごとのユニークな値を取得
        unique_months = sorted(temp_summary["Month"].unique())
        # if DEBUG:
        #     save_dataframe_to_csv(temp_summary, "temp_summary.csv", "data/_PCA/dataframe")
        # 科目ごとにグループ化
        grouped = temp_summary.groupby("Ledger_Account_Number")
        # 各科目のBeginning_BalanceとEnding_Balanceを保存する辞書
        beginning_balances = {account_number: [0] * len(unique_months) for account_number in temp_summary["Ledger_Account_Number"].unique()}
        ending_balances = {account_number: [0] * len(unique_months) for account_number in temp_summary["Ledger_Account_Number"].unique()}
        # 各グループごとに計算を実行
        for account_number, group in grouped:
            # 科目ごとの月データを既存の月に基づいてソート
            group = group.sort_values(by="Month").set_index("Month")
            # 初期値の設定
            previous_ending_balance = self.beginning_balances.get(account_number, 0)
            # 全てのunique_monthsを順番に処理
            for month in unique_months:
                if month in group.index:
                    # 該当月のデータが存在する場合
                    row = group.loc[month]
                    beginning_balance = previous_ending_balance  # 前月のEnding_BalanceをBeginning_Balanceに設定
                    # 勘定科目の種類に応じた残高計算
                    debit_amount = pd.to_numeric(row.get("Debit_Amount", 0), errors="coerce").astype(np.int64).item()
                    credit_amount = pd.to_numeric(row.get("Credit_Amount", 0), errors="coerce").astype(np.int64).item()
                    # 勘定科目の方向に応じて残高を計算
                    account_number = row["Ledger_Account_Number"]
                    account_category = self.etax_code_mapping_dict.get(account_number, {}).get('Category', "Unknown")
                    if "en"==self.lang:
                        account_direction = self.account_direction_dict_en.get(account_category)
                        if account_direction == "Increase on Debit":
                            ending_balance = beginning_balance + debit_amount - credit_amount
                        elif account_direction == "Increase on Credit":
                            ending_balance = beginning_balance + credit_amount - debit_amount
                        else:
                            # Define exception handling or default behaviour
                            self.trace_print(f"Unclassified account: {account_number}")
                            ending_balance = beginning_balance
                    else:
                        account_direction = self.account_direction_dict.get(account_category)
                        if account_direction == "借方増":
                            ending_balance = beginning_balance + debit_amount - credit_amount
                        elif account_direction == "貸方増":
                            ending_balance = beginning_balance + credit_amount - debit_amount
                        else:
                            # 未分類の場合は残高を変更しない
                            self.debug_print(f"未分類の勘定科目: {account_number}")
                            ending_balance = beginning_balance
                else:
                    # 該当月のデータが存在しない場合、前月のEnding_Balanceを引き継ぐ
                    beginning_balance = previous_ending_balance
                    ending_balance = beginning_balance  # 取引がないため残高に変化なし
                # 結果を辞書に保存
                month_index = unique_months.index(month)
                beginning_balances[account_number][month_index] = beginning_balance
                ending_balances[account_number][month_index] = ending_balance
                # 現在のEnding_Balanceを次の月のBeginning_Balanceに使用するため更新
                previous_ending_balance = ending_balance

        temp_summary["Beginning_Balance"] = temp_summary.apply(
            lambda row: beginning_balances[row["Ledger_Account_Number"]][unique_months.index(row["Month"])],
            axis=1,
        )

        temp_summary["Ending_Balance"] = temp_summary.apply(
            lambda row: ending_balances[row["Ledger_Account_Number"]][unique_months.index(row["Month"])],
            axis=1,
        )

        # eTax_Categoryを計算して列を追加
        temp_summary["eTax_Category"] = temp_summary.apply(
            lambda row: self.etax_code_mapping_dict.get(row["Ledger_Account_Number"], {}).get("eTax_Category", None),
            axis=1,
        )

        if DEBUG:
            save_dataframe_to_csv(temp_summary, "temp_summary.csv", "data/_PCA/dataframe")
        # 集計結果を保存
        for column in temp_summary:
            if pd.api.types.is_numeric_dtype(temp_summary[column]):
                temp_summary[column] = temp_summary[column].fillna(0)
            else:
                temp_summary[column] = temp_summary[column].fillna("")
        self.summary_df = temp_summary.copy()
        # temp_summaryを表示する
        self.debug_print("\nself.summary_df:")
        self.debug_print(self.summary_df.head())

    def bs_pl(self):
        # ------------------------------------------------------------------
        # Build/override the account code → category mapping from the uploaded
        # BS/PL definition tables (HOT010_3.0_BS_10 / HOT010_3.0_PL_10).
        #
        # IMPORTANT: For B/S and P/L exports we must not rely on account_list
        # (eTax_Account_Code) alone, because journal rows use Ledger_Account_Number.
        # The definition tables provide the canonical hierarchy category per
        # Ledger_Account_Number.
        # ------------------------------------------------------------------
        if not hasattr(self, "etax_code_mapping_dict") or self.etax_code_mapping_dict is None:
            self.etax_code_mapping_dict = {}

        if "en" == self.lang:
            bs_prefix_category = {"10A": "Assets", "10B": "Liabilities", "10C": "Net assets"}
            pl_prefix_category = {"10D": "Revenue", "10E": "Expenses", "10F": "Other"}
        else:
            bs_prefix_category = {"10A": "資産", "10B": "負債", "10C": "純資産"}
            pl_prefix_category = {"10D": "収益", "10E": "費用", "10F": "その他"}

        def _broad_category(code: str) -> str:
            s = str(code)
            p = s[:3]
            if p in bs_prefix_category:
                return bs_prefix_category[p]
            if p in pl_prefix_category:
                return pl_prefix_category[p]
            return "Unknown"

        for _df in (getattr(self, "bs_template_df", None), getattr(self, "pl_template_df", None)):
            if _df is None:
                continue
            for _, _r in _df.iterrows():
                _code = _r.get("Ledger_Account_Number")
                if pd.isna(_code):
                    continue
                _code = str(_code)
                _hier_cat = _r.get("Category")
                _acct_name = _r.get("Account_Name") if pd.notna(_r.get("Account_Name")) else _r.get("Name")
                self.etax_code_mapping_dict[_code] = {
                    "Category": _broad_category(_code),
                    "eTax_Category": _hier_cat if pd.notna(_hier_cat) else "Unknown",
                    "eTax_Account_Name": _acct_name if pd.notna(_acct_name) else "Unknown",
                }

        # Debit
        debit_summary = (
            self.amount_rows.groupby(
                [
                    self.columns["借方科目コード"],
                    self.columns["借方科目名"],
                ]
            )["Debit_Amount"]
            .sum()
            .astype("int64")  # Ensure the result is stored as int64
            .reset_index()
        )
        debit_summary.rename(
            columns={
                self.columns["借方科目コード"]: "Ledger_Account_Number",
                self.columns["借方科目名"]: "Ledger_Account_Name",
            },
            inplace=True,
        )
        # Normalise code type for reliable dict lookup
        debit_summary["Ledger_Account_Number"] = debit_summary["Ledger_Account_Number"].astype(str)
        # Add Category and eTax_Category to debit_summary
        debit_summary["Category"] = debit_summary["Ledger_Account_Number"].map(
            lambda code: self.etax_code_mapping_dict.get(code, {}).get("Category", "Unknown")
        )
        debit_summary["eTax_Category"] = debit_summary["Ledger_Account_Number"].map(
            lambda code: self.etax_code_mapping_dict.get(code, {}).get("eTax_Category", "Unknown")
        )
        # Credit
        credit_summary = (
            self.amount_rows.groupby(
                [
                    self.columns["貸方科目コード"],
                    self.columns["貸方科目名"],
                ]
            )["Credit_Amount"]
            .sum()
            .astype("int64")  # Ensure the result is stored as int64
            .reset_index()
        )
        credit_summary.rename(
            columns={
                self.columns["貸方科目コード"]: "Ledger_Account_Number",
                self.columns["貸方科目名"]: "Ledger_Account_Name",
            },
            inplace=True,
        )
        # Normalise code type for reliable dict lookup
        credit_summary["Ledger_Account_Number"] = credit_summary["Ledger_Account_Number"].astype(str)
        # Add Category and eTax_Category to credit_summary
        credit_summary["Category"] = credit_summary["Ledger_Account_Number"].map(
            lambda code: self.etax_code_mapping_dict.get(code, {}).get("Category", "Unknown")
        )
        credit_summary["eTax_Category"] = credit_summary["Ledger_Account_Number"].map(
            lambda code: self.etax_code_mapping_dict.get(code, {}).get("eTax_Category", "Unknown")
        )
        # Merge debit_summary and credit_summary on Ledger_Account_Number
        combined_summary = pd.merge(
            debit_summary,
            credit_summary,
            on=["Ledger_Account_Number", "Ledger_Account_Name", "Category", "eTax_Category"],
            how="outer"
        )
        # Ensure amounts are handled as int64 by replacing NaN with 0 and converting
        combined_summary["Debit_Amount"] = combined_summary["Debit_Amount"].fillna(0).astype("int64")
        combined_summary["Credit_Amount"] = combined_summary["Credit_Amount"].fillna(0).astype("int64")
        combined_summary.rename(
            columns={
                "Debit_Amount": "Total_Debit",
                "Credit_Amount": "Total_Credit"
            },
            inplace=True
        )
        # Remove rows where both Debit_Amount and Credit_Amount are 0
        combined_summary = combined_summary.loc[
            (combined_summary["Total_Debit"] != 0) | (combined_summary["Total_Credit"] != 0)
        ]
        # Convert self.beginning_balances (assumed to be a dictionary) to a DataFrame
        beginning_balances_df = pd.DataFrame.from_dict(
            self.beginning_balances,
            orient="index",
            columns=["Beginning_Balance"]
        )
        # Reset the index and rename columns
        beginning_balances_df.reset_index(inplace=True)
        beginning_balances_df.rename(columns={"index": "Ledger_Account_Number"}, inplace=True)
        beginning_balances_df["Ledger_Account_Number"] = beginning_balances_df["Ledger_Account_Number"].astype(str)
        # Ensure "Beginning_Balance" is of type int64
        beginning_balances_df["Beginning_Balance"] = beginning_balances_df["Beginning_Balance"].astype("int64")
        # Add Category and eTax_Category to beginning_balances_df
        beginning_balances_df["Category"] = beginning_balances_df["Ledger_Account_Number"].map(
            lambda code: self.etax_code_mapping_dict.get(code, {}).get("Category", "Unknown")
        )
        beginning_balances_df["eTax_Category"] = beginning_balances_df["Ledger_Account_Number"].map(
            lambda code: self.etax_code_mapping_dict.get(code, {}).get("eTax_Category", "Unknown")
        )
        beginning_balances_df["Ledger_Account_Name"] = beginning_balances_df["Ledger_Account_Number"].map(
            lambda code: self.etax_code_mapping_dict.get(code, {}).get("eTax_Account_Name", "Unknown")
        )
        # Merge the beginning balances into the combined_summary DataFrame
        combined_summary = pd.merge(
            combined_summary,
            beginning_balances_df,
            on=["Ledger_Account_Number", "Ledger_Account_Name", "Category", "eTax_Category"],
            how="outer"  # Use 'outer' to keep all rows from both
        )
        # Replace NaN values in Beginning_Balance with 0
        combined_summary["Beginning_Balance"] = combined_summary["Beginning_Balance"].fillna(0).astype("int64")
        combined_summary["Total_Debit"] = combined_summary["Total_Debit"].fillna(0).astype("int64")
        combined_summary["Total_Credit"] = combined_summary["Total_Credit"].fillna(0).astype("int64")

        self.trace_print(sorted(combined_summary["Category"].dropna().unique()))

        """
        BS
        """
        # Iterate through self.bs_template_df to populate the dictionary
        i = 0  # Initialize the sequence counter
        for _, row in self.bs_template_df.iterrows():
            ledger_account_number = row["Ledger_Account_Number"]
            if pd.notna(ledger_account_number):  # Only process rows with valid Ledger_Account_Number
                i += 1
                self.etax_code_mapping_dict[ledger_account_number] = {
                    "seq": i,
                    "Category": self.etax_code_mapping_dict.get(ledger_account_number, {}).get("Category", "Unknown"),
                    "eTax_Category": row["Category"] if pd.notna(row["Category"]) else "Unknown",
                    "eTax_Account_Name": row["Account_Name"] if pd.notna(row["Account_Name"]) else (row["Name"] if pd.notna(row["Name"]) else "Unknown"),
                }

        # Separate into Balance Sheet and Income Statement items
        ASSET_CATS = {"資産", "Assets", "Asset"}
        LIAB_CATS  = {"負債", "Liabilities", "Liability"}
        EQUITY_CATS = {"純資産", "Equity", "Net assets", "Net Assets"}
        BS_CATS = ASSET_CATS | LIAB_CATS | EQUITY_CATS

        balance_sheet_df = combined_summary[combined_summary["Category"].isin(BS_CATS)][
            ["Ledger_Account_Number", "Ledger_Account_Name", "Category", "Beginning_Balance", "Total_Debit", "Total_Credit"]
        ]

        # Add the Ending_Balance column based on the Category
        balance_sheet_df["Ending_Balance"] = balance_sheet_df.apply(
            lambda row: (
                row["Beginning_Balance"] + row["Total_Debit"] - row["Total_Credit"]
                if row["Category"] in ASSET_CATS
                else row["Beginning_Balance"] + row["Total_Credit"] - row["Total_Debit"]
            ),
            axis=1,
        )

        # Merge the sheet's Ledger_Account_Number with balance_sheet_df to get balances
        self.bs_data_df = pd.merge(
            self.bs_template_df,
            balance_sheet_df[["Ledger_Account_Number", "Beginning_Balance", "Total_Debit", "Total_Credit", "Ending_Balance"]],
            on="Ledger_Account_Number",
            how="left"
        )
        # Keep rows where `type` is "T" or both balances are not NaN
        self.bs_data_df = self.bs_data_df[
            (self.bs_data_df["Type"] == "T") |
            (~self.bs_data_df[["Beginning_Balance", "Total_Debit", "Total_Credit", "Ending_Balance"]].isna().all(axis=1))
        ]
        # Ensure these columns are of type int64, setting NaN to 0
        columns_to_convert = ["Beginning_Balance", "Total_Debit", "Total_Credit", "Ending_Balance"]
        for column in columns_to_convert:
            self.bs_data_df[column] = pd.to_numeric(self.bs_data_df[column], errors="coerce").fillna(0).astype("int64")
        # Set Beginning_Balance, Total_Debit, Total_Credit, and Ending_Balance to 0 for rows where Type is "T"
        self.bs_data_df.loc[self.bs_data_df["Type"] == "T", ["Beginning_Balance", "Total_Debit", "Total_Credit", "Ending_Balance"]] = 0

        # BSの親子関係を構築する関数
        def build_bs_parent_child_hierarcy(bs_data_df, level_range=(1, 10), exclude_empty_children=True):
            # 初期化: 各レベルの最新の要素を保持（レベル範囲を指定）
            level_list = {lvl: None for lvl in range(level_range[0], level_range[1] + 1)}
            children_list = {}  # 親要素とその子要素を保持する辞書
            for _, row in bs_data_df.iterrows():
                level = row["Level"]
                account = row["Ledger_Account_Number"]
                # 現在のレベルに対応する要素を更新
                level_list[level] = account
                # 子要素リストを初期化（もし未登録なら）
                if account not in children_list:
                    _type = row["Type"]
                    beginning_balance = 0 if "T"==_type else row["Beginning_Balance"] if not pd.isna(row["Beginning_Balance"]) else 0
                    total_debit = 0 if "T"==_type else row["Total_Debit"] if not pd.isna(row["Total_Debit"]) else 0
                    total_credit = 0 if "T"==_type else row["Total_Credit"] if not pd.isna(row["Total_Credit"]) else 0
                    ending_balance = 0 if "T"==_type else row["Ending_Balance"] if not pd.isna(row["Ending_Balance"]) else 0
                    children_list[account] = {"Level": level, "Type": _type, "Beginning_Balance": beginning_balance, "Total_Debit": total_debit, "Total_Credit": total_credit, "Ending_Balance": ending_balance, "children": []}
                # 親要素が存在する場合、その子要素として追加
                if level > 1 and level_list[level - 1] is not None:
                    parent = level_list[level - 1]
                    children_list[parent]["children"].append(account)
                    children_list[account]["parent"] = parent
                else:
                    continue
            # 空の子要素リストを持つ親要素を除外（オプション）
            if exclude_empty_children:
                filtered_list = {k: v for k, v in children_list.items() if "T"==v["Type"] or np.int64(v["Total_Debit"]) > 0 or np.int64(v["Total_Credit"]) > 0}
            else:
                filtered_list = children_list
            return filtered_list

        # BSの親子関係を構築 統合処理: bs_template_dfを基準にbs_data_dfで上書き
        # インデックスを基準に高速な検索を可能にする
        merge_keys = ["Ledger_Account_Number"]
        # bs_data_df を辞書に変換 (merge_keysをキーとして)
        self.bs_data_df = self.bs_data_df.groupby("Ledger_Account_Number").sum().reset_index()
        bs_data_dict = self.bs_data_df.set_index(merge_keys).to_dict(orient="index")
        # 結果を格納するための DataFrame を初期化
        bs_result_df = self.bs_template_df.copy()
        # for ループで self.bs_template_df を処理
        for index, row in self.bs_template_df.iterrows():
            key = row["Ledger_Account_Number"]
            # データ辞書の行を取得
            bs_data_row = bs_data_dict.get(key, {})
            # 必要な列を更新
            for column in ["Beginning_Balance", "Total_Debit", "Total_Credit", "Ending_Balance"]:
                if column in row and not pd.isna(row[column]):
                    # 既存の値が存在する場合、値を保持
                    bs_result_df.at[index, column] = row[column]
                elif column in bs_data_row:
                    # bs_data_df の値で上書き
                    bs_result_df.at[index, column] = bs_data_row[column]

        bs_parent_child_hierarchy = build_bs_parent_child_hierarcy(bs_result_df, level_range=(1, 10), exclude_empty_children=True)

        print("Category unique:", sorted(combined_summary["Category"].dropna().unique()))
        print("BS template Category unique:", sorted(self.bs_template_df["Category"].dropna().unique()))
        print("PL template Category unique:", sorted(self.pl_template_df["Category"].dropna().unique()))

        # BSの結果を表示
        self.bs_dict = {}
        min_level = 10
        max_level = 0
        for parent, details in bs_parent_child_hierarchy.items():
            level = details["Level"]
            children = details["children"]
            self.debug_print(f"Level:{level} children:{children}")
            for child in children:
                if child in bs_parent_child_hierarchy:
                    result = bs_parent_child_hierarchy[child]
                    _type = result["Type"]
                    beginning_balance = result["Beginning_Balance"]
                    total_debit = result["Total_Debit"]
                    total_credit = result["Total_Credit"]
                    ending_balance = result["Ending_Balance"]
                    if level > max_level:
                        max_level = level
                    if min_level > level:
                        min_level = level
                    if "T"==_type or beginning_balance > 0 or total_debit > 0 or total_credit > 0 or ending_balance > 0:
                        self.debug_print(f"bs_parent_child_hierarchy level: {level}, Ledger_Account_Number: {child} Type:{_type} Parent: {parent}, Beginning_Balance: {beginning_balance} Total_Debit: {total_debit} Total_Credit: {total_credit} Ending_Balance: {ending_balance}")
                        self.bs_dict[child] = {
                            "Level": level,
                            "Type": _type,
                            "Ledger_Account_Number": child,
                            "Parent": parent,
                            "Category": self.etax_code_mapping_dict.get(str(child), {}).get("Category", "Unknown"),
                            "eTax_Category": self.etax_code_mapping_dict.get(str(child), {}).get("eTax_Category", "Unknown"),
                            "eTax_Account_Name": self.etax_code_mapping_dict.get(str(child), {}).get("eTax_Account_Name", "Unknown"),
                            "Beginning_Balance": beginning_balance,
                            "Total_Debit": total_debit,
                            "Total_Credit": total_credit,
                            "Ending_Balance": ending_balance
                        }
                else:
                    self.debug_print(f"B/S Ledger_Account_Number {child} not found.")
        # max_level から min_level の範囲でループ
        for level in range(max_level, min_level, -1):
            # target_level に該当する要素を抽出
            filtered_dict = {key: value for key, value in self.bs_dict.items() if value["Level"] == level}
            for key, row in filtered_dict.items():
                parent_key = row["Parent"]
                if not parent_key in self.bs_dict:
                    if 'None'==parent_key:
                        parent_key = "10X000000"
                    self.bs_dict[parent_key] = {"Level": level-1, "Parent": None, "Beginning_Balance": 0, "Total_Debit": 0, "Total_Credit": 0, "Ending_Balance": 0}
                parent = self.bs_dict[parent_key]
                if not parent:
                    continue
                beginning_balance = 0 if pd.isna(row["Beginning_Balance"]) else row["Beginning_Balance"]
                total_debit = 0 if pd.isna(row["Total_Debit"]) else row["Total_Debit"]
                total_credit = 0 if pd.isna(row["Total_Credit"]) else row["Total_Credit"]
                ending_balance = 0 if pd.isna(row["Ending_Balance"]) else row["Ending_Balance"]
                if beginning_balance > 0:
                    parent["Beginning_Balance"] += np.int64(beginning_balance)
                if total_debit > 0:
                    parent["Total_Debit"] += np.int64(total_debit)
                if total_credit > 0:
                    parent["Total_Credit"] += np.int64(total_credit)
                if ending_balance > 0:
                    parent["Ending_Balance"] += np.int64(ending_balance)
        # Filter bs_dict to remove entries with Beginning_Balance and Ending_Balance both 0
        self.bs_dict = {
            key: value
            for key, value in self.bs_dict.items()
            if not (value["Beginning_Balance"] == 0 and value["Ending_Balance"] == 0)
        }
        self.bs_dict = {
            key: {
                **value,
                "Ending_Balance": (
                    value["Beginning_Balance"] + value["Total_Credit"] - value["Total_Debit"]
                    if key.startswith("10A") and value.get("Type") == "T"
                    else value["Beginning_Balance"] + value["Total_Debit"] - value["Total_Credit"]
                    if (key.startswith("10B") or key.startswith("10C")) and value.get("Type") == "T"
                    else value["Ending_Balance"]
                )
            }
            for key, value in self.bs_dict.items()
            if not (value["Total_Debit"] == 0 and value["Total_Credit"] == 0)
        }
        # Add eTax_Category and eTax_Account_Name based on self.etax_code_mapping_dict
        for key, value in self.bs_dict.items():
            # Retrieve eTax_Category and eTax_Account_Name based on the Ledger_Account_Number (key)
            if key in self.etax_code_mapping_dict:
                etax_info = self.etax_code_mapping_dict[key]
                # Assuming etax_info contains "eTax_Category" and "eTax_Account_Name"
                value["seq"] = etax_info.get("seq", 0)
                value["eTax_Category"] = etax_info.get("eTax_Category", "Unknown")
                value["eTax_Account_Name"] = etax_info.get("eTax_Account_Name", "Unknown")
            else:
                # Default values if the key is not in etax_code_mapping_dict
                value["seq"] = 0
                value["eTax_Category"] = "Unknown"
                value["eTax_Account_Name"] = "Unknown"
        # Sort the dictionary by `seq`
        sorted_bs_dict = dict(
            sorted(self.bs_dict.items(), key=lambda item: item[1].get("seq", 0))
        )
        # Replace the original dictionary with the sorted one
        self.bs_dict = sorted_bs_dict
        """
        PL
        """
        # Iterate through self.pl_template_df to populate the dictionary
        i = 0  # Initialize the sequence counter
        for _, row in self.pl_template_df.iterrows():
            ledger_account_number = row["Ledger_Account_Number"]
            if pd.notna(ledger_account_number):  # Only process rows with valid Ledger_Account_Number
                i += 1
                self.etax_code_mapping_dict[ledger_account_number] = {
                    "seq": i,
                    "Category": self.etax_code_mapping_dict.get(ledger_account_number, {}).get("Category", "Unknown"),
                    "eTax_Category": row["Category"] if pd.notna(row["Category"]) else "Unknown",
                    "eTax_Account_Name": row["Account_Name"] if pd.notna(row["Account_Name"]) else (row["Name"] if pd.notna(row["Name"]) else "Unknown"),
                }

        REV_CATS   = {"収益", "Revenue"}
        EXP_CATS   = {"費用", "Expenses", "Expense"}
        income_statement_df = combined_summary[combined_summary["Category"].isin(REV_CATS | EXP_CATS)][
            ["Ledger_Account_Number", "Ledger_Account_Name", "Category", "Beginning_Balance", "Total_Debit", "Total_Credit"]
        ]
        # Add the Ending_Balance column based on the Category
        income_statement_df["Ending_Balance"] = income_statement_df.apply(
            lambda row: (
                row["Beginning_Balance"] + row["Total_Credit"] - row["Total_Debit"]
                if row["Category"] in REV_CATS
                else row["Beginning_Balance"] + row["Total_Debit"] - row["Total_Credit"]
            ),
            axis=1
        )

        if DEBUG:
            save_dataframe_to_csv(self.pl_template_df,'pl_template_df.csv','data/_PCA/dataframe')
            save_dataframe_to_csv(income_statement_df,'income_statement_df.csv','data/_PCA/dataframe')
        # Merge the sheet Ledger_Account_Number with the income_statement_df to get balances
        self.pl_data_df = pd.merge(
            self.pl_template_df,
            income_statement_df[["Ledger_Account_Number", "Beginning_Balance", "Total_Debit", "Total_Credit", "Ending_Balance"]],
            on="Ledger_Account_Number",
            how="left"
        )
        # Keep rows where `Type` is "T" or both balances are not NaN
        self.pl_data_df = self.pl_data_df[
            (self.pl_data_df["Type"] == "T") |
            (~self.pl_data_df[["Beginning_Balance", "Total_Debit", "Total_Credit", "Ending_Balance"]].isna().all(axis=1))
        ]
        # Ensure these columns are of type int64, setting NaN to 0
        columns_to_convert = ["Beginning_Balance", "Total_Debit", "Total_Credit", "Ending_Balance"]
        for column in columns_to_convert:
            self.pl_data_df[column] = pd.to_numeric(self.pl_data_df[column], errors="coerce").fillna(0).astype("int64")
        # Set Beginning_Balance, Total_Debit, Total_Credit, and Ending_Balance to 0 for rows where Type is "T"
        self.pl_data_df.loc[self.pl_data_df["Type"] == "T", ["Beginning_Balance", "Total_Debit", "Total_Credit", "Ending_Balance"]] = 0
        if DEBUG:
            save_dataframe_to_csv(self.pl_data_df,'pl_data_df.csv','data/_PCA/dataframe')

        # BSの親子関係を構築する関数
        def build_pl_parent_child_hierarcy(pl_result_df, level_range=(1, 10), exclude_empty_children=True):
            # 初期化: 各レベルの最新の要素を保持（レベル範囲を指定）
            level_list = {lvl: None for lvl in range(level_range[0], level_range[1] + 1)}
            children_list = {}  # 親要素とその子要素を保持する辞書
            for _, row in pl_result_df.iterrows():
                level = row["Level"]
                account = row["Ledger_Account_Number"]
                _type = row["Type"]
                # 現在のレベルに対応する要素を更新
                level_list[level] = account
                # 子要素リストを初期化（もし未登録なら）
                if account not in children_list:
                    total_debit = 0 if "T"==_type else row["Total_Debit"] if not pd.isna(row["Total_Debit"]) else 0
                    total_credit = 0 if "T"==_type else row["Total_Credit"] if not pd.isna(row["Total_Credit"]) else 0
                    ending_balance = 0 if "T"==_type else row["Ending_Balance"] if not pd.isna(row["Ending_Balance"]) else 0
                    children_list[account] = {"Level": level, "Type": _type, "Total_Debit": total_debit, "Total_Credit": total_credit, "Ending_Balance": ending_balance, "children": []}
                # 親要素が存在する場合、その子要素として追加
                if level > 1 and level_list[level - 1] is not None:
                    parent = level_list[level - 1]
                    children_list[parent]["children"].append(account)
                    children_list[account]["parent"] = parent
                else:
                    continue
            # Debit/Credtとも0の要素を除外
            if exclude_empty_children:
                filtered_list = {k: v for k, v in children_list.items() if "T"==v["Type"] or np.int64(v["Total_Debit"]) > 0 or np.int64(v["Total_Credit"]) > 0}
            else:
                filtered_list = children_list
            return filtered_list

        # PLの親子関係を構築 統合処理: pl_template_dfを基準にpl_data_dfで上書き
        # インデックスを基準に高速な検索を可能にする
        merge_keys = ["Ledger_Account_Number"]
        # pl_data_df を辞書に変換 (merge_keysをキーとして)
        pl_data_dict = self.pl_data_df.set_index(merge_keys).to_dict(orient="index")
        # 結果を格納するための DataFrame を初期化
        pl_result_df = self.pl_template_df.copy()
        # for ループで self.pl_template_df を処理
        for index, row in self.pl_template_df.iterrows():
            account_number = row["Ledger_Account_Number"]
            # データ辞書の行を取得
            pl_data_row = pl_data_dict.get(account_number, {})
            # 必要な列を更新
            for column in ["Total_Debit", "Total_Credit", "Ending_Balance"]:
                if column in row and not pd.isna(row[column]):
                    # 既存の値が存在する場合、値を保持
                    pl_result_df.at[index, column] = row[column]
                elif column in pl_data_row:
                    # pl_data_df の値で上書き
                    pl_result_df.at[index, column] = pl_data_row[column]

        pl_parent_child_hierarchy = build_pl_parent_child_hierarcy(self.pl_data_df, level_range=(1, 10), exclude_empty_children=True)
        if DEBUG:
            save_dataframe_to_csv(pl_result_df,'pl_result_df.csv','data/_PCA/dataframe')

        # PLの結果を表示
        self.pl_dict = {}
        min_level = 10
        max_level = 0
        for parent, details in pl_parent_child_hierarchy.items():
            level = details["Level"]
            children = details["children"]
            self.debug_print(f"Level:{level} children:{children}")
            for child in children:
                if child in pl_parent_child_hierarchy:
                    result = pl_parent_child_hierarchy[child]
                    _type = result["Type"]
                    total_debit = result["Total_Debit"]
                    total_credit = result["Total_Credit"]
                    ending_balance = result["Ending_Balance"]
                    if level > max_level:
                        max_level = level
                    if min_level > level:
                        min_level = level
                    if "T"==_type or total_debit > 0 or total_credit > 0 or ending_balance > 0:
                        self.debug_print(f"pl_parent_child_hierarchy level: {level}, Ledger_Account_Number: {child} Type:{_type} Parent: {parent}, Beginning_Balance: {beginning_balance} Total_Debit: {total_debit} Total_Credit: {total_credit} Ending_Balance: {ending_balance}")
                        self.pl_dict[child] = {
                            "Level": level,
                            "Type": _type,
                            "Ledger_Account_Number": child,
                            "Parent": parent,
                            "Category": self.etax_code_mapping_dict.get(str(child), {}).get("Category", "Unknown"),
                            "eTax_Category": self.etax_code_mapping_dict.get(str(child), {}).get("eTax_Category", "Unknown"),
                            "eTax_Account_Name": self.etax_code_mapping_dict.get(str(child), {}).get("eTax_Account_Name", "Unknown"),
                            "Beginning_Balance": beginning_balance,
                            "Total_Debit": total_debit,
                            "Total_Credit": total_credit,
                            "Ending_Balance": ending_balance
                        }
                else:
                    self.debug_print(f"P/L Ledger_Account_Number {child} not found.")

        # max_level から min_level の範囲でループ
        for level in range(max_level, min_level, -1):
            # target_level に該当する要素を抽出
            filtered_dict = {key: value for key, value in self.pl_dict.items() if value["Level"] == level}
            for key, row in filtered_dict.items():
                parent_key = row['Parent']
                if not parent_key in self.pl_dict:
                    if 'None'==parent_key:
                        parent_key = "10X000000"
                    self.pl_dict[parent_key] = {"Level": level-1, "Parent": None, "Beginning_Balance": 0, "Total_Debit": 0, "Total_Credit": 0, "Ending_Balance": 0}
                parent = self.pl_dict[row['Parent']]
                beginning_balance = 0 if pd.isna(row["Beginning_Balance"]) else row["Beginning_Balance"]
                total_debit = 0 if pd.isna(row["Total_Debit"]) else row["Total_Debit"]
                if total_debit > 0:
                    parent["Total_Debit"] += np.int64(total_debit).item()
                total_credit = 0 if pd.isna(row["Total_Credit"]) else row["Total_Credit"]
                if total_credit > 0:
                    parent["Total_Credit"] += np.int64(total_credit).item()
                ending_balance = 0 if pd.isna(row["Ending_Balance"]) else row["Ending_Balance"]
                if ending_balance > 0:
                    parent["Ending_Balance"] += np.int64(ending_balance).item()

        # Filter pl_dict to remove entries with Total_Debit and Total_Credit both 0
        self.pl_dict = {
            key: value
            for key, value in self.pl_dict.items()
            if not (value["Total_Debit"] == 0 and value["Total_Credit"] == 0)
        }
        # Filters out accounts where both Total_Debit and Total_Credit are zero
        # Recalculates "Ending_Balance" based on account type (10D or 10E) and Type="T"
        # Preserves all other key-value pairs
        # Uses dictionary comprehension for efficiency
        self.pl_dict = {
            key: {
                **value,
                "Ending_Balance": (
                    value["Total_Credit"] - value["Total_Debit"]
                    if key.startswith("10D") and value.get("Type") == "T"
                    else (
                        value["Total_Debit"] - value["Total_Credit"]
                        if key.startswith("10E") and value.get("Type") == "T"
                        else value["Ending_Balance"]
                    )
                ),
            }
            for key, value in self.pl_dict.items()
            if not (
                0 == value["Total_Debit"]
                and 0 == value["Total_Credit"]
                and 0 == value["Ending_Balance"]
            )
            and value.get("Type") in ["T", "1"]
        }

        # Add eTax_Category and eTax_Account_Name based on self.etax_code_mapping_dict
        for key, value in self.pl_dict.items():
            # Retrieve eTax_Category and eTax_Account_Name based on the Ledger_Account_Number (key)
            if key in self.etax_code_mapping_dict:
                etax_info = self.etax_code_mapping_dict[key]
                # Assuming etax_info contains "eTax_Category" and "eTax_Account_Name"
                value["seq"] = etax_info.get('seq', 0)
                value["eTax_Category"] = etax_info.get("eTax_Category", "Unknown")
                value["eTax_Account_Name"] = etax_info.get("eTax_Account_Name", "Unknown")
            else:
                # Default values if the key is not in etax_code_mapping_dict
                value["seq"] = 0
                value["eTax_Category"] = "Unknown"
                value["eTax_Account_Name"] = "Unknown"

        # Sort the dictionary by `seq`
        sorted_pl_dict = dict(
            sorted(self.pl_dict.items(), key=lambda item: item[1].get("seq", 0))
        )
        # Replace the original dictionary with the sorted one
        self.pl_dict = sorted_pl_dict

    def code2etax(self):
        # account_list.csv を読み込み、変換用の辞書を作成
        display_account_df = pd.read_csv(
            self.account_path, dtype={"Account_Code": str, "eTax_Account_Code": str}
        )
        display_account_df.columns = display_account_df.columns.str.strip()
        structural_account_df = pd.read_csv(
            self.structural_account_path,
            dtype={"Account_Code": str, "eTax_Account_Code": str},
        )
        structural_account_df.columns = structural_account_df.columns.str.strip()
        if set(display_account_df["Account_Code"]) != set(structural_account_df["Account_Code"]):
            raise ValueError("Display and structural account lists have different Account_Code sets")
        account_list_df = structural_account_df[["Account_Code", "eTax_Account_Code"]].merge(
            display_account_df[
                ["Account_Code", "Account_Name", "Category", "eTax_Account_Name", "eTax_Category"]
            ],
            on="Account_Code",
            how="left",
            validate="one_to_one",
        )
        account_list_df.columns = account_list_df.columns.str.strip()  # 列名の空白を除去
        # Account_Code をキーにして eTax_Account_Code と eTax_Account_Name を持つ辞書を作成
        # debug_print(f"Columns in account_list_df:{account_list_df.columns}")
        self.code_mapping_dict = account_list_df.set_index("Account_Code")[["eTax_Account_Code", "eTax_Account_Name"]].to_dict('index')
        # eTax_Account_Code をキーにして、'Category' と "eTax_Account_Name" を持つ辞書を作成
        # eTax_Account_Code で重複を排除し、最初の出現のみを残す
        etax_unique_df = account_list_df.drop_duplicates(subset="eTax_Account_Code", keep='first')
        # eTax_Account_Code をキーにして辞書を作成
        self.etax_code_mapping_dict = etax_unique_df.set_index("eTax_Account_Code")[['Category', "eTax_Account_Name", "eTax_Category"]].to_dict('index')
        # tidyGL.csv を読み込み、列名の空白を除去
        # Datatype を Pandas dtype に変換するマッピング
        datatype_mapping = {
            'Identifier': 'str',
            'Char': 'str',
            'Code': 'str',
            'Name': 'str',
            'Text': 'str',
            'Date': 'str',  # 日付形式は後で変換する場合に備えてstrにしておく
            'Time': 'str',
            'Decimal': 'float',
            'Integer': 'int64',
            'Indicator': 'str',
        }
        # 辞書から dtype を生成
        dtype_dict = {}
        for column_id, properties in self.LHM_dict.items():
            datatype = properties.get('datatype', '')  # datatype を取得
            if datatype in datatype_mapping:  # マッピングに存在する場合のみ処理
                dtype_dict[column_id] = datatype_mapping[datatype]
        self.tidy_gl_df = pd.read_csv(self.file_path, dtype=dtype_dict)
        self.tidy_gl_df.columns = self.tidy_gl_df.columns.str.strip()  # 列名の空白を除去
        # beginning_balance_pathを読み込む
        beginning_balance_df = pd.read_csv(self.etax_beginning_balance_path, dtype={"Account_Code": str, "eTax_Account_Code": str})
        # 勘定科目の開始残高を辞書に変換
        beginning_balance_df["Account_Code"] = beginning_balance_df["Account_Code"].astype(str)
        beginning_balance_df = beginning_balance_df.merge(
            account_list_df[["Account_Code", "eTax_Account_Code"]],
            on="Account_Code",
            how="left",
        )
        # Trial balance and general ledger are keyed by e-Tax account code, so
        # source-account opening balances must be folded into that same key.
        beginning_balances = (
            beginning_balance_df.dropna(subset=["eTax_Account_Code"])
            .groupby("eTax_Account_Code")["Beginning_Balance"]
            .sum()
            .to_dict()
        )
        self.beginning_balances = beginning_balances

    def csv2dataframe(self, param_file_path, root=None, gui=None):
        # GUI ありの起動経路では ExecutionMessage を表示（root/gui が渡された場合のみ）
        if root is not None and gui is not None:
            ExecutionMessage.start(root, gui.create_gui, root)
        # 開始、終了、経過時間ラベルを追加
        log_tracker.write_log_text("CSV to DataFrame")
        self.trading_partner_dict = {"supplier":{}, "customer": {}, "bank": {}}
        with open(self.trading_partner_path, mode='r', encoding='utf-8-sig') as csv_file:
            reader = csv.DictReader(csv_file)  # ヘッダー行をキーとして利用
            for row in reader:
                category = row['category']
                code = row['code']
                if "仕入先" == category:
                    self.trading_partner_dict["supplier"][code] = row
                elif "得意先" == category:
                    self.trading_partner_dict["customer"][code] = row
                elif "預金" in category:
                    self.trading_partner_dict["bank"][code] = row
        self.LHM_dict = {}
        with open(self.LHM_path, mode='r', encoding='utf-8-sig') as csv_file:
            reader = csv.DictReader(csv_file)  # ヘッダー行をキーとして利用
            for row in reader:
                id = row['id']
                self.LHM_dict[id] = row
        self.code2etax()
        df = pd.read_csv(self.file_path, encoding="utf-8-sig", dtype=str) # f tidy data csv
        df.columns = df.columns.str.strip()
        if self.structural_file_path != self.file_path:
            structural_df = pd.read_csv(
                self.structural_file_path, encoding="utf-8-sig", dtype=str
            )
            structural_df.columns = structural_df.columns.str.strip()
            if len(df) != len(structural_df):
                raise ValueError(
                    f"Display and structural journals have different row counts: {len(df)} != {len(structural_df)}"
                )

            def normalise_identifier(value):
                text = "" if pd.isna(value) else str(value).strip()
                return text[:-2] if text.endswith(".0") else text

            for identifier in ("JP07a", "JP08a"):
                display_ids = df[identifier].map(normalise_identifier)
                structural_ids = structural_df[identifier].map(normalise_identifier)
                mismatch = display_ids != structural_ids
                if mismatch.any():
                    indices = mismatch[mismatch].index.tolist()[:10]
                    raise ValueError(
                        f"Display and structural journals differ at {identifier}: rows {indices}"
                    )
            structural_columns = [
                "JP07a", "JP08a", "BS04fb", "JP05a", "BS04fc", "JP05b",
                "JP07a_GL03_01", "JP07a_GL03_03", "GL05c_01", "GE23c_01", "GE09eR_01",
                "JP06e_GE24_01", "JP05a_01", "JP05a_03", "GE05ku_01", "GE05kw_01",
                "JP02j_BS09_01", "BS04fb_01", "BS04fb_03",
                "JP06f_GE24_01", "JP05b_01", "JP05b_03", "GE05kz_01", "GE05kB_01",
                "JP02k_BS09_01", "BS04fc_01", "BS04fc_03",
            ]
            for column in structural_columns:
                df[column] = structural_df[column]
            account_name_map = {
                str(code): str(properties.get("eTax_Account_Name", ""))
                for code, properties in self.etax_code_mapping_dict.items()
            }
            for code_column, name_column in (
                ("JP06e_GE24_01", "JP06e_GE24_02"),
                ("JP06f_GE24_01", "JP06f_GE24_02"),
            ):
                mapped_names = df[code_column].map(account_name_map)
                df[name_column] = mapped_names.where(mapped_names.notna(), df[name_column])
        # 関連する列を適切なデータ型に変換する
        df[self.columns["明細行"]] = pd.to_numeric(df[self.columns["明細行"]], errors="coerce").astype("Int64")  # 明細行
        df[self.columns["借方補助科目"]] = pd.to_numeric(df[self.columns["借方補助科目"]], errors="coerce").astype("Int64")  # 借方補助科目
        df[self.columns["貸方補助科目"]] = pd.to_numeric(df[self.columns["貸方補助科目"]], errors="coerce").astype("Int64")  # 貸方補助科目
        df[self.columns["借方部門"]] = pd.to_numeric(df[self.columns["借方部門"]], errors="coerce").astype("Int64")  # 借方部門
        df[self.columns["貸方部門"]] = pd.to_numeric(df[self.columns["貸方部門"]], errors="coerce").astype("Int64")  # 貸方部門
        df[self.columns["借方金額"]] = pd.to_numeric(df[self.columns["借方金額"]], errors="coerce").astype("Int64")  # 借方金額
        df[self.columns["貸方金額"]] = pd.to_numeric(df[self.columns["貸方金額"]], errors="coerce").astype("Int64")  # 貸方金額
        # JP04a_GL02_03（伝票日付）を日時に変換し、非標準の日付フォーマットを処理する
        if self.columns["伝票日付"] in df.columns:
            df[self.columns["伝票日付"]] = pd.to_datetime(df[self.columns["伝票日付"]], errors="coerce").dt.strftime('%Y-%m-%d')
        # 月を抽出し、新しい列に追加する
        df["Month"] = pd.to_datetime(df[self.columns["伝票日付"]], errors="coerce").dt.to_period("M").astype(str)
        self.debug_print("1. 初期のDataFrame:")
        columns_to_show_df = [
            self.columns["伝票"],
            self.columns["明細行"],
            self.columns["伝票日付"],
            self.columns["伝票番号"],
            self.columns["借方補助科目"],
            self.columns["貸方補助科目"],
            self.columns["借方部門"],
            self.columns["貸方部門"],
            self.columns["借方金額"],
            self.columns["貸方金額"],
            self.columns["摘要文"],
        ]
        self.debug_print("\ndf")
        self.debug_print(
            df[columns_to_show_df].head()
        )
        # 伝票と明細行に値があり、借方補助科目、貸方補助科目、借方部門、貸方部門がすべてNaNの行を抽出し、対象の借方金額と貸方金額の値を収集する
        initial_rows = df[
            (pd.notna(df[self.columns["伝票"]]))
            & (pd.notna(df[self.columns["明細行"]]))
            & (pd.isna(df[self.columns["借方補助科目"]]))
            & (pd.isna(df[self.columns["貸方補助科目"]]))
            & (pd.isna(df[self.columns["借方部門"]]))
            & (pd.isna(df[self.columns["貸方部門"]]))
        ][
            [
                self.columns["伝票"],
                self.columns["明細行"],
                self.columns["借方金額"],
                self.columns["貸方金額"],
            ]
        ].drop_duplicates()
        # マージ前に対象列を明確にするために列名を変更する
        initial_rows = initial_rows.rename(
            columns={
                self.columns["借方金額"]: "Debit_Amount",
                self.columns["貸方金額"]: "Credit_Amount"
            }
        )
        self.debug_print("\n2. initial_rows:")
        self.debug_print(initial_rows.head())
        # 伝票に値があり、明細行、借方補助科目、貸方補助科目、借方部門、貸方部門がすべてNaNの行を抽出し、伝票日付を取り出す。
        entry_df = df[
            (pd.notna(df[self.columns["伝票"]]))
            & (pd.isna(df[self.columns["明細行"]]))
            & (pd.isna(df[self.columns["借方補助科目"]]))
            & (pd.isna(df[self.columns["貸方補助科目"]]))
            & (pd.isna(df[self.columns["借方部門"]]))
            & (pd.isna(df[self.columns["貸方部門"]]))
        ][
            [self.columns["伝票"], self.columns["伝票日付"], self.columns["伝票番号"], "Month"]
        ].drop_duplicates()
        # マージ前に列名を明確にするために列名を変更する
        entry_df = entry_df.rename(
            columns={
                self.columns["伝票日付"]: f"{self.columns['伝票日付']}_value",
                self.columns["伝票番号"]: f"{self.columns['伝票番号']}_value",
                "Month": "Month_value",
            }
        )
        # 対象の金額の値をメインのDataFrameにマージする
        line_df = pd.merge(df, initial_rows, on=[self.columns["伝票"], self.columns["明細行"]], how="left")
        # JP04a_GL02_03（伝票日付）の値をメインのDataFrameにマージする
        line_df = pd.merge(line_df, entry_df, on=self.columns["伝票"], how="left")
        # 正しいJP04a_GL02_03（伝票日付）の値でメインのDataFrameを更新する
        line_df[self.columns["伝票日付"]] = line_df[f"{self.columns['伝票日付']}_value"].combine_first(line_df[self.columns["伝票日付"]])
        line_df[self.columns["伝票番号"]] = line_df[f"{self.columns['伝票番号']}_value"].combine_first(line_df[self.columns["伝票番号"]])
        line_df["Month"] = line_df["Month_value"].combine_first(line_df["Month"])
        # マージに使用した一時的な列を削除する
        line_df.drop(columns=[f"{self.columns['伝票日付']}_value", f"{self.columns['伝票日付']}_value", "Month_value"], inplace=True)
        self.debug_print("\nline_df")
        columns_to_show = [
            self.columns["伝票"],self.columns["明細行"],
            self.columns["借方補助科目"],self.columns["貸方補助科目"],
            self.columns["借方科目コード"], self.columns["借方補助科目コード"], "Debit_Amount",
            self.columns["貸方科目コード"], self.columns["貸方補助科目コード"], "Credit_Amount"
        ]  # 必要なカラムを指定
        self.debug_print(line_df[columns_to_show].head())
        # 借方補助科目コードに値があり、借方補助区分が["補助科目", "sub-account"]にある行を抽出し、借方補助科目コード、借方補助科目名を取り出す。
        self.debug_print("\ndf")
        columns_to_show_df = [
            self.columns["伝票"], self.columns["明細行"],
            self.columns["借方補助科目"], self.columns["貸方補助科目"],
            self.columns["借方科目コード"], self.columns["借方補助科目コード"], self.columns["借方補助区分"], self.columns["借方金額"],
            self.columns["貸方科目コード"], self.columns["貸方補助科目コード"], self.columns["貸方補助区分"], self.columns["貸方金額"]
        ]  # 必要なカラムを指定
        self.debug_print(df[columns_to_show_df].head())
        df[self.columns["借方補助区分"]] = df[self.columns["借方補助区分"]].fillna('')
        debit_subaccount_df = df[
            (pd.notna(df[self.columns["借方補助科目コード"]]))
            & (df[self.columns["借方補助区分"]].isin(["補助科目", "sub-account"]))
        ][
            [self.columns["伝票"], self.columns["明細行"], self.columns["借方補助科目コード"], self.columns["借方補助科目名"]]
        ].drop_duplicates()
        # マージ前に列名を明確にするために列名を変更する
        debit_subaccount_df = debit_subaccount_df.rename(
            columns={
                self.columns["借方補助科目コード"]: f"{self.columns['借方補助科目コード']}_value",
                self.columns["借方補助科目名"]: f"{self.columns['借方補助科目名']}_value",
            }
        )
        self.debug_print("\ndebit_subaccount_df")
        self.debug_print(debit_subaccount_df.head())
        # 補助科目の値をメインのDataFrameにマージする
        if not debit_subaccount_df.empty:
            line_df = pd.merge(line_df, debit_subaccount_df, on=[self.columns["伝票"], self.columns["明細行"]], how="left")
            line_df[self.columns["借方補助科目コード"]] = line_df[f"{self.columns['借方補助科目コード']}_value"].combine_first(line_df[self.columns["借方補助科目コード"]])
            line_df[self.columns["借方補助科目名"]] = line_df[f"{self.columns['借方補助科目名']}_value"].combine_first(line_df[self.columns["借方補助科目名"]])
            line_df.drop(columns=[
                f"{self.columns['借方補助科目コード']}_value", f"{self.columns['借方補助科目名']}_value"
            ], inplace=True)
            columns_to_show =[
                self.columns["伝票"], self.columns["明細行"],
                self.columns["借方補助科目"], self.columns["借方補助科目コード"], self.columns["借方補助科目名"],
            ]
            self.debug_print("\nline_df")
            self.debug_print(line_df[columns_to_show].head())
        # 貸方補助科目コードに値があり、貸方補助区分が["補助科目", "sub-account"]にある行を抽出し、貸方補助科目コード、貸方補助科目名を取り出す。
        df[self.columns["貸方補助区分"]] = df[self.columns["貸方補助区分"]].fillna('')
        credit_subaccount_df = df[
            (pd.notna(df[self.columns["貸方補助科目コード"]]))
            & (df[self.columns["貸方補助区分"]].isin(["補助科目", "sub-account"]))
        ][
            [
                self.columns["伝票"], self.columns["明細行"], self.columns["貸方補助科目コード"], self.columns["貸方補助科目名"],
            ]
        ].drop_duplicates()
        # マージ前に列名を明確にするために列名を変更する
        credit_subaccount_df = credit_subaccount_df.rename(
            columns={
                self.columns["貸方補助科目コード"]: f"{self.columns['貸方補助科目コード']}_value",
                self.columns["貸方補助科目名"]: f"{self.columns['貸方補助科目名']}_value",
            }
        )
        # 補助科目の値をメインのDataFrameにマージする
        if not credit_subaccount_df.empty:
            line_df = pd.merge(line_df, credit_subaccount_df, on=[self.columns["伝票"], self.columns["明細行"]], how="left")
            line_df[self.columns["貸方補助科目コード"]] = line_df[f"{self.columns['貸方補助科目コード']}_value"].combine_first(line_df[self.columns["貸方補助科目コード"]])
            line_df[self.columns["貸方補助科目名"]] = line_df[f"{self.columns['貸方補助科目名']}_value"].combine_first(line_df[self.columns["貸方補助科目名"]])
            line_df.drop(columns=[
                f"{self.columns['貸方補助科目コード']}_value",  f"{self.columns['貸方補助科目名']}_value"
            ], inplace=True)
            columns_to_show = [self.columns["伝票"],self.columns["明細行"],
                                self.columns["借方補助科目"],self.columns["貸方補助科目"],
                                self.columns["借方科目コード"], self.columns["借方補助科目コード"],"Debit_Amount",
                                self.columns["貸方科目コード"], self.columns["貸方補助科目コード"],"Debit_Amount"]  # 必要なカラムを指定
            self.debug_print("\nline_df")
            self.debug_print(line_df[columns_to_show].head())
        # BS04cZ（借方部門）に値があり、BS04cZ_03（借方部門区分）が"部門"の行を抽出し、借方部門コードと借方部門名を取り出す。
        debit_department_df = df[
            (df[self.columns["借方部門"]] > 0)
            & (df[self.columns["借方部門区分"]] == "部門")
        ][
            [
                self.columns["伝票"], self.columns["明細行"], self.columns["借方部門コード"], self.columns["借方部門名"],
            ]
        ].drop_duplicates()
        # マージ前に列名を明確にするために列名を変更する
        debit_department_df = debit_department_df.rename(
            columns={
                self.columns["借方部門コード"]: f"{self.columns['借方部門コード']}_value",
                self.columns["借方部門名"]: f"{self.columns['借方部門名']}_value",
            }
        )
        # 部門の値をメインのDataFrameにマージする
        if not debit_department_df.empty:
            line_df = pd.merge(line_df, debit_department_df, on=[self.columns["伝票"], self.columns["明細行"]], how="left")
            line_df[self.columns["借方部門コード"]] = line_df[f"{self.columns['借方部門コード']}_value"].combine_first(line_df[self.columns["借方部門コード"]])
            line_df[self.columns["借方部門名"]] = line_df[f"{self.columns['借方部門名']}_value"].combine_first(line_df[self.columns["借方部門名"]])
            line_df.drop(columns=[f"{self.columns['借方部門コード']}_value", f"{self.columns['借方部門名']}_value"], inplace=True)
            columns_to_show =[
                self.columns["伝票"], self.columns["明細行"],
                self.columns["借方部門"], self.columns["借方部門コード"], self.columns["借方部門名"],
            ]
            self.debug_print("\nline_df")
            self.debug_print(line_df[columns_to_show].head())
        # BS04c0（貸方部門）に値があり、BS04c0_03（貸方部門区分）が"部門"の行を抽出し、貸方部門コードと貸方部門名を取り出す。
        credit_department_df = df[
            (df[self.columns["貸方部門"]] > 0)
            & (df[self.columns["貸方部門区分"]] == "部門")
        ][
            [
                self.columns["伝票"], self.columns["明細行"], self.columns["貸方部門コード"], self.columns["貸方部門名"],
            ]
        ].drop_duplicates()
        # マージ前に列名を明確にするために列名を変更する
        credit_department_df = credit_department_df.rename(
            columns={
                self.columns["貸方部門コード"]: f"{self.columns['貸方部門コード']}_value",
                self.columns["貸方部門名"]: f"{self.columns['貸方部門名']}_value",
            }
        )
        # 補助科目の値をメインのDataFrameにマージする
        if not credit_department_df.empty:
            line_df = pd.merge(line_df, credit_department_df, on=[self.columns["伝票"], self.columns["明細行"]], how="left")
            line_df[self.columns["貸方部門コード"]] = line_df[f"{self.columns['貸方部門コード']}_value"].combine_first(line_df[self.columns["貸方部門コード"]])
            line_df[self.columns["貸方部門名"]] = line_df[f"{self.columns['貸方部門名']}_value"].combine_first(line_df[self.columns["貸方部門名"]])
            line_df.drop(columns=[f"{self.columns['貸方部門コード']}_value", f"{self.columns['貸方部門名']}_value"], inplace=True)
            columns_to_show =[
                self.columns["伝票"], self.columns["明細行"],
                self.columns["貸方部門"], self.columns["貸方部門コード"], self.columns["貸方部門名"],
            ]
            self.debug_print("\nline_df")
            self.debug_print(line_df[columns_to_show].head())
        # マージと更新後のDataFrameを表示する
        self.debug_print("\n3. マージと更新後のDataFrame:")
        self.debug_print(line_df.head())
        # OR条件で借方金額または貸方金額のいずれかに値があるものを抽出する
        self.amount_rows = line_df[
            (pd.notna(line_df[self.columns["伝票"]]))
            & (pd.notna(line_df[self.columns["明細行"]]))
            & (pd.isna(line_df[self.columns["借方補助科目"]]))
            & (pd.isna(line_df[self.columns["貸方補助科目"]]))
            & (pd.isna(line_df[self.columns["借方部門"]]))
            & (pd.isna(line_df[self.columns["貸方部門"]]))
            & (pd.notna(line_df["Debit_Amount"]) | pd.notna(line_df["Credit_Amount"]))
        ].drop_duplicates()
        self.debug_print("\namount_rows OR条件で借方金額または貸方金額のいずれかに値があるもの:")
        columns_to_show = [
            self.columns["伝票"],self.columns["明細行"],
            self.columns["借方補助科目"],self.columns["貸方補助科目"],
            self.columns["借方科目コード"], self.columns["借方補助科目コード"], "Debit_Amount",
            self.columns["貸方科目コード"], self.columns["貸方補助科目コード"], "Credit_Amount"
        ]
        self.debug_print(self.amount_rows[columns_to_show].head())

        self.amount_rows = self.amount_rows[
            [
                self.columns["伝票"],
                self.columns["明細行"],
                "Month",
                self.columns["伝票日付"],
                self.columns["伝票番号"],
                self.columns["摘要文"],
                # 借方
                self.columns["借方科目コード"],
                self.columns["借方科目名"],
                "Debit_Amount",
                self.columns["借方税区分コード"],
                self.columns["借方税区分名"],
                self.columns["借方消費税額"],
                self.columns["借方補助科目コード"],
                self.columns["借方補助科目名"],
                self.columns["借方部門コード"],
                self.columns["借方部門名"],
                # 貸方
                self.columns["貸方科目コード"],
                self.columns["貸方科目名"],
                "Credit_Amount",
                self.columns["貸方税区分コード"],
                self.columns["貸方税区分名"],
                self.columns["貸方消費税額"],
                self.columns["貸方補助科目コード"],
                self.columns["貸方補助科目名"],
                self.columns["貸方部門コード"],
                self.columns["貸方部門名"],
            ]
        ]
        self.amount_rows[self.columns["借方消費税額"]] = self.amount_rows[self.columns["借方消費税額"]].fillna(0).astype(float).astype(int)
        self.amount_rows[self.columns["貸方消費税額"]] = self.amount_rows[self.columns["貸方消費税額"]].fillna(0).astype(float).astype(int)
        # List of columns to process
        columns_to_process = [
            self.columns['借方補助科目コード'],
            self.columns['貸方補助科目コード'],
            self.columns['借方部門コード'],
            self.columns['貸方部門コード']
        ]
        # Replace NaN with "" and keep 0 as "0"
        for column in columns_to_process:
            self.amount_rows[column] = (
                self.amount_rows[column]
                .apply(lambda x: "0" if x == 0 else "" if pd.isna(x) else str(int(float(x))))
            )
        self.debug_print(f"\nself.amount_rows \n{self.amount_rows}")

        log_tracker.write_log_text("e-Tax Template")
        self.etax_template()

        log_tracker.write_log_text("General Ledger")
        self.general_ledger()

        log_tracker.write_log_text("Account Dict")
        self.fill_account_dict()

        log_tracker.write_log_text("Trial Balance")
        self.trial_balance_carried_forward()

        log_tracker.write_log_text("BS/PL")
        self.bs_pl()

        for column in self.amount_rows:
            if pd.api.types.is_numeric_dtype(self.amount_rows[column]):
                self.amount_rows[column] = self.amount_rows[column].fillna(0)
            else:
                self.amount_rows[column] = self.amount_rows[column].fillna("")
        log_tracker.write_log_text("END CSV to DataFrame")


class GUI:
    def __init__(self, root):
        self.style = ttk.Style()
        self.root = root
        self.base_frame = None
        self.previous_selection = None
        self.original_data = []  # TreeViewの元のデータを保持するリスト
        self.params = None
        self.columns = None
        self.amount_df = None
        self.general_ledger_df = None
        self.summary_df = None
        self.log_text = None
        self.month_title = None
        self.month_combobox = None
        self.account_title = None
        self.account_combobox = None
        self.combobox = None
        self.menu = None
        self.frame0 = None
        self.frame1 = None
        self.frame2 = None
        self.frame3 = None
        self.frame4 = None
        self.frame_number = None
        self.result_tree0 = None
        self.result_tree1 = None
        self.result_tree2 = None
        self.result_tree3 = None
        self.result_tree4 = None
        self.columns0 = None
        self.columns1 = None
        self.columns2 = None
        self.columns3 = None
        self.columns4 = None
        self.result_tree1_data = None
        self.column_visible = False
        self.combobox_width = None
        self.width_dimension = 20
        self.width_code = 30
        self.width_account = 80
        self.width_subaccount = 40
        self.width_amount = 90
        self.width_taxamount = 40
        self.width_name = 160
        self.width_taxname = 60
        self.width_longname = 180
        self.width_codename = 80
        self.width_text = 360
        self.width_month = 60
        self.width_date = 80
        self.line = None
        self.no_account_label = None
        self.no_month_label = None

        self.style = ttk.Style()
        self.style.theme_use("default")  # 必要ならテーマを設定
        # カスタムスタイルを設定
        bg_color = self.get_background_color()
        self.style.configure("Background.TCombobox", fieldbackground=bg_color, background=bg_color)

        with open(param_file_path, "r", encoding="utf-8-sig") as param_file:
            params = json.load(param_file)
        self.params = params
        self.DEBUG = 1 == params["DEBUG"]
        self.TRACE = 1 == params["TRACE"]
        self.file_path = params["e-tax_file_path"]
        self.lang = params["lang"]

    def debug_print(self, message):
        if self.DEBUG:
            print(message)

    def trace_print(self, message):
        if self.TRACE:
            print(message)

    # -------------------------------------------------------------------
    # Localisation helpers
    # -------------------------------------------------------------------
    def L(self, key: str) -> str:
        """
        Resolve a UI label key to the current language string.
        Falls back to the key itself if undefined.
        """
        lang = "en" if getattr(self, "lang", "ja") == "en" else "ja"
        return UI_LABELS.get(key, {}).get(lang, key)

    def menu_values(self):
        """Return menu labels in the current language."""
        lang = "en" if getattr(self, "lang", "ja") == "en" else "ja"
        return [v[lang] for v in UI_MENU.values()]

    def menu_key_from_label(self, label: str):
        """Map a displayed menu label (ja/en) back to its internal key."""
        for k, v in UI_MENU.items():
            if label == v.get("ja") or label == v.get("en"):
                return k
        return None

    def menu_label_from_key(self, key: str):
        """Map an internal menu key to the displayed label for current language."""
        lang = "en" if getattr(self, "lang", "ja") == "en" else "ja"
        v = UI_MENU.get(key)
        return v.get(lang) if v else key

    # -------------------------------------------------------------------
    # UI sizing / fonts
    # -------------------------------------------------------------------
    def apply_ui_sizes(self):
        """
        Apply common UI sizing customisations:
          - enlarge button/combobox widths
          - set log font size
          - make the log area expandable
        """
        # Defaults (can be overridden via params / attributes)
        self.ui_font_family = getattr(self, "ui_font_family", "Meiryo UI")
        self.ui_font_size = getattr(self, "ui_font_size", 11)
        self.log_font_family = getattr(self, "log_font_family", self.ui_font_family)
        self.log_font_size = getattr(self, "log_font_size", 12)

        # Width knobs (override in create_gui if desired)
        self.button_width = getattr(self, "button_width", 12)
        self.combo_width = getattr(self, "combo_width", 28)
        self.menu_combo_width = getattr(self, "menu_combo_width", 44)
        self.filter_combo_width = getattr(self, "filter_combo_width", 32)
        self.entity_combo_width = getattr(self, "entity_combo_width", self.filter_combo_width)

        # Apply widget sizes if they exist
        for wname in ("show_button", "load_button", "reset_button",
                      "search_button", "reset_search_button",
                      "view_button", "toggle_column_button",
                      "save_button", "toggle_language_button"):
            w = getattr(self, wname, None)
            if w is not None:
                try:
                    w.configure(width=self.button_width)
                except Exception:
                    pass

        for wname in ("combobox", "account_combobox", "month_combobox"):
            w = getattr(self, wname, None)
            if w is not None:
                try:
                    w.configure(width=self.combo_width)
                except Exception:
                    pass

        # Log widget font + grid behaviour
        if getattr(self, "log_text", None) is not None:
            try:
                f = font.Font(family=self.log_font_family, size=self.log_font_size)
                self.log_text.configure(font=f)
            except Exception:
                pass

            # Ensure log area expands with the window
            try:
                self.log_text.grid_configure(sticky="nsew")
                # base_frame may be None during early init
                if getattr(self, "base_frame", None) is not None:
                    self.base_frame.grid_columnconfigure(5, weight=1)
                    self.base_frame.grid_rowconfigure(0, weight=1)
                    self.base_frame.grid_rowconfigure(1, weight=1)
            except Exception:
                pass

    def debug_grid(self):
        # グリッドの行数と列数を指定（必要に応じて変更）
        num_rows = 6  # 仮の行数
        num_columns = 6  # 仮の列数
        # デバッグ用に各セルを塗り分ける
        for row in range(num_rows):
            for column in range(num_columns):
                # ラベルウィジェットを作成し、背景色を設定
                color = f'#{(row * 20 % 255):02x}{(column * 20 % 255):02x}AA'
                label = tk.Label(self.base_frame, text=f"R{row}C{column}", bg=color, relief="solid", width=10, height=2)
                # グリッドに配置
                label.grid(row=row, column=column, sticky="nsew")
        # グリッドの列と行のリサイズ設定
        for col in range(num_columns):
            self.base_frame.grid_columnconfigure(col, weight=1)
        for row in range(num_rows):
            self.base_frame.grid_rowconfigure(row, weight=1)

    def toggle_language(self):
        if self.lang == "en":
            self.lang = "ja"
            tidy_data.lang = "ja"
        else:
            self.lang = "en"
            tidy_data.lang = "en"
        self.update_labels()

    def update_labels(self):
        """
        Update labels based on the selected language using the common localisation maps:
          - UI_LABELS (term-by-term labels)
          - UI_MENU   (menu items)
        """
        # Buttons / labels
        self.show_button.config(text=self.L("show"))
        self.account_title.config(text=self.L("account_title"))
        self.month_title.config(text=self.L("month_title"))
        self.load_button.config(text=self.L("load_params"))
        self.reset_button.config(text=self.L("reset_selection"))

        # Menu combobox: keep selection stable via internal key
        current_label = self.combobox.get()
        current_key = self.menu_key_from_label(current_label)
        self.combobox["values"] = self.menu_values()
        if current_key is not None:
            self.combobox.set(self.menu_label_from_key(current_key))

        # Search / action controls
        self.search_label.config(text=self.L("search_term"))
        self.search_button.config(text=self.L("search"))
        self.reset_search_button.config(text=self.L("reset_search"))
        self.view_button.config(text=self.L("view_data"))
        self.toggle_column_button.config(text=self.L("toggle_code_cols"))
        self.save_button.config(text=self.L("save_csv"))
        self.toggle_language_button.config(text=self.L("toggle_lang"))

        # Tree headings use translation map (implemented separately)
        self.update_tree_headings()

        # window title
        if hasattr(self, "root") and self.root:
            self.root.title(self.L("app_title"))

        # entity radio buttons
        if hasattr(self, "radio_customer") and self.radio_customer:
            self.radio_customer.configure(text=self.L("entity_customer"))
        if hasattr(self, "radio_supplier") and self.radio_supplier:
            self.radio_supplier.configure(text=self.L("entity_supplier"))
        if hasattr(self, "radio_bank") and self.radio_bank:
            self.radio_bank.configure(text=self.L("entity_bank"))


    def load_json(self):
        # Open file dialog to select a JSON file
        param_file_path = filedialog.askopenfilename(filetypes=[("JSON files", "*.json")])
        if not param_file_path:
            return
        # Load the JSON file
        with open(param_file_path, "r", encoding="utf-8-sig") as param_file:
            params = json.load(param_file)
            self.columns = params['columns']
        tidy_data.csv2dataframe(param_file_path, root=getattr(self, "root", None), gui=self)

    def reset_filters(self, event=None):
        self.account_combobox.set('')
        self.month_combobox.set('')

    def show_frame(self, frame, frame_number):
        self.frame_number = frame_number
        frame.tkraise()
        if 0 == frame_number: # Journal Entry
            self.hide_account()
            self.show_month()
            self.show_reset()
        elif 1 == frame_number: # General Ledger
            self.show_account()
            self.show_month()
            self.show_reset()
        elif 2 == frame_number: # Trial Balance
            self.hide_account()
            self.show_month()
            self.show_reset()
        elif 3 == frame_number: # Balance Sheet
            self.hide_account()
            self.hide_month()
            self.hide_reset()
        elif 4 == frame_number: # Profit and Loss
            self.hide_account()
            self.hide_month()
            self.hide_reset()

    def get_cell_width_in_chars(self, frame, row, column, font_name="TkDefaultFont"):
        # セルのピクセル幅を取得
        bbox = frame.grid_bbox(row, column)
        if not bbox:
            return 0  # セルが存在しない場合
        cell_width_pixels = bbox[2]
        # フォントを作成
        current_font = font.Font(name=font_name, exists=True)  # 現在のフォントを取得
        char_width = current_font.measure("A")  # 'A' 1文字分の幅をピクセル単位で取得
        # 文字数に換算
        if char_width > 0:
            return cell_width_pixels // char_width
        return 0

    def get_background_color(self):
        # ttk.Frameの背景色を取得
        style = ttk.Style()
        return style.lookup("TFrame", "background") or "SystemButtonFace"

    def show_account(self):
        self.account_title.config(fg="black")
        account_dict = tidy_data.get_account_dict()
        accounts = [key.split(" ", 1)[1] for key in account_dict.keys()]
        self.account_combobox["values"] = accounts
        self.account_combobox.configure(style="TCombobox")

    def hide_account(self):
        self.account_title.config(fg=self.account_title.cget("bg"))
        bg_color = self.get_background_color()
        self.account_combobox["values"] = [""]
        self.account_combobox.set('')
        self.account_combobox.configure(style="Background.TCombobox")

    def show_month(self):
        self.month_title.config(fg="black")
        summary_df = tidy_data.get_summary_df()
        months = sorted(summary_df["Month"].unique())
        self.month_combobox["values"] = [""] + months
        self.month_combobox.configure(style="TCombobox")

    def hide_month(self):
        self.month_title.config(fg=self.month_title.cget("bg"))
        self.month_combobox["values"] = [""]
        self.month_combobox.set('')
        self.month_combobox.configure(style="Background.TCombobox")

    def show_reset(self):
        self.reset_button.config(fg="black")

    def hide_reset(self):
        self.reset_button.config(fg=self.reset_button.cget("bg"))

    def search_keyword(self):
        ExecutionMessage.start(root, self.search_keyword_body, None)

    def search_keyword_body(self):
        def filter_data(original_data, search_term, columns_to_search):
            """
            指定されたカラムに基づいてデータをフィルタリングする関数
            """
            filtered_data = []
            for row in original_data:
                if any(pd.notna(row[col]) and search_term in row[col].lower() for col in columns_to_search):
                    filtered_data.append(row)
            return filtered_data
        search_term = self.search_entry.get().lower()
        frame_number = self.frame_number
        if 0 == frame_number:
            result_tree = self.result_tree0
            """
            0   row[self.columns["伝票"]],
            1   row[self.columns["明細行"]],
            2   row[self.columns["伝票日付"]],
            3   row[self.columns["伝票番号"]],
            *4  row[self.columns["摘要文"]],
            5   row[self.columns["借方科目コード"]],
            *6  row[self.columns["借方科目名"]],
            7   row["Debit_Amount"],
            8   row[self.columns["借方税区分コード"]],
            9   row[self.columns["借方税区分名"]],
            10  row[self.columns["借方消費税額"]],
            11  row[self.columns["貸方科目コード"]],
            *12 row[self.columns["貸方科目名"]],
            13  row["Credit_Amount"],
            14  row[self.columns["貸方税区分コード"]],
            15  row[self.columns["貸方税区分名"]],
            16  row[self.columns["貸方消費税額"]],
            17  row[self.columns["借方補助科目コード"]],
            *18 row[self.columns["借方補助科目名"]],
            19  row[self.columns["借方部門コード"]],
            *20 row[self.columns["借方部門名"]],
            21  row[self.columns["貸方補助科目コード"]],
            *22 row[self.columns["貸方補助科目名"]],
            23  row[self.columns["貸方部門コード"]],
            *24 row[self.columns["貸方部門名"]],
            """
            columns_to_search = [
                4,  # 摘要文
                6,  # 借方科目名
                12, # 貸方科目名
                18, # 借方補助科目名
                20, # 借方部門名
                22, # 貸方補助科目名
                24  # 貸方部門名
            ]
        elif 1 == frame_number:
            result_tree = self.result_tree1
            """
            0   row["Transaction_Date"],  # オリジナルの日付列を使用
            *1  row["Description"],
            2   row["Debit_Amount"],
            3   row["Credit_Amount"],
            4   row["Balance"],
            5   row["Counterpart_Account_Number"],
            *6  row["Counterpart_Account_Name"],
            7   row["Subaccount_Code"],
            *8  row["Subaccount_Name"],
            9   row["Department_Code"],
            *10 row["Department_Name"],
            11  row["Counterpart_Subaccount_Code"],
            *12 row["Counterpart_Subaccount_Name"],
            13  row["Counterpart_Department_Code"],
            *14 row["Counterpart_Department_Name"],
            """
            columns_to_search = [
                1,  # 摘要文
                6,  # 相手科目
                8,  # 補助科目名
                10, # 部門
                12, # 相手補助科目名
                14  # 相手部門
            ]
        else:
            return
        # 共通関数を呼び出してフィルタリング
        filtered_data = filter_data(self.original_data, search_term, columns_to_search)
        filtered_data = filtered_data.drop_duplicates().reset_index(drop=True)
        for i in result_tree.get_children():
            result_tree.delete(i)
        for row in filtered_data:
            result_tree.insert("", "end", values=self.format_searched_row(row, frame_number))
        ExecutionMessage.end()

    def reset_search(self):
        frame_number = self.frame_number
        if 0 == frame_number:
            result_tree = self.result_tree0
        elif 1 == frame_number:
            result_tree = self.result_tree1
        else:
            return
        # TreeViewの内容をクリア
        for item in result_tree.get_children():
            result_tree.delete(item)
        # self.original_dataからデータを挿入
        for row in self.original_data:
            result_tree.insert("", "end", values=row)

    def view_data(self, event=None):
        frame_number = self.frame_number
        self.debug_print(f"frame number: {frame_number}")
        # Get the selected item
        if 0 == frame_number:
            item = self.result_tree0.selection()[0]
            row_data = self.result_tree0.item(item, "values")
            description = row_data[4]  # Assuming "Description" is the 4th column
        elif 1 == frame_number:
            item = self.result_tree1.selection()[0]
            row_data = self.result_tree1.item(item, "values")
            description = row_data[1]  # Assuming "Description" is the 2nd column
        else:
            return
        pdf_path = f'LedgerExplorer/slip/{description}.pdf'
        pdf_path = os.path.abspath(pdf_path)
        if os.path.isfile(pdf_path):
            webbrowser.open(f'file://{pdf_path}')
        else:
            if self.lang == "en":
                messagebox.showwarning("Warning", "No corresponding PDF found.")
            else:
                messagebox.showwarning("警告", "該当するPDFが見つかりませんでした。")

    def save_dict2csv(self, output_file, data_dict):
        # Save the dictionary as a CSV
        with open(output_file, mode='w', encoding='utf-8-sig', newline='') as file:
            writer = csv.writer(file)
            # Write the header row
            header = ["Ledger_Account_Number"] + list(next(iter(data_dict.values())).keys())
            writer.writerow(header)
            # Write each row of data
            for key, values in data_dict.items():
                row = [key] + [values.get(col, "") for col in header[1:]]
                writer.writerow(row)

    def save_csv(self):
        # ディレクトリダイアログを表示して保存ディレクトリを選択
        directory = filedialog.askdirectory()
        if directory:
            try:
                # DataFrameをCSVファイルに保存
                amount_path = os.path.join(directory, 'data_amount.csv')
                general_ledger_path = os.path.join(directory, 'data_general_ledger.csv')
                summary_path = os.path.join(directory, 'data_summary.csv')
                amount_rows = tidy_data.get_amount_rows()
                amount_df = pd.DataFrame(amount_rows).copy()
                amount_df.to_csv(amount_path, index=False, encoding="utf-8-sig")
                tidy_data.get_general_ledger_df().to_csv(general_ledger_path, index=False, encoding="utf-8-sig")
                tidy_data.get_summary_df().to_csv(summary_path, index=False, encoding="utf-8-sig")
                input_BS_path = tidy_data.BS_path[1+tidy_data.BS_path.index('/'):] # BS Template CSV
                output_BS_path = os.path.join(directory, input_BS_path)
                self.save_dict2csv(output_BS_path, tidy_data.bs_dict)
                input_PL_path = tidy_data.PL_path[1+tidy_data.PL_path.index('/'):]  # PL Template CSV
                output_PL_path = os.path.join(directory, input_PL_path)
                self.save_dict2csv(output_PL_path, tidy_data.pl_dict)
                if self.lang == "en":
                    messagebox.showinfo("Success", f"DataFrames have been saved as CSV files to {amount_path}, {general_ledger_path}, {summary_path}, {output_BS_path}, {output_PL_path}.")
                else:
                    messagebox.showinfo("成功", f"DataFrameをCSVファイルとして {amount_path}, {general_ledger_path}, {summary_path}, {output_BS_path}, {output_PL_path} に保存しました。")
            except Exception as e:
                if self.lang == "en":
                    messagebox.showerror("Error", f"Failed to save: {str(e)}")
                else:
                    messagebox.showerror("エラー", f"保存に失敗しました: {str(e)}")

    def on_combobox_select(self, event=None):
        selected_option = self.combobox.get()
        log_tracker.write_log_text(selected_option)
        if self.lang == "en":
            if selected_option == "Journal Entry":
                self.show_frame(self.frame0, 0)
            elif selected_option == "General Ledger":
                self.show_frame(self.frame1, 1)
            elif selected_option == "Trial Balance":
                self.show_frame(self.frame2, 2)
            elif selected_option == "Balance Sheet":
                self.show_frame(self.frame3, 3)
            elif selected_option == "Profit and Loss":
                self.show_frame(self.frame4, 4)
        else:
            if selected_option == "仕訳帳":
                self.show_frame(self.frame0, 0)
            elif selected_option == "総勘定元帳画面":
                self.show_frame(self.frame1, 1)
            elif selected_option == "試算表画面":
                self.show_frame(self.frame2, 2)
            elif selected_option == "貸借対照表":
                self.show_frame(self.frame3, 3)
            elif selected_option == "損益計算書":
                self.show_frame(self.frame4, 4)

    def create_base(self, root):
        # 13インチMacBook Air（M2/M3）は1,470×956ピクセル、14インチMacBook Pro（M3/M3 Pro/M3 Max）は1,512×982ピクセルの解像度
        root.geometry("1450x950")
        root.update_idletasks()  # ウィンドウのレイアウトを更新
        root_width = root.winfo_width()  # rootウィンドウの幅を取得
        root_height = root.winfo_height()  # rootウィンドウの高さを取得
        self.previous_selection = None
        if self.lang == "en":
            root.title("Accounting Ledgers")
        else:
            root.title("会計帳簿")
        self.base_frame = tk.Frame(root)
        self.base_frame.pack(side="top", fill="x", padx=10, pady=10)
        # self.base_frameの幅と高さをrootのサイズに合わせる
        self.base_frame.config(width=root_width, height=root_height)

    def insert_data(self, filtered_df, result_tree, frame_number):
        # 複数タグを設定
        result_tree.tag_configure("emphasis", background="gray", foreground="white")
        result_tree.tag_configure("normal", background="white", foreground="black")
        self.original_data = []
        for index, row in filtered_df.iterrows():
            # フォーマット済みのデータを取得
            formatted_row = self.format_row(row, frame_number)
            # TreeView にデータを挿入
            tag = "normal"
            if "Description" in row and "* "==row["Description"][:2]:
                tag = "emphasis"   
            result_tree.insert("", "end", values=formatted_row, tags=(tag,))
            # フォーマット済みのデータを保存
            self.original_data.append(formatted_row)
            # レスポンス性を維持するための更新処理
            if index % 100 == 0:
                result_tree.update_idletasks()  # Update the GUI to keep it responsive

    def show_results(self, frame_number, event=None):
        """ 処理を開始し、メッセージウィンドウを表示 """
        # メッセージウィンドウを表示
        ExecutionMessage.start(root, self.show_results_body, frame_number)

    def show_results_body(self, frame_number, event=None):
        if self.lang == "en":
            log_tracker.write_log_text(f"START {self.menu[frame_number]}")
        else:
            log_tracker.write_log_text(f"{self.menu[frame_number]} 開始")
        self.columns = tidy_data.get_columns()
        if 0 == frame_number: # Journal Entry
            selected_month = self.month_combobox.get()
            amount_rows = tidy_data.get_amount_rows()
            self.amount_df = pd.DataFrame(amount_rows).copy()
            result_tree = self.result_tree0
            if selected_month:
                target_month = pd.Period(selected_month)
                filtered_df = self.amount_df[self.amount_df['Month'] == str(target_month)].copy()
            else:
                filtered_df = self.amount_df.copy()
            if filtered_df.empty:
                if self.lang == "en":
                    messagebox.showwarning("Warning", "No data found.")
                else:
                    messagebox.showwarning("警告", "データが見つかりません。")
                return
        elif 1 == frame_number: # General Ledger
            selected_account = self.account_combobox.get()
            selected_month = self.month_combobox.get()
            account_dict = self.account_dict = tidy_data.get_account_dict()
            self.general_ledger_df = tidy_data.get_general_ledger_df().copy()
            # Transaction_Date列をdatetime型に変換してTransaction_Date_dtの列に保存
            self.general_ledger_df['Transaction_Date_dt'] = pd.to_datetime(self.general_ledger_df['Transaction_Date'])
            # Transaction_Date列をYYYY-MM形式に変換してTransaction_Monthの列に保存
            self.general_ledger_df['Transaction_Month'] = self.general_ledger_df['Transaction_Date_dt'].dt.to_period('M').astype(str)
            result_tree = self.result_tree1
            if selected_month:
                filtered_df = self.general_ledger_df[self.general_ledger_df['Transaction_Month'] == selected_month].copy()
            else:
                filtered_df = self.general_ledger_df.copy()
            if selected_account:
                account_number = next((v for k, v in account_dict.items() if selected_account in k.split(' ', 1)[1]), None)
                if account_number:
                    filtered_df = filtered_df[filtered_df["Ledger_Account_Number"] == account_number]
            else:
                if self.lang == "en":
                    messagebox.showwarning("Warning", "Please select an account name.")
                else:
                    messagebox.showwarning("警告", "科目名を選択してください。")
                return
            if filtered_df.empty:
                if self.lang == "en":
                    messagebox.showwarning("Warning", "No data found.")
                else:
                    messagebox.showwarning("警告", "データが見つかりません。")
                return
        elif 2 == frame_number: # Trial Balance
            selected_month = self.month_combobox.get()
            if not selected_month:
                if self.lang == "en":
                    messagebox.showwarning("Warning", "Please select a target month.")
                else:
                    messagebox.showwarning("警告", "対象月を選択してください。")
                return
            target_month = pd.Period(selected_month)
            self.summary_df = tidy_data.get_summary_df().copy()
            filtered_df = self.summary_df[self.summary_df['Month'] == str(target_month)]
            result_tree = self.result_tree2
        elif 3 == frame_number:  # Balance Sheet (BS)
            result_tree = self.result_tree3
            # Convert dictionary to DataFrame
            filtered_df = pd.DataFrame.from_dict(tidy_data.bs_dict, orient='index').reset_index()
        elif 4 == frame_number:  # Profit and Loss (PL)
            result_tree = self.result_tree4
            # Convert dictionary to DataFrame
            filtered_df = pd.DataFrame.from_dict(tidy_data.pl_dict, orient='index').reset_index()
        # Treeviewの行削除
        for i in result_tree.get_children():
            result_tree.delete(i)
        self.insert_data(filtered_df, result_tree, frame_number)
        if self.lang == "en":
            log_tracker.write_log_text(f"END {self.menu[frame_number]} listing")
        else:
            log_tracker.write_log_text(f"{self.menu[frame_number]} 表示終了")
        ExecutionMessage.end()

    def update_tree_headings(self):
        """
        Update Treeview headings using the common term-by-term map UI_COL_LABELS.
        This avoids invalid column index errors by using the Treeview's own
        column definitions as the source of truth.
        """
        tree_map = {
            0: getattr(self, "result_tree0", None),
            1: getattr(self, "result_tree1", None),
            2: getattr(self, "result_tree2", None),
            3: getattr(self, "result_tree3", None),
            4: getattr(self, "result_tree4", None),
        }
        tree = tree_map.get(getattr(self, "frame_number", None))
        if tree is None:
            return

        lang = "en" if getattr(self, "lang", "ja") == "en" else "ja"

        cols = list(tree.cget("columns"))
        valid = set(cols) | {"#0"}

        for col_id in cols:
            if col_id not in valid:
                continue

            tr = UI_COL_LABELS.get(col_id)
            if tr:
                label = tr.get(lang) or tr.get("ja") or col_id
            else:
                # Fallback: keep existing heading (if any), otherwise use column id
                current = tree.heading(col_id)
                label = current.get("text") or col_id

            try:
                tree.heading(col_id, text=label)
            except tk.TclError:
                # Defensive: skip any unexpected invalid columns
                continue

    def format_searched_row(self, row, frame_number):
        if 0 == frame_number:
            formatted_row = (
                row[0],  # 伝票
                row[1],  # 明細行
                row[2],  # 伝票日付
                row[3],  # 伝票番号
                row[4],  # 摘要文
                # 借方
                row[5],  # 借方科目コード
                row[6],  # 借方科目名
                (  #'Debit_Amount'
                    f"{row[7]:,.0f}"
                    if pd.notna(row[7])
                    and pd.notna(row[7])
                    and row[6] != 0
                    and isinstance(row[7], (int, float))
                    else row[7]
                ),
                row[8],  # 借方税区分コード
                row[9],  # 借方税区分名
                (  # 借方消費税額
                    f"{row[10]:,.0f}"
                    if pd.notna(row[10])
                    and pd.notna(row[10])
                    and row[10] != 0
                    and isinstance(row[10], (int, float))
                    else row[10]
                ),  # 貸方
                row[11],  # 貸方科目コード
                row[12],  # 貸方科目名
                (  # 'Credit_Amount'
                    f"{row[13]:,.0f}"
                    if pd.notna(row[13])
                    and pd.notna(row[13])
                    and row[13] != 0
                    and isinstance(row[13], (int, float))
                    else row[13]
                ),
                row[14],  # 貸方税区分コード
                row[15],  # 貸方税区分名
                (  # 貸方消費税額
                    f"{row[16]:,.0f}"
                    if pd.notna(row[16])
                    and pd.notna(row[16])
                    and row[16] != 0
                    and isinstance(row[16], (int, float))
                    else row[16]
                ),
                row[17],  # 借方補助科目コード
                row[18],  # 借方補助科目名
                row[19],  # 借方部門コード
                row[20],  # 借方部門名
                row[21],  # 貸方補助科目コード
                row[22],  # 貸方補助科目名
                row[23],  # 貸方部門コード
                row[24],  # 貸方部門名
            )
        elif 1 == frame_number:
            formatted_row = (
                row[0],  # Transaction_Date  # オリジナルの日付列を使用
                row[1],  # Description
                (  # 'Debit_Amount'
                    f"{row[2]:,.0f}"
                    if pd.notna(row[2])
                    and row[2] != 0
                    and isinstance(row[2], (int, float))
                    else row[2]
                ),
                (  # 'Credit_Amount'
                    f"{row[3]:,.0f}"
                    if pd.notna(row[3])
                    and row[3] != 0
                    and isinstance(row[3], (int, float))
                    else row[3]
                ),
                (  # 'Balance'
                    f"{row[4]:,.0f}"
                    if pd.notna(row[4])
                    and row[4] != 0
                    and isinstance(row[4], (int, float))
                    else row[4]
                ),
                row[5],  # Counterpart_Account_Number
                row[6],  # Counterpart_Account_Name
                row[7],  # Subaccount_Code
                row[8],  # Subaccount_Name
                row[9],  # Department_Code
                row[10],  # Department_Name
                row[11],  # Counterpart_Subaccount_Code
                row[12],  # Counterpart_Subaccount_Name
                row[13],  # Counterpart_Department_Code
            )
        elif 2 == frame_number:
            formatted_row = (
                row[0],  # Month
                row[1],  # Ledger_Account_Number
                row[2],  # Ledger_Account_Name
                row[3],  # e-Tax Category
                (  # 'Beginning_Balance'
                    f"{row[4]:,.0f}"
                    if pd.notna(row[4])
                    and row[4] != 0
                    and isinstance(row[4], (int, float))
                    else row[4]
                ),
                (  # 'Debit_Amount'
                    f"{row[5]:,.0f}"
                    if pd.notna(row[5])
                    and row[5] != 0
                    and isinstance(row[5], (int, float))
                    else row[5]
                ),
                (  # 'Credit_Amount'
                    f"{row[6]:,.0f}"
                    if pd.notna(row[6])
                    and row[6] != 0
                    and isinstance(row[6], (int, float))
                    else row[6]
                ),
                (  # 'Ending_Balance'
                    f"{float(row[7]):,.0f}"
                    if pd.notna(row[7])
                    and row[7] != 0
                    and isinstance(row[7], (int, float))
                    else row[7]
                ),
            )
        else:
            formatted_row = row
        return formatted_row

    def format_row(self, row, frame_number):
        if 0 == frame_number: # Journal Entry
            formatted_row = (
                # 0 ~ 3
                row[self.columns["伝票"]],
                row[self.columns["明細行"]],
                row[self.columns["伝票日付"]],
                row[self.columns["伝票番号"]],
                row[self.columns["摘要文"]],
                # 借方 4 ~ 9
                row[self.columns["借方科目コード"]],
                row[self.columns["借方科目名"]],
                (
                    f"{row['Debit_Amount']:,.0f}"
                    if pd.notna(row[self.columns["借方科目コード"]])
                    and pd.notna(row["Debit_Amount"])
                    and row["Debit_Amount"] != 0
                    else ""
                ),
                row[self.columns["借方税区分コード"]],
                row[self.columns["借方税区分名"]],
                row[self.columns["借方消費税額"]],
                # 貸方 10 ~ 15
                row[self.columns["貸方科目コード"]],
                row[self.columns["貸方科目名"]],
                (
                    f"{row['Credit_Amount']:,.0f}"
                    if pd.notna(row[self.columns["貸方科目コード"]])
                    and pd.notna(row["Credit_Amount"])
                    and row["Credit_Amount"] != 0
                    else ""
                ),
                row[self.columns["貸方税区分コード"]],
                row[self.columns["貸方税区分名"]],
                row[self.columns["貸方消費税額"]],
                # 16 ~ 19
                row[self.columns["借方補助科目コード"]],
                row[self.columns["借方補助科目名"]],
                row[self.columns["借方部門コード"]],
                row[self.columns["借方部門名"]],
                # 20 ~ 23
                row[self.columns["貸方補助科目コード"]],
                row[self.columns["貸方補助科目名"]],
                row[self.columns["貸方部門コード"]],
                row[self.columns["貸方部門名"]],
            )
        elif 1 == frame_number: # General Ledger
            formatted_row = (
                # 0 ~ 4
                row["Transaction_Date"],  # オリジナルの日付列を使用
                row["Description"],
                (
                    f"{row['Debit_Amount']:,.0f}"
                    if pd.notna(row["Debit_Amount"])
                    and row["Debit_Amount"] != 0
                    else ""
                ),
                (
                    f"{row['Credit_Amount']:,.0f}"
                    if pd.notna(row["Credit_Amount"])
                    and row["Credit_Amount"] != 0
                    else ""
                ),
                (
                    f"{row['Balance']:,.0f}"
                    if pd.notna(row["Balance"]) and row["Balance"] != 0
                    else ""
                ),
                # 5 ~ 14
                row["Counterpart_Account_Number"],
                row["Counterpart_Account_Name"],
                row["Subaccount_Code"],
                row["Subaccount_Name"],
                row["Department_Code"],
                row["Department_Name"],
                row["Counterpart_Subaccount_Code"],
                row["Counterpart_Subaccount_Name"],
                row["Counterpart_Department_Code"],
                row["Counterpart_Department_Name"],
            )
        elif 2 == frame_number: # Trial Balance
            formatted_row = (
                row["Month"],
                row["Ledger_Account_Number"],
                row["Ledger_Account_Name"],
                row["eTax_Category"],
                f"{row['Beginning_Balance']:,.0f}"
                if pd.notna(row["Beginning_Balance"]) and row["Beginning_Balance"] != 0
                else "",
                f"{row['Debit_Amount']:,.0f}"
                if pd.notna(row["Debit_Amount"]) and row["Debit_Amount"] != 0
                else "",
                f"{row['Credit_Amount']:,.0f}"
                if pd.notna(row["Credit_Amount"]) and row["Credit_Amount"] != 0
                else "",
                f"{row['Ending_Balance']:,.0f}"
                if pd.notna(row["Ending_Balance"]) and row["Ending_Balance"] != 0
                else "",
            )
        elif 3 == frame_number:  # Balance Sheet
            formatted_row = (
                row["seq"],
                row["Level"],
                row["Ledger_Account_Number"],
                row["eTax_Account_Name"],
                row["eTax_Category"],
                f"{row['Beginning_Balance']:,.0f}"
                if pd.notna(row["Beginning_Balance"]) and row["Beginning_Balance"] != 0
                else "",
                f"{row['Total_Debit']:,.0f}"
                if pd.notna(row["Total_Debit"]) and row["Total_Debit"] != 0
                else "",
                f"{row['Total_Credit']:,.0f}"
                if pd.notna(row["Total_Credit"]) and row["Total_Credit"] != 0
                else "",
                f"{row['Ending_Balance']:,.0f}"
                if pd.notna(row["Ending_Balance"]) and row["Ending_Balance"] != 0
                else "",
            )
        elif 4 == frame_number:  # Profit and Loss
            formatted_row = (
                row["seq"],
                row["Level"],
                row["Ledger_Account_Number"],
                row["eTax_Account_Name"],
                row["eTax_Category"],
                # f"{row['Beginning_Balance']:,.0f}"
                # if pd.notna(row["Beginning_Balance"]) and row["Beginning_Balance"] != 0
                # else "",
                f"{row['Total_Debit']:,.0f}"
                if pd.notna(row["Total_Debit"]) and row["Total_Debit"] != 0
                else "",
                f"{row['Total_Credit']:,.0f}"
                if pd.notna(row["Total_Credit"]) and row["Total_Credit"] != 0
                else "",
                f"{row['Ending_Balance']:,.0f}"
                if pd.notna(row["Ending_Balance"]) and row["Ending_Balance"] != 0
                else "",
            )
        return formatted_row

    def toggle_column(self):
        frame_number = self.frame_number
        if 0 == frame_number:
            frame = self.frame0
            tree = self.result_tree0
            columns = self.columns0
        elif 1 == frame_number:
            frame = self.frame1
            tree = self.result_tree1
            columns = self.columns1
        elif 2 == frame_number:
            frame = self.frame2
            tree = self.result_tree2
            columns = self.columns2
        elif 3 == frame_number:
            frame = self.frame3
            tree = self.result_tree3
            columns = self.columns3
        elif 4 == frame_number:
            frame = self.frame4
            tree = self.result_tree4
            columns = self.columns4
        else:
            return
        # コード列の表示/非表示を切り替える
        self.column_visible = not self.column_visible
        if self.column_visible:
            if 0 == frame_number:
                tree.column("DebitAccountCode", width=self.width_account, anchor="center", stretch=tk.NO)
                tree.column("DebitTaxCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
                tree.column("DebitSubaccountCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
                tree.column("DebitDepartmentCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
                tree.column("CreditAccountCode", width=self.width_account, anchor="center", stretch=tk.NO)
                tree.column("CreditTaxCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
                tree.column("CreditSubaccountCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
                tree.column("CreditDepartmentCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
            elif 1 == frame_number:
                tree.column("Subaccount_Code", width=self.width_subaccount, anchor="center", stretch=tk.NO)
                tree.column("Department_Code", width=self.width_subaccount, anchor="center", stretch=tk.NO)
                tree.column("Counterpart_Account_Number", width=self.width_account, anchor="center", stretch=tk.NO)
                tree.column("Counterpart_Subaccount_Code", width=self.width_subaccount, anchor="center", stretch=tk.NO)
                tree.column("Counterpart_Department_Code", width=self.width_subaccount, anchor="center", stretch=tk.NO)
            elif 2 == frame_number:
                self.result_tree2.column("Account_Number", width=self.width_account, stretch=tk.NO)
            elif 3 == frame_number:
                self.result_tree3.column("Ledger_Account_Number", width=self.width_account, anchor="center", stretch=tk.NO)
            elif 4 == frame_number:
                self.result_tree4.column("Ledger_Account_Number", width=self.width_account, anchor="center", stretch=tk.NO)
        else:
            if 0 == frame_number:
                tree.column("DebitAccountCode", width=0, stretch=tk.NO)
                tree.column("DebitTaxCode", width=0, stretch=tk.NO)
                tree.column("DebitSubaccountCode", width=0, stretch=tk.NO)
                tree.column("DebitDepartmentCode", width=0, stretch=tk.NO)
                tree.column("CreditAccountCode", width=0, stretch=tk.NO)
                tree.column("CreditTaxCode", width=0, stretch=tk.NO)
                tree.column("CreditSubaccountCode", width=0, stretch=tk.NO)
                tree.column("CreditDepartmentCode", width=0, stretch=tk.NO)
            elif 1 == frame_number:
                tree.column("Subaccount_Code", width=0, stretch=tk.NO)
                tree.column("Department_Code", width=0, stretch=tk.NO)
                tree.column("Counterpart_Account_Number", width=0, stretch=tk.NO)
                tree.column("Counterpart_Subaccount_Code", width=0, stretch=tk.NO)
                tree.column("Counterpart_Department_Code", width=0, stretch=tk.NO)
            elif 2 == frame_number:
                tree.column("Account_Number", width=0, stretch=tk.NO)
            elif 3 == frame_number:
                tree.column("Ledger_Account_Number", width=0, stretch=tk.NO)
            elif 4 == frame_number:
                tree.column("Ledger_Account_Number", width=0, stretch=tk.NO)
        # TreeviewまたはFrameの幅を更新する関数
        # 表示されている列の合計幅を計算
        total_width = sum(
            tree.column(col, option="width")
            for col in columns
            if tree.column(col, option="width") > 0
        )
        # Treeviewとフレームの幅を更新
        frame.config(width=total_width)
        # Treeviewのサイズ調整 (packの代わりにgridを使用)
        tree.grid(sticky="nsew")  # gridを統一して使用

    def create_frame0(self):
        # Frame 0: 仕訳帳表示 Journal entry
        frame = self.frame0
        self.columns0 = (
            "Journal",
            "DetailRow",
            "TransactionDate",
            "Entry_ID",
            "Description",
            "DebitAccountCode",
            "DebitAccountName",
            "Debit_Amount",
            "DebitTaxCode",
            "DebitTaxName",
            "DebitTaxAmount",
            "CreditAccountCode",
            "CreditAccountName",
            "Credit_Amount",
            "CreditTaxCode",
            "CreditTaxName",
            "CreditTaxAmount",
            "DebitSubaccountCode",
            "DebitSubaccountName",
            "DebitDepartmentCode",
            "DebitDepartmentName",
            "CreditSubaccountCode",
            "CreditSubaccountName",
            "CreditDepartmentCode",
            "CreditDepartmentName",
        )
        # Treeviewの作成
        self.result_tree0 = ttk.Treeview(
            frame,
            columns=self.columns0,
            show="headings",
            height=37
        )
        tree = self.result_tree0
        # width
        tree.column("Journal", width=self.width_dimension, anchor="e", stretch=tk.NO)
        tree.column("DetailRow", width=self.width_dimension, anchor="e", stretch=tk.NO)
        tree.column("TransactionDate", width=self.width_date, anchor="center", stretch=tk.NO)
        tree.column("Entry_ID", width=self.width_code, anchor="e", stretch=tk.NO)
        tree.column("Description", width=self.width_text, anchor="w", stretch=tk.NO)
        tree.column("DebitAccountCode", width=self.width_account, anchor="center", stretch=tk.NO)
        tree.column("DebitAccountName", width=self.width_longname, anchor="w", stretch=tk.NO)
        tree.column("Debit_Amount", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("DebitTaxCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("DebitTaxName", width=self.width_taxname, anchor="w", stretch=tk.NO)
        tree.column("DebitTaxAmount", width=self.width_taxamount, anchor="e", stretch=tk.NO)
        tree.column("CreditAccountCode", width=self.width_account, anchor="center", stretch=tk.NO)
        tree.column("CreditAccountName", width=self.width_longname, anchor="w", stretch=tk.NO)
        tree.column("Credit_Amount", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("CreditTaxCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("CreditTaxName", width=self.width_taxname, anchor="w", stretch=tk.NO)
        tree.column("CreditTaxAmount", width=self.width_taxamount, anchor="e", stretch=tk.NO)
        tree.column("DebitSubaccountCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("DebitSubaccountName", width=self.width_codename, anchor="w", stretch=tk.NO)
        tree.column("DebitDepartmentCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("DebitDepartmentName", width=self.width_codename, anchor="w", stretch=tk.NO)
        tree.column("CreditSubaccountCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("CreditSubaccountName", width=self.width_codename, anchor="w", stretch=tk.NO)
        tree.column("CreditDepartmentCode", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("CreditDepartmentName", width=self.width_codename, anchor="w", stretch=tk.NO)
        # text
        tree.heading("Journal", text="Journal" if self.lang=="en" else "仕訳")
        tree.heading("DetailRow", text="Detail Row" if self.lang=="en" else "明細行")
        tree.heading("TransactionDate", text="Date" if self.lang=="en" else "日付")
        tree.heading("Entry_ID", text="Entry ID" if self.lang=="en" else "仕訳番号")
        tree.heading("Description", text="Description" if self.lang=="en" else "摘要")
        tree.heading("DebitAccountCode", text="Debit Account Code" if self.lang=="en" else "借方科目コード")
        tree.heading("DebitAccountName", text="Debit Account Name" if self.lang=="en" else "借方科目名")
        tree.heading("Debit_Amount", text="Debit Amount" if self.lang=="en" else "借方金額")
        tree.heading("DebitTaxCode", text="Debit Tax Code" if self.lang=="en" else "借方税コード")
        tree.heading("DebitTaxName", text="Debit Tax Name" if self.lang=="en" else "借方税")
        tree.heading("DebitTaxAmount", text="Debit Tax Amount" if self.lang=="en" else "借方税額")
        tree.heading("CreditAccountCode", text="Credit Account" if self.lang=="en" else "貸方科目コード")
        tree.heading("CreditAccountName", text="貸方科目名" if self.lang=="ja" else "")
        tree.heading("Credit_Amount", text="Credit Amount" if self.lang=="en" else "貸方金額")
        tree.heading("CreditTaxCode", text="Credit Tax Code" if self.lang=="en" else "貸方税コード")
        tree.heading("CreditTaxName", text="Credit Tax Name" if self.lang=="en" else "貸方税")
        tree.heading("CreditTaxAmount", text="Credit Tax Amount" if self.lang=="en" else "貸方税額")
        tree.heading("DebitSubaccountCode", text="Debit Subaccount Code" if self.lang=="en" else "借方補助科目コード")
        tree.heading("DebitSubaccountName", text="Debit Subaccount Name" if self.lang=="en" else "借方補助科目名")
        tree.heading("DebitDepartmentCode", text="Debit Department Code" if self.lang=="en" else "借方部門コード")
        tree.heading("DebitDepartmentName", text="Debit epartment Name" if self.lang=="en" else "借方部門名")
        tree.heading("CreditSubaccountCode", text="Credit Subaccount Code" if self.lang=="en" else "貸方補助科目コード")
        tree.heading("CreditSubaccountName", text="Credit Subaccount Name" if self.lang=="en" else "貸方補助科目名")
        tree.heading("CreditDepartmentCode", text="Credit Department Code" if self.lang=="en" else "貸方部門コード")
        tree.heading("CreditDepartmentName", text="Credit Department Name" if self.lang=="en" else "貸方部門名")
        # Treeview を frame に配置
        tree.grid(row=0, column=0, sticky="nsew")
        # 水平スクロールバーの作成と配置
        scrollbar0x = tk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        scrollbar0x.grid(row=1, column=0, sticky="ew")
        # 垂直スクロールバーの作成と配置
        scrollbar0y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        scrollbar0y.grid(row=0, column=1, sticky="ns")
        # Treeview にスクロールバーを関連付け
        tree.configure(xscrollcommand=scrollbar0x.set, yscrollcommand=scrollbar0y.set)
        # double click
        tree.bind("<Double-1>", self.view_data)

    def create_frame1(self):
        # Frame 1: 総勘定元帳表示 General Ledger
        frame = self.frame1
        self.columns1 = (
            "Transaction_Date",
            "Description",
            "Debit_Amount",
            "Credit_Amount",
            "Balance",
            "Counterpart_Account_Number",
            "Counterpart_Account_Name",
            "Subaccount_Code",
            "Subaccount_Name",
            "Department_Code",
            "Department_Name",
            "Counterpart_Subaccount_Code",
            "Counterpart_Subaccount_Name",
            "Counterpart_Department_Code",
            "Counterpart_Department_Name",
        )
        self.result_tree1 = ttk.Treeview(
            frame,
            columns=self.columns1,
            show="headings",
            height=37
        )
        tree = self.result_tree1
        # width
        tree.column("Transaction_Date", width=self.width_date, anchor="center", stretch=tk.NO)
        tree.column("Description", width=self.width_text, anchor="w", stretch=tk.NO)
        tree.column("Debit_Amount", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Credit_Amount", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Balance", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Subaccount_Code", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("Subaccount_Name", width=self.width_codename, anchor="w", stretch=tk.NO)
        tree.column("Department_Code", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("Department_Name", width=self.width_codename, anchor="w", stretch=tk.NO)
        tree.column("Counterpart_Account_Number", width=self.width_account, anchor="center", stretch=tk.NO)
        tree.column("Counterpart_Account_Name", width=self.width_longname, anchor="w", stretch=tk.NO)
        tree.column("Counterpart_Subaccount_Code", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("Counterpart_Subaccount_Name", width=self.width_codename, anchor="w", stretch=tk.NO)
        tree.column("Counterpart_Department_Code", width=self.width_subaccount, anchor="center", stretch=tk.NO)
        tree.column("Counterpart_Department_Name", width=self.width_codename, anchor="w", stretch=tk.NO)
        # text
        tree.heading("Transaction_Date", text="Date" if self.lang=="en" else "日付")
        tree.heading("Description", text="Description" if self.lang=="en" else "摘要")
        tree.heading("Debit_Amount", text="Debit Amount" if self.lang=="en" else "借方金額")
        tree.heading("Credit_Amount", text="Credit Amount" if self.lang=="en" else "貸方金額")
        tree.heading("Balance", text="Balance" if self.lang=="en" else "残高")
        tree.heading("Counterpart_Account_Number", text="Counterpart Account Number" if self.lang=="en" else "相手科目コード")
        tree.heading("Counterpart_Account_Name", text="Counterpart Account Name" if self.lang=="en" else "相手科目名")
        tree.heading("Subaccount_Code", text="Subaccount Code" if self.lang=="en" else "補助科目コード")
        tree.heading("Subaccount_Name", text="Subaccount Name" if self.lang=="en" else "補助科目名")
        tree.heading("Department_Code", text="Department Code" if self.lang=="en" else "部門コード")
        tree.heading("Department_Name", text="Department name" if self.lang=="en" else "部門名")
        tree.heading("Counterpart_Subaccount_Code", text="Counterpart Subaccount Code" if self.lang=="en" else "相手補助科目コード")
        tree.heading("Counterpart_Subaccount_Name", text="Counterpart Subaccount Name" if self.lang=="en" else "相手補助科目名")
        tree.heading("Counterpart_Department_Code", text="Counterpart Department Code" if self.lang=="en" else "相手部門コード")
        tree.heading("Counterpart_Department_Name", text="CCounterpart Department Name" if self.lang=="en" else "相手部門名")
        # Treeview を frame に配置
        tree.grid(row=0, column=0, sticky="nsew")
        # 水平スクロールバーの作成と配置
        scrollbar1x = tk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        scrollbar1x.grid(row=1, column=0, sticky="ew")
        # 垂直スクロールバーの作成と配置
        scrollbar1y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        scrollbar1y.grid(row=0, column=1, sticky="ns")
        # Treeview にスクロールバーを関連付け
        tree.configure(xscrollcommand=scrollbar1x.set, yscrollcommand=scrollbar1y.set)
        # double click
        tree.bind("<Double-1>", self.view_data)

    def create_frame2(self):
        # Frame 2: 残高試算表表示 Trial Balance
        frame = self.frame2
        self.columns2 = (
            "Month",
            "Account_Number",
            "Account_Name",
            "eTax_Category",
            "Beginning_Balance",
            "Debit_Amount",
            "Credit_Amount",
            "Ending_Balance"
        )
        self.result_tree2 = ttk.Treeview(
            frame,
            columns=self.columns2,
            show="headings",
            height=37
        )
        tree = self.result_tree2
        # width
        tree.column("Month", width=self.width_month, anchor="center", stretch=tk.NO)
        tree.column("Account_Number", width=self.width_account, anchor="center", stretch=tk.NO)
        tree.column("Account_Name", width=self.width_longname, anchor="w", stretch=tk.NO)
        tree.column("eTax_Category", width=self.width_name, anchor="w", stretch=tk.NO)
        tree.column("Beginning_Balance", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Debit_Amount", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Credit_Amount", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Ending_Balance", width=self.width_amount, anchor="e", stretch=tk.NO)
        # text
        tree.heading("Month", text="Month" if self.lang=="en" else "日付")
        tree.heading("Account_Number", text="Account Num." if self.lang=="en" else "勘定科目")
        tree.heading("Account_Name", text="Account Name" if self.lang=="en" else "科目名")
        tree.heading("eTax_Category", text="Account Category" if self.lang=="en" else "勘定科目区分")
        tree.heading("Beginning_Balance", text="Starting Balance" if self.lang=="en" else "開始残高")
        tree.heading("Debit_Amount", text="Debit Amount" if self.lang=="en" else "借方金額")
        tree.heading("Credit_Amount", text="Credit Amount" if self.lang=="en" else "貸方金額")
        tree.heading("Ending_Balance", text="Ending Balance" if self.lang=="en" else "終了残高")
        # Treeview を frame2 に配置
        tree.grid(row=0, column=0, sticky="nsew")
        # 垂直スクロールバーの作成と配置
        scrollbar2y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        scrollbar2y.grid(row=0, column=1, sticky="ns")
        # Treeview にスクロールバーを関連付け
        tree.configure(yscrollcommand=scrollbar2y.set)

    def create_frame3(self):
        # Frame 3: Balance Sheet (BS) Display
        frame = self.frame3
        self.columns3 = (
            "seq",
            "Level",
            "Ledger_Account_Number",
            "eTax_Account_Name",
            "eTax_Category",
            "Beginning_Balance",
            "Total_Debit",
            "Total_Credit",
            "Ending_Balance"
        )
        self.result_tree3 = ttk.Treeview(
            frame,
            columns=self.columns3,
            show="headings",
            height=37
        )
        tree = self.result_tree3
        # width
        tree.column("seq", width=self.width_dimension, anchor="e", stretch=tk.NO)
        tree.column("Level", width=self.width_dimension, anchor="e", stretch=tk.NO)
        tree.column("Ledger_Account_Number", width=self.width_account, anchor="w", stretch=tk.NO)
        tree.column("eTax_Account_Name", width=self.width_text, anchor="w") #, stretch=tk.NO)
        tree.column("eTax_Category", width=self.width_longname, anchor="w", stretch=tk.NO)
        tree.column("Beginning_Balance", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Total_Debit", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Total_Credit", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Ending_Balance", width=self.width_amount, anchor="e", stretch=tk.NO)
        # text
        tree.heading("seq", text="Seq" if self.lang=="en" else "順序")
        tree.heading("Level", text="Level" if self.lang=="en" else "レベル")
        tree.heading("Ledger_Account_Number", text="Account Number" if self.lang=="en" else "勘定科目番号")
        tree.heading("eTax_Account_Name", text="Account Name" if self.lang=="en" else "勘定科目名")
        tree.heading("eTax_Category", text="Account Category" if self.lang=="en" else "勘定科目区分")
        tree.heading("Beginning_Balance", text="Starting Balance" if self.lang=="en" else "期首残高")
        tree.heading("Total_Debit", text="Debit" if self.lang=="en" else "借方")
        tree.heading("Total_Credit", text="Credit" if self.lang=="en" else "貸方")
        tree.heading("Ending_Balance", text="Ending Balance" if self.lang=="en" else "期末残高")
        # Treeview を frame3 に配置
        tree.grid(row=0, column=0, sticky="nsew")
        # 垂直スクロールバーの作成と配置
        scrollbar3y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        scrollbar3y.grid(row=0, column=1, sticky="ns")
        # Treeview にスクロールバーを関連付け
        tree.configure(yscrollcommand=scrollbar3y.set)

    def create_frame4(self):
        # Frame 4: Profit and Loss (PL) Display
        frame = self.frame4
        self.columns4 = (
            "seq",
            "Level",
            "Ledger_Account_Number",
            "eTax_Account_Name",
            "eTax_Category",
            # "Beginning_Balance",
            "Total_Debit",
            "Total_Credit",
            "Ending_Balance"
        )
        self.result_tree4 = ttk.Treeview(
            frame,
            columns=self.columns4,
            show="headings",
            height=37
        )
        tree = self.result_tree4
        # width
        tree.column("seq", width=self.width_dimension, anchor="e", stretch=tk.NO)
        tree.column("Level", width=self.width_dimension, anchor="e", stretch=tk.NO)
        tree.column("Ledger_Account_Number", width=self.width_account, anchor="w", stretch=tk.NO)
        tree.column("eTax_Account_Name", width=self.width_text, anchor="w") #, stretch=tk.NO)
        tree.column("eTax_Category", width=self.width_longname, anchor="w") #, stretch=tk.NO)
        # tree.column("Beginning_Balance", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Total_Debit", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Total_Credit", width=self.width_amount, anchor="e", stretch=tk.NO)
        tree.column("Ending_Balance", width=self.width_amount, anchor="e", stretch=tk.NO)
        # text
        tree.heading("seq", text="Seq" if self.lang=="en" else "順序")
        tree.heading("Level", text="Level" if self.lang=="en" else "レベル")
        tree.heading("Ledger_Account_Number", text="Account Number" if self.lang=="en" else "勘定科目番号")
        tree.heading("eTax_Account_Name", text= "勘定科目名" if self.lang=="ja" else "Account Name")
        tree.heading("eTax_Category", text="Account Category" if self.lang=="en" else "勘定科目区分")
        # tree.heading("Beginning_Balance", text="Starting Balance" if self.lang=="en" else "期首残高")
        tree.heading("Total_Debit", text="Debit" if self.lang=="en" else "借方")
        tree.heading("Total_Credit", text="Credit" if self.lang=="en" else "貸方")
        tree.heading("Ending_Balance", text="Amount" if self.lang=="en" else "金額")
        # Treeview を frame4 に配置
        tree.grid(row=0, column=0, sticky="nsew")
        # 垂直スクロールバーの作成と配置
        scrollbar4y = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        scrollbar4y.grid(row=0, column=1, sticky="ns")
        # Treeview にスクロールバーを関連付け
        tree.configure(yscrollcommand=scrollbar4y.set)

    def scroll_to_end(self, event=None):
        self.log_text.see("end")

    def open_link(self, event, url):
        """指定されたURLを開く"""
        webbrowser.open(url) 

    def on_hover(self, event):
        """マウスホバー時にアンダーラインを表示"""
        event.widget.configure(font=("Arial", 10, "underline"))

    def on_leave(self, event):
        """マウスホバーが外れたときにアンダーラインを解除"""
        event.widget.configure(font=("Arial", 10))

    def create_gui(self, root):
        # Root title
        self.root = root
        self.root.title(self.L("app_title"))

        # Localised default texts
        self.show_button_text = self.L("show")
        self.account_title_text = self.L("account_title")
        self.month_title_text = self.L("month_title")
        self.load_button_text = self.L("load_params")
        self.reset_button_text = self.L("reset_selection")

        # UI sizing knobs
        BTN_W = 24
        MENU_W = 44
        FILTER_W = 32
        LOG_W = 70
        PAD_L = (8, 4)
        PAD_M = (4, 4)
        PAD_R = (4, 8)

        # -------------------------
        # Top controls (base_frame)
        # -------------------------
        # View selector
        self.menu = self.menu_values()
        self.combobox = ttk.Combobox(
            self.base_frame,
            values=self.menu,
            width=MENU_W
        )
        self.combobox.set(self.menu_label_from_key("journal"))
        self.combobox.grid(row=0, column=0, padx=PAD_L, pady=4, sticky="ew")
        self.combobox.bind("<<ComboboxSelected>>", self.on_combobox_select)

        # Show Button
        self.show_button = tk.Button(
            self.base_frame,
            text=self.show_button_text,
            width=BTN_W,
            command=lambda: self.show_results(self.frame_number)
        )
        self.show_button.grid(row=1, column=0, padx=PAD_L, pady=4, sticky="ew")

        # Account
        self.account_title = tk.Label(self.base_frame, text=self.account_title_text)
        self.account_title.grid(row=0, column=1, padx=PAD_M, pady=4, sticky="w")
        self.account_combobox = ttk.Combobox(
            self.base_frame,
            values=[""],
            width=FILTER_W
        )
        self.account_combobox.grid(row=0, column=2, padx=PAD_M, pady=4, sticky="ew")

        # Month
        self.month_title = tk.Label(self.base_frame, text=self.month_title_text)
        self.month_title.grid(row=1, column=1, padx=PAD_M, pady=4, sticky="w")
        self.month_combobox = ttk.Combobox(
            self.base_frame,
            values=[""],
            width=FILTER_W
        )
        self.month_combobox.grid(row=1, column=2, padx=PAD_M, pady=4, sticky="ew")

        # Load Button
        self.load_button = tk.Button(
            self.base_frame,
            text=self.load_button_text,
            width=BTN_W,
            command=self.load_json
        )
        self.load_button.grid(row=0, column=4, padx=PAD_M, pady=4, sticky="ew")

        # Reset filter Button
        self.reset_button = tk.Button(
            self.base_frame,
            text=self.reset_button_text,
            width=BTN_W,
            command=self.reset_filters
        )
        self.reset_button.grid(row=1, column=4, padx=PAD_M, pady=4, sticky="ew")

        # log_text (message box)
        self.log_text = tk.Text(
            self.base_frame,
            spacing1=3, spacing2=2, spacing3=3,
            height=4, width=LOG_W, wrap="char"   # ★ LOG_W を使う
        )
        self.log_text.grid(row=0, column=5, rowspan=2, padx=PAD_R, pady=4, sticky="nsew")
        self.log_text.bind("<KeyRelease>", self.scroll_to_end)

        self.line = "0.0"
        log_tracker.write_log_text(self.file_path)

        # ---- layout tuning (grid weights) ----
        # ★ ここで決めた weight/minsize を、後続のループで上書きしない
        self.base_frame.grid_columnconfigure(0, weight=3, minsize=220)  # menu+show
        self.base_frame.grid_columnconfigure(1, weight=0, minsize=70)   # label
        self.base_frame.grid_columnconfigure(2, weight=3, minsize=260)  # combo
        self.base_frame.grid_columnconfigure(3, weight=0, minsize=10)   # spacer
        self.base_frame.grid_columnconfigure(4, weight=2, minsize=200)  # load/reset
        self.base_frame.grid_columnconfigure(5, weight=0, minsize=160)  # ★ log (narrow, not expanding)

        # frameN
        self.frame0 = tk.Frame(self.base_frame)
        self.frame1 = tk.Frame(self.base_frame)
        self.frame2 = tk.Frame(self.base_frame)
        self.frame3 = tk.Frame(self.base_frame)
        self.frame4 = tk.Frame(self.base_frame)

        for fr in [self.frame0, self.frame1, self.frame2, self.frame3, self.frame4]:
            fr.grid(row=2, column=0, columnspan=6, sticky="nsew", padx=4, pady=4)
            fr.grid_rowconfigure(0, weight=1)
            fr.grid_columnconfigure(0, weight=1)

        self.create_frame0()
        self.create_frame1()
        self.create_frame2()
        self.create_frame3()
        self.create_frame4()
        self.update_tree_headings()

        # -------------------------
        # Search frame (bottom)
        # -------------------------
        search_frame = tk.Frame(self.base_frame)
        search_frame.grid(row=5, column=0, columnspan=6, padx=(4, 4), pady=(4, 4), sticky="nsew")

        # Radio state (keep on self so language toggle can update the widget text safely)
        self.entity_type_var = tk.StringVar(value="")
        self.entity_type = None

        def on_entity_type_click():
            self.entity_type = self.entity_type_var.get()
            entities = [
                val.get("name", "")
                for _, val in tidy_data.trading_partner_dict.get(
                    self.entity_type, {}
                ).items()
            ]
            self.entity_combobox["values"] = entities

        self.radio_customer = ttk.Radiobutton(
            search_frame,
            text=self.L("entity_customer"),
            value="customer",
            variable=self.entity_type_var,
            command=on_entity_type_click
        )
        self.radio_customer.pack(side="left", padx=4)

        self.radio_supplier = ttk.Radiobutton(
            search_frame,
            text=self.L("entity_supplier"),
            value="supplier",
            variable=self.entity_type_var,
            command=on_entity_type_click
        )
        self.radio_supplier.pack(side="left", padx=4)

        self.radio_bank = ttk.Radiobutton(
            search_frame,
            text=self.L("entity_bank"),
            value="bank",
            variable=self.entity_type_var,
            command=on_entity_type_click
        )
        self.radio_bank.pack(side="left", padx=4)

        # Combobox (store on self so callbacks can access safely)
        self.entity_combobox = ttk.Combobox(
            search_frame,
            values=[""],
            width=20
        )
        self.entity_combobox.pack(side="left", padx=4)

        # Combobox selection handler
        def on_entity_combobox_select(event):
            selected_value = self.entity_combobox.get()
            search_trading_partner(selected_value)

        self.entity_combobox.bind("<<ComboboxSelected>>", on_entity_combobox_select)

        # ---- your existing search functions (keep as-is) ----
        def get_aliases(name):
            if not self.entity_type:
                return None, None
            for _, values in tidy_data.trading_partner_dict.get(self.entity_type, {}).items():
                if values.get("name") == name:
                    return values.get("alias1", ""), values.get("alias2", "")
            return None, None

        def search_trading_partner(selected_value):
            name = selected_value
            alias1, alias2 = get_aliases(name)
            ExecutionMessage.start(root, search_trading_partner_body, name, alias1, alias2)

        def search_trading_partner_body(name, alias1, alias2):
            def filter_data(original_data, search_terms, columns_to_search):
                filtered_data = []
                for row in original_data:
                    for search_term in search_terms:
                        if (
                            search_term
                            and any(
                                pd.notna(row[col]) and search_term in str(row[col]).lower()
                                for col in columns_to_search
                            )
                        ):
                            filtered_data.append(row)
                return filtered_data

            search_terms = [name, alias1, alias2]
            frame_number = self.frame_number

            if 0 == frame_number:
                result_tree = self.result_tree0
                columns_to_search = [4, 6, 12, 18, 20, 22, 24]
            elif 1 == frame_number:
                result_tree = self.result_tree1
                columns_to_search = [1, 6, 8, 10, 12, 14]
            else:
                return

            filtered_data = filter_data(self.original_data, search_terms, columns_to_search)

            for i in result_tree.get_children():
                result_tree.delete(i)

            for row in filtered_data:
                result_tree.insert("", "end", values=self.format_searched_row(row, frame_number))

            ExecutionMessage.end()

        # ---- rest of bottom controls (keep, but you can localise later) ----
        self.apply_ui_sizes()

        self.search_label = tk.Label(search_frame, text="検索語:")
        self.search_label.pack(side="left")

        self.search_entry = tk.Entry(search_frame)
        self.search_entry.pack(side="left", padx=4)

        self.search_button = tk.Button(search_frame, text="検索", command=self.search_keyword)
        self.search_button.pack(side="left", padx=4)

        self.reset_search_button = tk.Button(
            search_frame, text=self.L("reset_search"), width=self.button_width, command=self.reset_search
        )

        self.reset_search_button.pack(side="left", padx=4)

        self.view_button = tk.Button(
            search_frame, text=self.L("view_data"), width=self.button_width, command=self.view_data
        )

        self.view_button.pack(side="left", padx=4)

        self.toggle_column_button = tk.Button(
            search_frame, text=self.L("toggle_code_cols"), width=self.button_width, command=self.toggle_column
        )

        self.toggle_column_button.pack(side="left", padx=4)

        self.save_button = tk.Button(
            search_frame, text=self.L("save_csv"), width=self.button_width, command=self.save_csv
        )

        self.save_button.pack(side="left", padx=4)

        self.toggle_language_button = tk.Button(
            search_frame, text=self.L("toggle_lang"), width=self.button_width, command=self.toggle_language
        )

        self.toggle_language_button.pack(side="left", padx=4)

        # Apply localisation + common UI sizing (fonts, widths)
        self.update_labels()
        self.apply_ui_sizes()

        # Footer (copyright) — place at bottom under the button row
        footer_frame = tk.Frame(self.base_frame)
        footer_frame.grid(row=6, column=0, columnspan=6, padx=(4, 4), pady=(0, 6), sticky="ew")
        footer_frame.grid_columnconfigure(0, weight=1)

        self.copyright_label = tk.Label(
            footer_frame,
            text=APP_COPYRIGHT_TEXT,
            anchor="e",
            font=("Arial", 10),
            fg="gray",
            cursor="hand2"  # hand cursor
        )
        self.copyright_label.grid(row=0, column=0, sticky="e")
        url = APP_WEBSITE
        self.copyright_label.bind("<Button-1>", lambda event: self.open_link(event, url))  # open link
        self.copyright_label.bind("<Enter>", self.on_hover)   # underline on hover
        self.copyright_label.bind("<Leave>", self.on_leave)   # remove underline
        # account combobox
        account_dict = tidy_data.get_account_dict()
        accounts = [key.split(" ", 1)[1] for key in account_dict.keys()]
        self.account_combobox["values"] = accounts

        # month combobox values
        self.summary_df = tidy_data.get_summary_df()
        months = sorted(self.summary_df["Month"].unique())
        self.month_combobox["values"] = [""] + months

        # show frame0
        self.show_frame(self.frame0, 0)
        self.update_labels()

        if "en" == self.lang:
            log_tracker.write_log_text("window created.")
        else:
            log_tracker.write_log_text("ウィンドウ生成")

        ExecutionMessage.end()


if __name__ == "__main__":
    PARSER = True
    NO_GUI = None
    if PARSER:
        ap = argparse.ArgumentParser()
        ap.add_argument("param_file", help="Path to parameters.json")
        ap.add_argument("--export-dir", default=None, help="Export CSVs for web into this directory")
        ap.add_argument("--no-gui", action="store_true", help="Do not start Tkinter GUI")
        args = ap.parse_args()

        param_file_path = args.param_file
        export_dir = args.export_dir
        NO_GUI = args.no_gui
    else:
        BASE_DIR = "LedgerExplorer"
        param_file_path = f"{BASE_DIR}/parameters.json"
        export_dir = f"{BASE_DIR}/server/data"

        NO_GUI = False

# グローバル変数としてlog_trackerを定義
log_tracker = LogTracker()

# ---- 1) Load + process data ONCE (usable for export and/or GUI) ----
tidy_data = TidyData()
tidy_data.csv2dataframe(param_file_path)

# ---- 2) Export mode (optional) ----
if export_dir:
    paths = export_web_csv(tidy_data, export_dir)
    print("Exported CSVs:")
    for k, v in paths.items():
        print(f"  {k}: {v}")

# ---- 3) No-GUI mode (export-only) ----
if NO_GUI:
    sys.exit(0)

# ---- 4) GUI mode (reuse the already-built tidy_data) ----
root = tk.Tk()
gui = GUI(root)

gui.create_base(root)

# Build the GUI now, using the already processed tidy_data.
gui.create_gui(root)

root.mainloop()
