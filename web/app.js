/*
Ledger Explorer Web Viewer - app.js
Copyright (c) 2026 SAMBUICHI, Nobuyuki (Sambuichi Professional Engineers Office)
https://www.sambuichi.jp/

Licensing / ライセンス
- MIT License: program source code in this file (software logic).
- CC BY-SA 4.0: original LHM-related definitions, join/mapping tables,
  semantic labels, UI text, label/translation dictionaries, and documentation
  comments contributed for this project, including when embedded in this file.
- Third-party standards and code lists retain their original rights and terms.
- See LICENSE-SCOPE.md for the detailed boundary.

Note / 注意
This script is a small, dependency-free CSV viewer for accounting/ledger CSV exports.
It loads an index.json that describes available "views" (journal/ledger/trial balance/BS/PL/tidy),
then fetches the corresponding CSV from the server (or reads a user-uploaded CSV),
applies localisation (JA/EN), formatting (numbers/alignment/indent), and interactive filtering.

本スクリプトは依存ライブラリなしの軽量CSVビューアです。
index.json で定義された view（仕訳帳/総勘定元帳/試算表/BS/PL/構造化CSV）を選択し、
サーバ上のCSV取得（またはローカルCSVアップロード）→ 見出し翻訳（日本語/英語）→
数値整形・配置 → 検索/科目フィルタ → 表示、を行います。
*/

// -------- Settings --------
/*
Data location
- This repo keeps web assets in /web and datasets in /data/{sample|full}.
- When hosting GitHub Pages from /web, the data folder is a sibling of /web, so we use "../data/...".
- You can switch dataset with ?dataset=sample or ?dataset=full.
*/
const DATASET_DEFAULT = "sample";
let DATASET = DATASET_DEFAULT;
let DATA_ROOT = null; // resolved at runtime
let INDEX_URL = null; // resolved at runtime
let INDEX_BOOTSTRAP = null; // validated index.json loaded during path resolution
let DATASET_PATH_FAILURES = [];
const DEFAULT_MAX_ROWS = 3000;     // 表示行数（重ければ下げる）
const CSV_CACHE = new Map();       // key: url -> parsed rows

// ---- Language ----
const LANG_DEFAULT = "ja";
let currentLang = localStorage.getItem("ledger_lang") || LANG_DEFAULT;

// ---- Column visibility toggles ----
// Show/hide *display* (filtering keeps using the underlying data)
let showCodeCols =
  (localStorage.getItem("ledger_show_code_cols") ?? "0") !== "0";

// Toggle button elements (created at runtime)
let toggleCodeColsBtn = null;

// Public viewer data mode is fixed to server-hosted data.
let dataMode = "server";
// ---- Data root / dataset resolution ----
// dataset: "sample" | "full" (default: sample)
// Production may serve the UI directly from /ledger/ with data below /ledger/data/{dataset}.
// The repository layout serves the UI from /web/ with data in the sibling /data/{dataset}.
//
// You can override with URL query:
//   ?dataset=sample   (default)
//   ?dataset=full
//
// A candidate is accepted only after its response body has been parsed and validated as an index.
async function initDatasetAndPaths() {
  const url = new URL(location.href);

  // dataset selection
  const qDataset = url.searchParams.get("dataset");
  if (qDataset && (qDataset === "sample" || qDataset === "full")) {
    DATASET = qDataset;
  }

  // Candidate data roots (in priority order)
  const candidates = [
    { root: `./data/${DATASET}`,  index: `./data/${DATASET}/index.json`  },
    { root: `../data/${DATASET}`, index: `../data/${DATASET}/index.json` },
    // legacy / alternate layouts
    { root: `./data`,  index: `./data/index.json`  },
    { root: `../data`, index: `../data/index.json` },
  ];

  DATA_ROOT = null;
  INDEX_URL = null;
  INDEX_BOOTSTRAP = null;
  DATASET_PATH_FAILURES = [];

  // Pick the first response that is both successful and a valid Ledger Explorer index.
  for (const c of candidates) {
    try {
      const res = await fetch(c.index, { cache: "no-store" });
      const finalUrl = res.url || c.index;
      const contentType = res.headers.get("content-type") || "(not supplied)";

      if (!res.ok) {
        throw new Error(`HTTP ${res.status} ${res.statusText || ""}`.trim());
      }

      const body = await res.text();
      let parsed;
      try {
        parsed = JSON.parse(body);
      } catch (error) {
        const redirectNote = res.redirected ? `; redirected to ${finalUrl}` : "";
        throw new Error(`invalid JSON (${contentType}${redirectNote}): ${error.message}`);
      }

      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
        throw new Error(`JSON root must be an object (${contentType}; final URL ${finalUrl})`);
      }
      if (!parsed.views || typeof parsed.views !== "object" || Array.isArray(parsed.views)) {
        throw new Error(`JSON does not contain a valid \"views\" object (${contentType}; final URL ${finalUrl})`);
      }

      DATA_ROOT = c.root;
      INDEX_URL = c.index;
      INDEX_BOOTSTRAP = parsed;
      console.info(`Dataset index selected: ${c.index} (final URL: ${finalUrl})`);
      return;
    } catch (error) {
      const reason = String(error?.message || error);
      DATASET_PATH_FAILURES.push({ index: c.index, reason });
      console.warn(`Dataset index rejected: ${c.index} - ${reason}`);
    }
  }

  const details = DATASET_PATH_FAILURES
    .map(item => `${item.index}: ${item.reason}`)
    .join("\n");
  throw new Error(`No valid dataset index was found for dataset \"${DATASET}\".\n${details}`);
}

let localRowsAll = null; // uploaded CSV rows

// View labels (button labels)
const VIEW_LABELS_I18N = {
  ja: {
    journal: "仕訳帳",
    ledger: "総勘定元帳",
    trial_balance: "試算表",
    balance_sheet: "貸借対照表",
    pnl: "損益計算書",
    receivables: "売掛金集計",
    payables: "買掛金集計",
    tidy: "構造化CSV",
  },
  en: {
    journal: "Journal",
    ledger: "General Ledger",
    trial_balance: "Trial Balance",
    balance_sheet: "Balance Sheet",
    pnl: "Profit and Loss",
    receivables: "A/R Summary",
    payables: "A/P Summary",
    tidy: "Structured CSV",
  },
};


/*
EN: Column dictionaries
- COLUMN_DEFS maps Japanese semantic labels to your structured CSV column codes (JPxx/BSxx/GE..).
- CODE_TO_JA / CODE_TO_EN translate codes to headers for display (JA/EN).
JA: 列定義（辞書）
- COLUMN_DEFS は「日本語ラベル → 列コード（JPxx/BSxx/GE..）」の対応表です。
- CODE_TO_JA / CODE_TO_EN は「コード → 画面見出し（日本語/英語）」を提供します。
*/
// ---- Column definitions (Japanese label -> column code) ----
const COLUMN_DEFS = {
  "伝票": "JP07a",
  "明細行": "JP08a",
  "借方部門": "BS04fb",
  "借方補助科目": "JP05a",
  "貸方部門": "BS04fc",
  "貸方補助科目": "JP05b",
  "伝票日付": "JP07a_GL03_03",
  "伝票番号": "JP07a_GL03_01",
  "借方部門コード": "BS04fb_01",
  "借方部門名": "BS04fb_02",
  "借方部門区分": "BS04fb_03",
  "借方科目コード": "JP06e_GE24_01",
  "借方科目名": "JP06e_GE24_02",
  "借方補助科目コード": "JP05a_01",
  "借方補助科目名": "JP05a_02",
  "借方補助区分": "JP05a_03",
  "借方税区分コード": "JP02j_BS09_01",
  "借方税区分名": "JP02j_BS09_02",
  "借方金額": "GE05ku_01",
  "借方消費税額": "GE05kw_01",
  "貸方部門コード": "BS04fc_01",
  "貸方部門名": "BS04fc_02",
  "貸方部門区分": "BS04fc_03",
  "貸方科目コード": "JP06f_GE24_01",
  "貸方科目名": "JP06f_GE24_02",
  "貸方補助科目コード": "JP05b_01",
  "貸方補助科目名": "JP05b_02",
  "貸方補助区分": "JP05b_03",
  "貸方税区分コード": "JP02k_BS09_01",
  "貸方税区分名": "JP02k_BS09_02",
  "貸方金額": "GE05kz_01",
  "貸方消費税額": "GE05kB_01",
  "摘要文": "JP08a_GL04_03",
  "入力プログラム区分": "GE23c_01",
  "仕訳区分": "GL05c_01",
  "入力日付": "GE09eR_01"
};

// code -> Japanese label
const CODE_TO_JA = Object.fromEntries(
  Object.entries(COLUMN_DEFS).map(([jpLabel, code]) => [String(code).trim(), jpLabel])
);
CODE_TO_JA["Month"] = "対象月";

// code -> English label (USER PROVIDED)
const CODE_TO_EN = {
  "JP07a": "Voucher",
  "JP08a": "Line",
  "Month": "Month",

  "BS04fb": "Debit department",
  "JP05a": "Debit subaccount",
  "BS04fc": "Credit department",
  "JP05b": "Credit subaccount",

  "JP07a_GL03_03": "Voucher date",
  "JP07a_GL03_01": "Voucher number",
  "GE23c_01": "Journal type",

  "BS04fb_01": "Debit department code",
  "BS04fb_02": "Debit department name",
  "BS04fb_03": "Debit department category",

  "JP06e_GE24_01": "Debit account code",
  "JP06e_GE24_02": "Debit account name",

  "JP05a_01": "Debit subaccount code",
  "JP05a_02": "Debit subaccount name",
  "JP05a_03": "Debit subaccount category",

  "JP02j_BS09_01": "Debit tax category code",
  "JP02j_BS09_02": "Debit tax category name",

  "GE05ku_01": "Debit amount",
  "GE05kw_01": "Debit consumption tax amount",

  "BS04fc_01": "Credit department code",
  "BS04fc_02": "Credit department name",
  "BS04fc_03": "Credit department category",

  "JP06f_GE24_01": "Credit account code",
  "JP06f_GE24_02": "Credit account name",

  "JP05b_01": "Credit subaccount code",
  "JP05b_02": "Credit subaccount name",
  "JP05b_03": "Credit subaccount category",

  "JP02k_BS09_01": "Credit tax category code",
  "JP02k_BS09_02": "Credit tax category name",

  "GE05kz_01": "Credit amount",
  "GE05kB_01": "Credit consumption tax amount",

  "JP08a_GL04_03": "Description",
  "GL05c_01": "Input date",
  "GE09eR_01": "Input program type"
};

// ---- Style derived ONLY from your definitions (no code-pattern guessing) ----
function styleFromJaLabel(jpLabel) {
  const s = String(jpLabel || "").trim();
  if (s.includes("金額") || s.includes("税額") || s.includes("残高")) return { align: "right", fmt: "number" };
  if (s.includes("コード") || s.includes("区分")) return { align: "center", fmt: "text" };
  return { align: "left", fmt: "text" };
}

const STYLE_BY_CODE = Object.fromEntries(
  Object.entries(COLUMN_DEFS).map(([jpLabel, code]) => [String(code).trim(), styleFromJaLabel(jpLabel)])
);

const STYLE_BY_JA_LABEL = Object.fromEntries(
  Object.entries(COLUMN_DEFS).map(([jpLabel, _code]) => [String(jpLabel).trim(), styleFromJaLabel(jpLabel)])
);

// --- Normalise header labels (to match English label variants like Debit_Amount vs Debit amount) ---
function normLabel(s) {
  return String(s ?? "")
    .trim()
    .replace(/_/g, " ")
    .replace(/\s+/g, " ")
    .toLowerCase();
}

// ---- Raw header localisation (for non-coded CSV headers like Transaction_Date) ----
// NOTE: normLabel() converts underscores to spaces and lowercases for matching.
//       Add entries here when a CSV header appears in English in Japanese mode, or vice versa.
const RAW_HEADER_ALIAS_I18N = {
  ja: {
    // General ledger / trial balance columns
    "Transaction_Date": "伝票日付",
    "Description": "摘要文",
    "Ledger_Account_Name": "科目",
    "Ledger_Account_Number": "勘定科目番号",
    "Subaccount_Code": "補助科目コード",
    "Subaccount_Name": "補助科目",
    "Department_Code": "部門コード",
    "Department_Name": "部門",
    "Debit_Amount": "借方金額",
    "Credit_Amount": "貸方金額",
    "Beginning_Balance": "月初残高",
    "Ending_Balance": "月末残高",
    "Balance": "残高",
    "Account_Category": "勘定科目区分",
    "Account_Name": "勘定科目名",
    "Counterpart_Account_Number": "相手科目番号",
    "Counterpart_Account_Name": "相手科目",
    "Counterpart_Subaccount_Code": "相手補助科目コード",
    "Counterpart_Subaccount_Name": "相手補助科目",
    "Counterpart_Department_Code": "相手部門コード",
    "Counterpart_Department_Name": "相手部門",
    "Parent": "親科目",
    "Level": "レベル",
    "Type": "種別",
    "Typ": "種別",
    "Total_Debit": "借方合計",
    "Total_Credit": "貸方合計",
    "seq": "順序",
    "eTax_Category": "勘定科目区分",
    "eTax_Account_Name": "勘定科目名",
    "Month": "対象月",
  },
  en: {
    // Optional: reverse mapping when CSV headers are Japanese
    "伝票日付": "Transaction Date",
    "摘要文": "Description",
    "科目": "Account Name",
    "勘定科目番号": "Account Number",
    "補助科目コード": "Subaccount Code",
    "補助科目": "Subaccount Name",
    "部門コード": "Department Code",
    "部門": "Department Name",
    "借方金額": "Debit Amount",
    "貸方金額": "Credit Amount",
    "月初残高": "Beginning Balance",
    "月末残高": "Ending Balance",
    "残高": "Balance",
    "勘定科目区分": "Account Category",
    "勘定科目名": "Account Name",
    "相手科目番号": "Counterpart Account Number",
    "相手科目": "Counterpart Account Name",
    "相手補助科目コード": "Counterpart Subaccount Code",
    "相手補助科目": "Counterpart Subaccount Name",
    "相手部門コード": "Counterpart Department Code",
    "相手部門": "Counterpart Department Name",
    "親科目": "Parent",
    "レベル": "Level",
    "種別": "Type",
    "借方合計": "Total_Debit",
    "貸方合計": "Total_Credit",
    "対象月": "Month",
    // Prefer these display names in English UI
    "eTax_Category": "Account_Category",
    "eTax_Account_Name": "Account_Name",
  }
};

const SEARCH_PLACEHOLDER_I18N = {
  ja: "摘要など（部分一致）",
  en: "Description etc. (partial match)",
};

const RAW_HEADER_ALIAS_NORM = {
  ja: Object.fromEntries(Object.entries(RAW_HEADER_ALIAS_I18N.ja).map(([k, v]) => [normLabel(k), v])),
  en: Object.fromEntries(Object.entries(RAW_HEADER_ALIAS_I18N.en).map(([k, v]) => [normLabel(k), v])),
};

// English label -> code (reverse of CODE_TO_EN), using normalised key
const EN_LABEL_TO_CODE_NORM = Object.fromEntries(
  Object.entries(CODE_TO_EN).map(([code, enLabel]) => [normLabel(enLabel), String(code).trim()])
);

// ---- Explicit UI styles for non-coded datasets (trial balance / BS / PL etc.) ----
// Integer columns (right-aligned)
const INT_HEADERS_NORM = new Set([
  "seq",
].map(normLabel));

// Numeric columns (right-aligned + number formatting)
const NUMERIC_HEADERS_NORM = new Set([
  "Beginning_Balance",
  "Total_Debit",
  "Total_Credit",
  "Ending_Balance",
  "Balance"
].map(normLabel));

// Center-aligned columns
const CENTER_HEADERS_NORM = new Set([
  // Journal / general
  "Voucher", "Line", "Month", "Voucher date", "Voucher number",
  // Structured CSV (tidy) identifiers
  "Debit department", "Debit subaccount", "Credit department", "Credit subaccount",
  // Ledger
  "Transaction_Date", "Ledger_Account_Number", "Counterpart_Account_Number",
  // Trial balance / statements
  "Level", "Type", "Parent"
].map(normLabel));

// Override some coded columns to centre
const CENTER_CODES = new Set([
  "JP07a",           // Voucher
  "JP08a",           // Line
  "BS04fb",          // Debit department (structured CSV / tidy)
  "JP05a",           // Debit subaccount (structured CSV / tidy)
  "BS04fc",          // Credit department (structured CSV / tidy)
  "JP05b",           // Credit subaccount (structured CSV / tidy)
  "Month",
  "JP07a_GL03_03",   // Voucher date
  "JP07a_GL03_01"    // Voucher number
]);

// Integer-like identifier columns (display as integer without thousand separators, centred)
const INT_CODES = new Set([
  "JP08a",  // Line
  "BS04fb", // Debit department (structured CSV / tidy)
  "JP05a",  // Debit subaccount (structured CSV / tidy)
  "BS04fc", // Credit department (structured CSV / tidy)
  "JP05b"   // Credit subaccount (structured CSV / tidy)

]);

// Hide columns only for specific views (e.g., BS/PL).
// IMPORTANT: BS/PL can contain duplicate header names (e.g., Ledger_Account_Number appears twice).
// We must hide by *index* to avoid hiding the later (4th) Ledger_Account_Number column.
const HIDDEN_COL_INDEXES_BY_VIEW = {
  balance_sheet: new Set([0, 1, 2]), // 1st Ledger_Account_Number, Level, Typ/Type
  pnl: new Set([0, 1, 2]),
};

// Optional: hide-by-name rules for other views (kept for future use)
const HIDDEN_HEADERS_BY_VIEW_NORM = {
  // example:
  // some_view: new Set(["SomeHeader"].map(normLabel)),
};

// Resolve any header cell to a "code" if possible.
// Supports:
//  - header is a code (JP07a_GL03_01, GE05ku_01, Month, ...)
//  - header is a Japanese label (伝票番号, 借方金額, ...)
//  - header is an English label variant (Voucher number / Voucher_number / debit_amount / Debit Amount ...)
/*
EN: Header resolution strategy
This viewer avoids heuristics that guess column meaning from patterns.
Instead it resolves each header to a known code using:
  (1) code itself, (2) Japanese label, (3) normalised English label.
JA: 見出し解決の方針
パターン推測に依存せず、(1)コード、(2)日本語ラベル、(3)英語ラベル（正規化）で
既知の列コードへ解決し、整形/翻訳/表示制御に利用します。
*/
function resolveHeaderToCode(headerCell) {
  const h = String(headerCell ?? "").trim();

  // 1) already a code in our definitions (or Month)
  if (STYLE_BY_CODE[h]) return h;
  if (h === "Month") return "Month";

  // 2) Japanese label
  if (STYLE_BY_JA_LABEL[h]) return COLUMN_DEFS[h];

  // 3) English label variant
  const code = EN_LABEL_TO_CODE_NORM[normLabel(h)];
  if (code && (STYLE_BY_CODE[code] || code === "Month")) return code;

  return null;
}

// ---- Column alignment resolver (no guessing) ----
function getColStyle(headerCell) {
  const code = resolveHeaderToCode(headerCell);
  // Prefer code-based styling when we can resolve it
  if (code) {
    let st = (STYLE_BY_CODE[code] || { align: "left", fmt: "text" });

    // Integer-like identifier columns (e.g. Line / Department / Subaccount in Structured CSV)
    if (INT_CODES.has(code)) st = { ...st, align: "center", fmt: "int" };
    else if (CENTER_CODES.has(code)) st = { ...st, align: "center" };

    return st;
  }

  // Non-coded datasets: use explicit header name lists
  const hn = normLabel(headerCell);
  if (INT_HEADERS_NORM.has(hn)) return { align: "right", fmt: "int" };
  if (NUMERIC_HEADERS_NORM.has(hn)) return { align: "right", fmt: "number" };
  if (CENTER_HEADERS_NORM.has(hn)) return { align: "center", fmt: "text" };

  return { align: "left", fmt: "text" };
}
// :
// function getColStyle(headerCell) {
//   // Special case: sequence column (seq / 順序) should be right-aligned
//   const hn = normLabel(headerCell);
//   if (hn === "seq" || hn === "順序") return { align: "right", fmt: "text" };

//   const code = resolveHeaderToCode(headerCell);
//   if (code && STYLE_BY_CODE[code]) return STYLE_BY_CODE[code];
//   return { align: "left", fmt: "text" };
// }

// ---- Header label for display ----
// Display header aliases (applied when no code-based translation matched)
const HEADER_DISPLAY_ALIAS_NORM = Object.fromEntries([
  ["eTax_Category", "Account_Category"],
  ["eTax_Account_Name", "Account_Name"],
].map(([k, v]) => [normLabel(k), v]));

// ---- Header label for display ----
function prettifyLabel(s) {
  // Display-only normalisation: replace "_" with two spaces for readability.
  // 表示用の整形：可読性のため "_" をスペース2個に置換する。
  return String(s ?? "").replaceAll("_", "  ");
}
function tHeader(headerCell) {
  const raw = String(headerCell ?? "").trim();

  // 1) Translate if the header matches a known column code or label variant.
  // 1) 列コード（または派生表記）として解決できる場合は翻訳する。
  const code = resolveHeaderToCode(raw);
  if (code) {
    if (currentLang === "ja") return prettifyLabel(CODE_TO_JA[code] || raw);
    if (currentLang === "en") return prettifyLabel(CODE_TO_EN[code] || raw);
  }

  // 2) Raw header localisation (term-by-term), e.g. "Ledger_Account_Name" -> "科目".
  // 2) 生ヘッダ（項目名）を用語単位で翻訳する（例："Ledger_Account_Name" -> "科目"）。
  const aliasMap = RAW_HEADER_ALIAS_NORM[currentLang] || {};
  const alias = aliasMap[normLabel(raw)];
  if (alias) return prettifyLabel(alias);

  // 3) Fallback: prettify raw (replace "_" with two spaces).
  // 3) 最後の手段：生ヘッダを整形（"_" をスペース2個に置換）。
  return prettifyLabel(raw);
}

function tViewLabel(viewKey) {
  return (VIEW_LABELS_I18N[currentLang] && VIEW_LABELS_I18N[currentLang][viewKey]) || viewKey;
}

// ---- Number formatting ----
function isNumericLike(v) {
  if (v === null || v === undefined) return false;
  const s = String(v).trim();
  if (!s) return false;
  const t = s.replace(/,/g, "");
  return /^-?\d+(\.\d+)?$/.test(t);
}

function formatNumberLike(v) {
  const s = String(v).trim();
  const t = s.replace(/,/g, "");
  const n = Number(t);
  if (!Number.isFinite(n)) return s;

  const locale = (currentLang === "ja") ? "ja-JP" : "en-US";
  const dot = t.indexOf(".");
  if (dot >= 0) {
    const d = t.length - dot - 1;
    return n.toLocaleString(locale, { minimumFractionDigits: d, maximumFractionDigits: d });
  }
  return n.toLocaleString(locale);
}

// -------- DOM --------
const navEl = document.getElementById("nav");
const statusEl = document.getElementById("status");
const wrapEl = document.getElementById("tableWrap");
const monthSel = document.getElementById("monthSelect");
const acctSel = document.getElementById("accountSelect");
const searchInput = document.getElementById("searchInput");
const langSel = document.getElementById("langSelect");
const fileInput = document.getElementById("fileInput");
const useServerBtn = document.getElementById("useServerBtn");
const accountLabelEl = document.getElementById("accountLabel");
const columnToggleGroupEl = document.getElementById("columnToggleGroup");
const companyHeaderEl = document.getElementById("companyHeader");
const monthLabelTextEl = document.getElementById("monthLabelText");
const searchLabelTextEl = document.getElementById("searchLabelText");

const PARTNER_REPORTS = {
  receivables: {
    partnerType: "C",
    accountNumber: "10A100090",
    normalSide: "debit",
  },
  payables: {
    partnerType: "S",
    accountNumber: "10B100040",
    normalSide: "credit",
  },
};

function isPartnerReportView(viewKey = currentView) {
  return Object.prototype.hasOwnProperty.call(PARTNER_REPORTS, viewKey);
}

// -------- Helpers --------
function setStatus(msg) { statusEl.textContent = msg; }

function escapeHtml(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

// --- CSV parser (quotes/commas/CRLF) ---
/*
EN: CSV parsing
A minimal RFC4180-style parser (supports quoted fields, escaped quotes, CRLF).
JA: CSVパース
引用符・エスケープ・CRLFに対応した最小限のCSVパーサです（外部ライブラリ不使用）。
*/

function parseCSV(text) {
  // Remove UTF-8 BOM
  if (text && text.charCodeAt(0) === 0xFEFF) text = text.slice(1);

  const rows = [];
  let row = [];
  let field = "";
  let inQuotes = false;

  for (let i = 0; i < text.length; i++) {
    const c = text[i];

    if (inQuotes) {
      if (c === '"') {
        if (text[i + 1] === '"') { field += '"'; i++; }
        else inQuotes = false;
      } else field += c;
      continue;
    }

    if (c === '"') inQuotes = true;
    else if (c === ",") { row.push(field); field = ""; }
    else if (c === "\n") {
      row.push(field.replace(/\r$/, ""));
      rows.push(row);
      row = []; field = "";
    } else field += c;
  }

  row.push(field.replace(/\r$/, ""));
  rows.push(row);

  if (rows.length > 0 && rows[rows.length - 1].length === 1 && rows[rows.length - 1][0] === "") rows.pop();
  return rows;
}

async function fetchCSV(url) {
  if (CSV_CACHE.has(url)) return CSV_CACHE.get(url);

  const res = await fetch(url, { cache: "no-store" });
  if (!res.ok) throw new Error(`Failed to load ${url} (${res.status})`);
  const text = await res.text();
  const rows = parseCSV(text);
  CSV_CACHE.set(url, rows);
  return rows;
}

function buildOptions(selectEl, values, { includeAll = true, allLabel = "（全て）" } = {}) {
  selectEl.innerHTML = "";
  if (includeAll) {
    const opt = document.createElement("option");
    opt.value = "";
    opt.textContent = allLabel;
    selectEl.appendChild(opt);
  }
  for (const v of values) {
    const opt = document.createElement("option");
    if (v && typeof v === "object") {
      const val = (v.value ?? "").toString();
      const lab = (v.label ?? v.value ?? "").toString();
      opt.value = val;
      opt.textContent = lab;
    } else {
      opt.value = v;
      opt.textContent = v;
    }
    selectEl.appendChild(opt);
  }
}

function detectColumnIndex(header, candidates) {
  const lower = header.map(h => String(h).trim().toLowerCase());
  for (const c of candidates) {
    const idx = lower.indexOf(String(c).trim().toLowerCase());
    if (idx >= 0) return idx;
  }
  return -1;
}

function getAccountColumnIndexForView(header, viewKey) {
  if (!header || header.length === 0) return -1;

  // BS/PL can have duplicate account-number headers. Prefer the *last* matching column
  // so filters and the account selector match the visible account-number column.
  if (viewKey === "balance_sheet" || viewKey === "pnl") {
    const hn = header.map(normLabel);
    const cand = new Set(ACCOUNT_COL_CANDIDATES.map(normLabel));
    let last = -1;
    for (let i = 0; i < hn.length; i++) {
      if (cand.has(hn[i])) last = i;
    }
    if (last >= 0) return last;
  }

  return detectColumnIndex(header, ACCOUNT_COL_CANDIDATES);
}

function detectColumnIndexNorm(header, candidates) {
  const hn = header.map(normLabel);
  for (const c of candidates) {
    const idx = hn.indexOf(normLabel(c));
    if (idx >= 0) return idx;
  }
  return -1;
}

function extractUniqueColumnValues(rows, colIdx, limit = 200000) {
  const set = new Set();
  for (let i = 1; i < rows.length && i <= limit; i++) {
    const v = rows[i]?.[colIdx];
    if (v !== undefined && v !== null && String(v).trim() !== "") set.add(String(v).trim());
  }
  return Array.from(set).sort();
}

// Candidates
const ACCOUNT_COL_CANDIDATES = [
  "Ledger_Account_Number", "Account", "Account_Number", "Account Number", "Account Code",
  "account", "account_code", "account_number",
  "勘定科目", "勘定科目コード", "科目", "科目コード",
  // if your dataset uses these:
  "Debit account code", "Credit account code", "借方科目コード", "貸方科目コード"
];

const ACCOUNT_NAME_COL_CANDIDATES = [
  "Ledger_Account_Name", "Ledger Account Name", "ledger_account_name", "ledger account name",
  "eTax_Account_Name", "eTax Account Name", "etax_account_name", "etax account name",
  "Account_Name", "Account Name", "account_name", "account name",
  "Debit account name", "Credit account name",
  "借方科目名", "貸方科目名", "勘定科目名", "科目名", "科目"
];

function detectAccountNameIndex(header, acctIdx) {
  if (acctIdx < 0) return -1;
  const h = normLabel(header?.[acctIdx] ?? "");

  const pairs = [
    { code: ["ledger_account_number", "ledger account number"], names: ["ledger_account_name", "ledger account name"] },
    { code: ["ledger_account_number", "ledger account number", "account_number", "account number"], names: ["etax_account_name", "etax account name", "etax_account_name"] },
    { code: ["account_number", "account number", "account code", "account"], names: ["account_name", "account name"] },
    { code: ["debit account code", "借方科目コード"], names: ["debit account name", "借方科目名"] },
    { code: ["credit account code", "貸方科目コード"], names: ["credit account name", "貸方科目名"] },
    { code: ["勘定科目コード", "科目コード"], names: ["勘定科目名", "科目名", "科目"] },
  ];

  for (const p of pairs) {
    if (p.code.map(normLabel).includes(h)) {
      const idx = detectColumnIndexNorm(header, p.names);
      if (idx >= 0) return idx;
    }
  }
  return detectColumnIndexNorm(header, ACCOUNT_NAME_COL_CANDIDATES);
}

function extractAccountOptions(rows, codeIdx, nameIdx, limit = 200000) {
  const map = new Map(); // code -> name (or "")
  for (let i = 1; i < rows.length && i <= limit; i++) {
    const code = String(rows[i]?.[codeIdx] ?? "").trim();
    if (!code) continue;

    const name = (nameIdx >= 0) ? String(rows[i]?.[nameIdx] ?? "").trim() : "";
    if (!map.has(code)) {
      map.set(code, name);
    } else if (name && !(map.get(code) || "").trim()) {
      // Prefer non-empty name
      map.set(code, name);
    }
  }

  const out = [];
  for (const [code, name] of map.entries()) {
    out.push({ value: code, label: name || code });
  }

  // Sort by label then code
  out.sort((a, b) => {
    const la = String(a.label || "");
    const lb = String(b.label || "");
    const c1 = la.localeCompare(lb);
    if (c1 !== 0) return c1;
    return String(a.value).localeCompare(String(b.value));
  });

  return out;
}

const MONTH_COL_CANDIDATES = ["Month", "対象月", "month"];

// -------- Rendering --------

function isAccountNumberColumn(headerCellNorm, headerCellRaw) {
  // Account number columns toggle (and related structural parent key)
  // Covers:
  //   - explicit header names (Ledger_Account_Number / Account_Number / Counterpart_Account_Number ...)
  //   - Japanese labels (勘定科目番号 / 科目番号)
  //   - coded headers (JP.. etc) by resolving to CODE_TO_EN and checking "...account number"
  //   - "Parent" (requested to be toggled together with account numbers)
  if (headerCellNorm === "parent") return true;
  if (headerCellNorm.includes("account number")) return true;  // Account_Number, Ledger_Account_Number, Counterpart_Account_Number...
  const raw = String(headerCellRaw ?? "");
  if (raw.includes("勘定科目番号") || raw.includes("科目番号")) return true;

  // If header is a code, resolve to English label and decide by semantics
  const code = resolveHeaderToCode(raw);
  if (code) {
    const en = CODE_TO_EN[code] || "";
    const hn = normLabel(en);
    if (hn === "parent") return true;
    if (hn.includes("account number")) return true;
  }
  return false;
}

function isCodeColumn(headerCellNorm, headerCellRaw) {
  // Code columns toggle:
  //   - explicit header names ending with Code / _Code (DebitAccountCode, Debit account code, ...)
  //   - Japanese labels containing "コード"
  //   - coded headers (JP.. etc) by resolving to CODE_TO_EN and checking if the label ends with "code"
  if (headerCellNorm.endsWith(" code") || headerCellNorm.endsWith("code")) return true;
  const raw = String(headerCellRaw ?? "");
  if (raw.includes("コード")) return true;

  // If header is a code, resolve to English label and decide by semantics
  const code = resolveHeaderToCode(raw);
  if (code) {
    const en = CODE_TO_EN[code] || "";
    const hn = normLabel(en);
    if (hn.endsWith(" code") || hn.endsWith("code")) return true;
  }
  return false;
}

function tToggleText(stateOn) {
  // stateOn means "currently showing"
  const ja = {
    code_show: "コード列を表示",
    code_hide: "コード列を非表示",
  };
  const en = {
    code_show: "Show code columns",
    code_hide: "Hide code columns",
  };
  const dict = (currentLang === "en") ? en : ja;
  return stateOn ? dict.code_hide : dict.code_show;
}

function updateColumnToggleButtons() {
  if (toggleCodeColsBtn) {
    const actionText = tToggleText(showCodeCols);
    toggleCodeColsBtn.textContent = currentLang === "en" ? "Code cols" : "コード列";
    toggleCodeColsBtn.title = actionText;
    toggleCodeColsBtn.setAttribute("aria-label", actionText);
    toggleCodeColsBtn.setAttribute("aria-pressed", String(showCodeCols));
    toggleCodeColsBtn.classList.toggle("active", showCodeCols);
  }
}

function initColumnToggleButtons() {
  if (!columnToggleGroupEl) return;

  // Create the button only once.
  if (!toggleCodeColsBtn) {
    toggleCodeColsBtn = document.createElement("button");
    toggleCodeColsBtn.type = "button";
    toggleCodeColsBtn.className = "column-toggle-button";
    toggleCodeColsBtn.addEventListener("click", async () => {
      showCodeCols = !showCodeCols;
      localStorage.setItem("ledger_show_code_cols", showCodeCols ? "1" : "0");
      updateColumnToggleButtons();
      await refresh({ skipReloadCsv: true });
    });
    columnToggleGroupEl.appendChild(toggleCodeColsBtn);
  }

  updateColumnToggleButtons();
}

function getVisibleIdxs(header, viewKey) {
  let baseIdxs = null;

  // 1) Prefer hide-by-index (needed for BS/PL where headers can be duplicated)
  const hiddenIdxSet = HIDDEN_COL_INDEXES_BY_VIEW[viewKey];
  if (hiddenIdxSet) {
    baseIdxs = [];
    for (let i = 0; i < header.length; i++) {
      if (!hiddenIdxSet.has(i)) baseIdxs.push(i);
    }
  } else {
    // 2) Fallback: hide-by-header-name (for other views)
    const hiddenSet = HIDDEN_HEADERS_BY_VIEW_NORM[viewKey];
    if (!hiddenSet) baseIdxs = header.map((_, i) => i);
    else {
      const hn = header.map(normLabel);
      baseIdxs = [];
      for (let i = 0; i < header.length; i++) {
        if (!hiddenSet.has(hn[i])) baseIdxs.push(i);
      }
    }
  }

  // 3) Apply the runtime code-column toggle (account/subaccount numbers and xx_Code columns).
  const hnAll = header.map(normLabel);
  const out = [];
  for (const i of baseIdxs) {
    const hn = hnAll[i];
    const raw = header[i];
    if (!showCodeCols && (isAccountNumberColumn(hn, raw) || isCodeColumn(hn, raw))) continue;
    out.push(i);
  }
  return out;
}


// -------- Rendering --------
/*
EN: Table rendering
- Computes visible columns (per-view hidden columns + the unified code-column toggle).
- Applies alignment/formatting (int/number/text) and optional indentation for BS/PL account names.
JA: 表描画
- viewごとの非表示列 + コード列の一括切替を反映して表示列を決定します。
- 整数/数値/テキスト整形、B/S・P/Lの階層表示（Levelに基づくインデント）を適用します。
*/
function renderTable(rows, maxRows = DEFAULT_MAX_ROWS) {
  wrapEl.classList.remove("partner-report-wrap");
  if (!rows || rows.length === 0) {
    wrapEl.innerHTML = "<div style='padding:12px'>No data</div>";
    return;
  }

  const header = rows[0];
  const body = rows.slice(1, 1 + maxRows);
  const noDataRows = body.length === 0;

  // Determine visible columns (hide some only for specific views like BS/PL)
  const visibleIdxs = getVisibleIdxs(header, currentView);

  // Column styles for all columns (we will pick by index)
  const colStyles = header.map(getColStyle);

  // Indent eTax_Account_Name using Level for B/S and P/L (period-end statements)
  const headerNorm = header.map(normLabel);
  const levelIdx = headerNorm.indexOf("level");
  const etaxNameIdx = headerNorm.indexOf("etax account name");
  const indentEtaxName = (currentView === "balance_sheet" || currentView === "pnl") && levelIdx >= 0 && etaxNameIdx >= 0;

  // let html = '<table id="dataTable"><thead><tr>';
  let html = `<table id="dataTable" class="${noDataRows ? "no-data" : ""}" style="${noDataRows ? "table-layout:fixed;width:100%;" : ""}"><thead><tr>`;
  for (const i of visibleIdxs) {
    const st = colStyles[i] || { align: "left", fmt: "text" };
    const thExtra = noDataRows
      ? "white-space:normal;max-width:140px;overflow-wrap:anywhere;word-break:break-word;line-height:1.2;"
      : "";
    html += `<th style="text-align:${st.align};${thExtra}">${escapeHtml(tHeader(header[i]))}</th>`;
  }
  html += "</tr></thead><tbody>";

  for (const r of body) {
    html += "<tr>";
    for (const i of visibleIdxs) {
      const st = colStyles[i] || { align: "left", fmt: "text" };
      const raw = r[i] ?? "";

      let disp = String(raw);

      // Integer / number formatting
      if (st.fmt === "int" && isNumericLike(raw)) {
        const t = String(raw).trim().replace(/,/g, "");
        const n = Number(t);
        if (Number.isFinite(n) && Math.floor(n) === n) disp = String(n);
      } else if (st.fmt === "number" && isNumericLike(raw)) {
        disp = formatNumberLike(raw);
      }

      // Indent eTax_Account_Name based on (Level - 1) * 2 spaces (even if Level column itself is hidden)
      let extraStyle = "";
      if (indentEtaxName && i === etaxNameIdx) {
        const lv = parseInt(String(r?.[levelIdx] ?? "").trim(), 10);
        const indent = Number.isFinite(lv) ? Math.max(0, (lv - 1) * 2) : 0;
        if (indent > 0) {
          disp = " ".repeat(indent) + disp;
          extraStyle = "white-space:pre;";
        }
      }

      html += `<td style="text-align:${st.align};${extraStyle}">${escapeHtml(disp)}</td>`;
    }
    html += "</tr>";
  }

  html += "</tbody></table>";
  wrapEl.innerHTML = html;

  const total = rows.length - 1;
  if (total > maxRows) setStatus(`${total} rows（先頭 ${maxRows} 行のみ表示）`);
}

function numberValue(value) {
  const n = Number(String(value ?? "").replace(/,/g, "").trim());
  return Number.isFinite(n) ? n : 0;
}

async function fetchOptionalCSV(url) {
  try {
    return await fetchCSV(url);
  } catch (error) {
    console.warn(`Optional CSV not available: ${url}`, error);
    return [];
  }
}

function ledgerRowIndexes(rows) {
  const header = rows?.[0] || [];
  return {
    transactionId: detectColumnIndexNorm(header, ["Transaction_ID", "JP07a"]),
    lineId: detectColumnIndexNorm(header, ["Line_ID", "JP08a"]),
    ledgerSide: detectColumnIndexNorm(header, ["Ledger_Side"]),
    account: detectColumnIndexNorm(header, ["Ledger_Account_Number"]),
    subCode: detectColumnIndexNorm(header, ["Subaccount_Code"]),
    subName: detectColumnIndexNorm(header, ["Subaccount_Name"]),
    debit: detectColumnIndexNorm(header, ["Debit_Amount"]),
    credit: detectColumnIndexNorm(header, ["Credit_Amount"]),
    counterpart: detectColumnIndexNorm(header, ["Counterpart_Account_Number"]),
  };
}

function reportMovement(row, idx, config) {
  const debit = numberValue(row[idx.debit]);
  const credit = numberValue(row[idx.credit]);
  return config.normalSide === "debit" ? debit - credit : credit - debit;
}

function classifySettlement(counterpart, viewKey) {
  if (counterpart === "10A100020") return "cash";
  if (viewKey === "receivables") {
    if (counterpart === "10A100060") return "note";
    if (counterpart === "10E200690") return "fee";
    if (["10D100110", "10D100111", "10D100112"].includes(counterpart)) return "discount";
  } else {
    if (counterpart === "10B100030") return "note";
    if (["10E100120", "10E100121", "10E100122"].includes(counterpart)) return "discount";
  }
  return "other";
}

function ensurePartner(map, code, name = "") {
  const key = String(code ?? "").trim();
  if (!key) return null;
  if (!map.has(key)) {
    map.set(key, {
      code: key,
      name: String(name || key).trim(),
      opening: 0,
      occurrence: 0,
      discount: 0,
      note: 0,
      cash: 0,
      fee: 0,
      other: 0,
      applied: 0,
      balance: 0,
    });
  } else if (name && (!map.get(key).name || map.get(key).name === key)) {
    map.get(key).name = String(name).trim();
  }
  return map.get(key);
}

async function loadPartnerMaster(config) {
  const fileName = currentLang === "en" ? "trading_partner_en.csv" : "trading_partner.csv";
  const rows = await fetchOptionalCSV(joinUrlPath(DATA_ROOT, currentLang, "source", fileName));
  const partners = new Map();
  if (!rows.length) return partners;
  const header = rows[0];
  const categoryIdx = detectColumnIndexNorm(header, ["category"]);
  const codeIdx = detectColumnIndexNorm(header, ["code"]);
  const nameIdx = detectColumnIndexNorm(header, ["name"]);
  const expected = config.partnerType === "C"
    ? new Set(["得意先", "customer"])
    : new Set(["仕入先", "supplier"]);
  for (const row of rows.slice(1)) {
    const category = String(row[categoryIdx] || "").trim().toLowerCase();
    if (expected.has(category)) partners.set(String(row[codeIdx]).trim(), String(row[nameIdx]).trim());
  }
  return partners;
}

async function loadInitialPartnerBalances(config) {
  const candidates = [
    joinUrlPath(DATA_ROOT, currentLang, "source", "trading_partner_balance.csv"),
    joinUrlPath(DATA_ROOT, "ja", "source", "trading_partner_balance.csv"),
  ];
  let rows = [];
  for (const url of [...new Set(candidates)]) {
    try {
      rows = await fetchCSV(url);
    } catch (error) {
      console.warn(`Opening balance CSV candidate failed: ${url}`, error);
    }
    if (rows.length) break;
  }
  const balances = new Map();
  if (!rows.length) {
    throw new Error(currentLang === "en"
      ? "The required trading_partner_balance.csv could not be loaded. Annual balances were not calculated."
      : "必須の trading_partner_balance.csv を読み込めません。ゼロ補完せず、年間残高計算を停止しました。");
  }
  const header = rows[0];
  const typeIdx = detectColumnIndexNorm(header, ["partner_type"]);
  const codeIdx = detectColumnIndexNorm(header, ["partner_code"]);
  const balanceIdx = detectColumnIndexNorm(header, ["opening_balance"]);
  for (const row of rows.slice(1)) {
    if (String(row[typeIdx]).trim() !== config.partnerType) continue;
    const code = String(row[codeIdx]).trim();
    const raw = String(row[balanceIdx] ?? "").replace(/,/g, "").trim();
    if (!code || !raw || !Number.isFinite(Number(raw))) throw new Error(`Invalid partner opening balance: ${config.partnerType}/${code || "(blank)"}`);
    if (balances.has(code)) throw new Error(`Duplicate partner opening balance: ${config.partnerType}/${code}`);
    balances.set(code, Number(raw));
  }
  if (!balances.size) throw new Error(`No opening balances found for partner type ${config.partnerType}.`);
  return balances;
}

async function buildPartnerReport(viewKey, month) {
  const config = PARTNER_REPORTS[viewKey];
  if (dataMode === "local") {
    throw new Error(currentLang === "en"
      ? "Annual partner balances require all 12 server ledger files. Use server data."
      : "年間取引先残高には12か月分の総勘定元帳が必要です。「Use server data」を選択してください。");
  }
  const partners = new Map();
  const [master, initialBalances] = await Promise.all([
    loadPartnerMaster(config),
    loadInitialPartnerBalances(config),
  ]);
  if (!master.size) throw new Error(currentLang === "en" ? "Trading partner master is missing." : "取引先マスターがありません。");
  for (const [code, name] of master.entries()) ensurePartner(partners, code, name);
  for (const code of master.keys()) {
    if (!initialBalances.has(code)) throw new Error(`Opening balance is missing: ${config.partnerType}/${code}`);
  }
  for (const [code, opening] of initialBalances.entries()) {
    if (!master.has(code)) throw new Error(`Partner master entry is missing: ${config.partnerType}/${code}`);
    ensurePartner(partners, code).balance = opening;
  }

  const months = (INDEX?.months || []).filter(value => value <= month);
  if (!months.length || !months.includes(month)) throw new Error(`Invalid report month: ${month}`);
  const ledgerMonths = await Promise.all(months.map(async value => ({
    month: value,
    rows: await fetchCSV(resolveCsvUrl("ledger", value)),
  })));
  const snapshots = new Map();
  const dataIssues = [];

  for (const item of ledgerMonths) {
    const idx = ledgerRowIndexes(item.rows);
    if (idx.account < 0 || idx.subCode < 0 || idx.debit < 0 || idx.credit < 0) throw new Error(`Required ledger columns are missing: ${item.month}`);
    for (const partner of partners.values()) {
      partner.opening = partner.balance;
      partner.occurrence = partner.discount = partner.note = partner.cash = partner.fee = partner.other = partner.applied = 0;
    }
    for (const row of item.rows.slice(1)) {
      if (String(row[idx.account]).trim() !== config.accountNumber) continue;
      const code = String(row[idx.subCode] || "").trim();
      if (!code) {
        const debit = numberValue(row[idx.debit]);
        const credit = numberValue(row[idx.credit]);
        if (debit || credit) {
          const issue = {
            month: item.month,
            transactionId: idx.transactionId >= 0 ? String(row[idx.transactionId] || "").trim() : "",
            lineId: idx.lineId >= 0 ? String(row[idx.lineId] || "").trim() : "",
            ledgerSide: idx.ledgerSide >= 0 ? String(row[idx.ledgerSide] || "").trim() : "",
            debit,
            credit,
          };
          dataIssues.push(issue);
          console.error("Trading partner ID is missing on a ledger row", issue);
        }
        continue;
      }
      if (!partners.has(code)) throw new Error(`Partner master/opening balance is missing: ${config.partnerType}/${code} (${item.month})`);
      const partner = ensurePartner(partners, code, row[idx.subName]);

      const occurrence = config.normalSide === "debit"
        ? numberValue(row[idx.debit])
        : numberValue(row[idx.credit]);
      const settlement = config.normalSide === "debit"
        ? numberValue(row[idx.credit])
        : numberValue(row[idx.debit]);
      partner.occurrence += occurrence;
      if (settlement) {
        const bucket = classifySettlement(String(row[idx.counterpart] || "").trim(), viewKey);
        partner[bucket] += settlement;
        partner.applied += settlement;
      }
    }
    for (const partner of partners.values()) partner.balance = partner.opening + partner.occurrence - partner.applied;
    const rows = Array.from(partners.values(), partner => ({ ...partner }));
    snapshots.set(item.month, rows);
  }
  const selected = snapshots.get(month).sort((a, b) =>
    a.name.localeCompare(b.name, currentLang === "ja" ? "ja" : "en", { numeric: true })
  );
  selected.dataIssues = dataIssues.filter(issue => issue.month === month);
  return selected;
}

function reportText(viewKey) {
  const receivables = viewKey === "receivables";
  if (currentLang === "en") {
    return {
      title: receivables ? "Accounts Receivable Summary" : "Accounts Payable Summary",
      partner: "Trading partner",
      opening: "Opening balance",
      occurrence: "New charges",
      discount: "Discount / adjustment",
      note: receivables ? "Notes received" : "Notes payable",
      cash: "Cash / bank",
      extra: receivables ? "Transfer fee" : "Offset / other",
      other: "Offset / other",
      applied: "Applied total",
      balance: "Balance",
      total: "Total",
      unit: "Unit: JPY",
      journalTitle: "Related journal entries",
      journalIncludedTitle: receivables ? "A/R journal entries" : "A/P journal entries",
      journalRelatedTitle: "Other transactions for this partner",
      journalEmpty: "No related journal entries were found for this month.",
      journalSectionEmpty: "No entries.",
      selectHint: "Click a trading partner row to show related journal entries.",
      appliedFormula: receivables
        ? "Applied total = Discount / adjustment + Notes received + Cash / bank + Transfer fee + Offset / other"
        : "Applied total = Discount / adjustment + Notes payable + Cash / bank + Offset / other",
      balanceFormula: "Balance = Opening balance + New charges - Applied total",
      appliedNote: "Each applied amount is shown as a positive value and is subtracted when calculating the balance.",
    };
  }
  return {
    title: receivables ? "売掛金集計表" : "買掛金集計表",
    partner: "取引先名",
    opening: "前期繰越",
    occurrence: "発生",
    discount: "値引",
    note: "手形",
    cash: "現金",
    extra: receivables ? "振込料" : "相殺他",
    other: "相殺・その他",
    applied: "消込欄計",
    balance: "残高",
    total: "合計",
    unit: "単位（円）",
    journalTitle: "関連する仕訳日記帳",
    journalIncludedTitle: receivables ? "売掛金に関係する仕訳" : "買掛金に関係する仕訳",
    journalRelatedTitle: "当月のその他の取引",
    journalEmpty: "この月に関連する仕訳はありません。",
    journalSectionEmpty: "該当する仕訳はありません。",
    selectHint: "取引先の行をクリックすると、関連する仕訳を下に表示します。",
    appliedFormula: receivables
      ? "消込欄計 ＝ 値引 ＋ 手形 ＋ 現金 ＋ 振込料 ＋ 相殺・その他"
      : "消込欄計 ＝ 値引 ＋ 手形 ＋ 現金 ＋ 相殺・その他",
    balanceFormula: "残高 ＝ 前期繰越 ＋ 発生 − 消込欄計",
    appliedNote: "値引などの各欄は消込額を正数で表示し、残高計算時に差し引きます。",
  };
}

function journalDetailText(viewKey) {
  return currentLang === "en" ? {
    date: "Date", voucher: "Voucher", description: "Description",
    debit: "Debit account", debitAmount: "Debit amount",
    credit: "Credit account", creditAmount: "Credit amount",
  } : {
    date: "伝票日付", voucher: "伝票番号", description: "摘要文",
    debit: "借方科目", debitAmount: "借方金額",
    credit: "貸方科目", creditAmount: "貸方金額",
  };
}

function journalRowIndexes(rows) {
  const header = rows?.[0] || [];
  return {
    transactionId: detectColumnIndexNorm(header, ["JP07a", "Transaction_ID"]),
    lineId: detectColumnIndexNorm(header, ["JP08a", "Line_ID"]),
    date: detectColumnIndexNorm(header, ["JP07a_GL03_03", "Transaction_Date"]),
    voucher: detectColumnIndexNorm(header, ["JP07a_GL03_01", "Voucher_Number"]),
    description: detectColumnIndexNorm(header, ["JP08a_GL04_03", "Description"]),
    debitAccount: detectColumnIndexNorm(header, ["JP06e_GE24_01", "Debit_Account_Number"]),
    debitName: detectColumnIndexNorm(header, ["JP06e_GE24_02", "Debit_Account_Name"]),
    debitAmount: detectColumnIndexNorm(header, ["Debit_Amount"]),
    debitSubCode: detectColumnIndexNorm(header, ["JP05a_01", "Debit_Subaccount_Code"]),
    debitSubName: detectColumnIndexNorm(header, ["JP05a_02", "BS04fb_02", "Debit_Subaccount_Name"]),
    creditAccount: detectColumnIndexNorm(header, ["JP06f_GE24_01", "Credit_Account_Number"]),
    creditName: detectColumnIndexNorm(header, ["JP06f_GE24_02", "Credit_Account_Name"]),
    creditAmount: detectColumnIndexNorm(header, ["Credit_Amount"]),
    creditSubCode: detectColumnIndexNorm(header, ["JP05b_01", "Credit_Subaccount_Code"]),
    creditSubName: detectColumnIndexNorm(header, ["JP05b_02", "BS04fc_02", "Credit_Subaccount_Name"]),
  };
}

async function renderPartnerJournalDetailLegacy(viewKey, month, partner) {
  const target = document.getElementById("partnerJournalDetail");
  if (!target) return;
  const text = reportText(viewKey);
  target.innerHTML = `<h2>${escapeHtml(text.journalTitle)} — ${escapeHtml(partner.name)}</h2><p>${currentLang === "en" ? "Loading..." : "読込中..."}</p>`;
  try {
    const rows = await fetchCSV(resolveCsvUrl("journal", month));
    const idx = journalRowIndexes(rows);
    const required = [idx.date, idx.description, idx.debitAccount, idx.debitAmount, idx.debitSubCode, idx.creditAccount, idx.creditAmount, idx.creditSubCode];
    if (required.some(value => value < 0)) throw new Error(`Required journal columns are missing: ${month}`);
    const account = PARTNER_REPORTS[viewKey].accountNumber;
    const normalizedPartnerName = String(partner.name).normalize("NFKC").replace(/\s+/g, "").toLowerCase();
    const shortPartnerName = normalizedPartnerName.replace(/株式会社|有限会社|合同会社|合資会社|合名会社/g, "");
    const partnerNameVariants = [...new Set([normalizedPartnerName, shortPartnerName].filter(value => value.length >= 2))];
    const containsPartnerName = value => {
      const normalized = String(value || "").normalize("NFKC").replace(/\s+/g, "").toLowerCase();
      return partnerNameVariants.some(name => normalized.includes(name));
    };
    const classified = rows.slice(1).map(row => {
      const reportAccountMatch =
        (String(row[idx.debitAccount] || "").trim() === account && String(row[idx.debitSubCode] || "").trim() === partner.code) ||
        (String(row[idx.creditAccount] || "").trim() === account && String(row[idx.creditSubCode] || "").trim() === partner.code);
      const nameMatch =
        (idx.debitSubName >= 0 && containsPartnerName(row[idx.debitSubName])) ||
        (idx.creditSubName >= 0 && containsPartnerName(row[idx.creditSubName])) ||
        containsPartnerName(row[idx.description]);
      return { row, reportAccountMatch, related: reportAccountMatch || nameMatch };
    }).filter(item => item.related);
    const related = classified;
    if (!related.length) {
      target.innerHTML = `<h2>${escapeHtml(text.journalTitle)} — ${escapeHtml(partner.name)}</h2><p>${escapeHtml(text.journalEmpty)}</p>`;
      return;
    }
    const label = journalDetailText(viewKey);
    const accountLabel = (row, codeIdx, nameIdx) => [row[codeIdx], nameIdx >= 0 ? row[nameIdx] : ""].filter(Boolean).join(" ");
    const renderTable = (title, items, sectionClass) => {
      let html = `<section class="partner-journal-section ${sectionClass}"><h3>${escapeHtml(title)}</h3>`;
      if (!items.length) return html + `<p class="partner-journal-empty">${escapeHtml(text.journalSectionEmpty)}</p></section>`;
      html += `<div class="partner-journal-table-wrap"><table class="partner-journal-table"><thead><tr>
        <th>${escapeHtml(label.date)}</th><th>${escapeHtml(label.voucher)}</th><th>${escapeHtml(label.description)}</th>
        <th>${escapeHtml(label.debit)}</th><th>${escapeHtml(label.debitAmount)}</th>
        <th>${escapeHtml(label.credit)}</th><th>${escapeHtml(label.creditAmount)}</th></tr></thead><tbody>`;
      for (const item of items) {
        const row = item.row;
        html += `<tr><td>${escapeHtml(row[idx.date])}</td><td>${escapeHtml(idx.voucher >= 0 ? row[idx.voucher] : "")}</td><td>${escapeHtml(row[idx.description])}</td>
          <td>${escapeHtml(accountLabel(row, idx.debitAccount, idx.debitName))}</td><td>${formatNumberLike(row[idx.debitAmount])}</td>
          <td>${escapeHtml(accountLabel(row, idx.creditAccount, idx.creditName))}</td><td>${formatNumberLike(row[idx.creditAmount])}</td></tr>`;
      }
      return html + "</tbody></table></div></section>";
    };
    const included = related.filter(item => item.reportAccountMatch);
    const others = related.filter(item => !item.reportAccountMatch);
    target.innerHTML = `<h2>${escapeHtml(text.journalTitle)} — <span class="partner-code">${escapeHtml(partner.code)}</span>${escapeHtml(partner.name)}</h2>
      <div class="partner-journal-grid">${renderTable(text.journalIncludedTitle, included, "is-included")}${renderTable(text.journalRelatedTitle, others, "is-related")}</div>`;
  } catch (error) {
    console.error(error);
    target.innerHTML = `<h2>${escapeHtml(text.journalTitle)} — ${escapeHtml(partner.name)}</h2><p class="partner-report__error">${escapeHtml(error.message || error)}</p>`;
  }
}

async function renderPartnerJournalDetail(viewKey, month, partner, options = {}) {
  const target = document.getElementById("partnerJournalDetail");
  if (!target) return;
  target.closest(".partner-report")?.classList.add("has-journal-detail");
  const text = reportText(viewKey);
  const pastMonths = Math.max(0, Math.min(3, Number(options.pastMonths) || 0));
  const futureMonths = Math.max(0, Math.min(3, Number(options.futureMonths) || 0));
  const availableJournalMonths = Array.isArray(INDEX?.views?.journal?.available)
    ? INDEX.views.journal.available
    : (INDEX?.months || []);
  const availableLedgerMonths = new Set(Array.isArray(INDEX?.views?.ledger?.available)
    ? INDEX.views.ledger.available
    : (INDEX?.months || []));
  const availableMonths = availableJournalMonths
    .filter(value => value !== "ALL" && availableLedgerMonths.has(value))
    .sort();
  const selectedMonthIndex = availableMonths.indexOf(month);
  const rangeText = currentLang === "en" ? {
    current: "Current month only", past: "Past", future: "Future", month: "month(s)",
    range: "Displayed period", loading: "Loading...",
  } : {
    current: "\u5f53\u6708\u306e\u307f", past: "\u904e\u53bb", future: "\u672a\u6765", month: "\u304b\u6708",
    range: "\u8868\u793a\u671f\u9593", loading: "\u8aad\u8fbc\u4e2d...",
  };
  const rangeControls = () => {
    const rangeButton = (direction, count, disabled) => {
      const active = (direction === "past" ? pastMonths : futureMonths) === count;
      const directionLabel = direction === "past" ? rangeText.past : rangeText.future;
      const label = currentLang === "en"
        ? `${directionLabel} ${count} ${count === 1 ? "month" : "months"}`
        : `${directionLabel}${count}${rangeText.month}`;
      return `<button type="button" class="partner-journal-range__button${active ? " is-active" : ""}" data-range-direction="${direction}" data-range-months="${count}"${disabled ? " disabled" : ""}>${escapeHtml(label)}</button>`;
    };
    const pastAvailable = selectedMonthIndex < 0 ? 0 : selectedMonthIndex;
    const futureAvailable = selectedMonthIndex < 0 ? 0 : availableMonths.length - selectedMonthIndex - 1;
    return `<div class="partner-journal-range" role="group" aria-label="${escapeHtml(rangeText.range)}">
      <button type="button" class="partner-journal-range__button${pastMonths === 0 && futureMonths === 0 ? " is-active" : ""}" data-range-reset="true">${escapeHtml(rangeText.current)}</button>
      <span class="partner-journal-range__group"><span class="partner-journal-range__label">${escapeHtml(rangeText.past)}</span>${[1, 2, 3].map(count => rangeButton("past", count, count > pastAvailable)).join("")}</span>
      <span class="partner-journal-range__group"><span class="partner-journal-range__label">${escapeHtml(rangeText.future)}</span>${[1, 2, 3].map(count => rangeButton("future", count, count > futureAvailable)).join("")}</span>
    </div>`;
  };
  const heading = () => `<h2>${escapeHtml(text.journalTitle)} - <span class="partner-code">${escapeHtml(partner.code)}</span>${escapeHtml(partner.name)}</h2>${rangeControls()}`;
  const bindRangeControls = () => {
    const reset = target.querySelector("[data-range-reset]");
    if (reset) reset.addEventListener("click", () => renderPartnerJournalDetail(viewKey, month, partner, { pastMonths: 0, futureMonths: 0 }));
    for (const button of target.querySelectorAll("[data-range-direction]")) {
      button.addEventListener("click", () => {
        const direction = button.dataset.rangeDirection;
        const count = Number(button.dataset.rangeMonths) || 0;
        renderPartnerJournalDetail(viewKey, month, partner, {
          pastMonths: direction === "past" ? count : pastMonths,
          futureMonths: direction === "future" ? count : futureMonths,
        });
      });
    }
  };
  target.innerHTML = `${heading()}<p>${escapeHtml(rangeText.loading)}</p>`;
  bindRangeControls();
  try {
    if (selectedMonthIndex < 0) throw new Error(`Invalid journal month: ${month}`);
    const firstMonthIndex = Math.max(0, selectedMonthIndex - pastMonths);
    const lastMonthIndex = Math.min(availableMonths.length - 1, selectedMonthIndex + futureMonths);
    const displayMonths = availableMonths.slice(firstMonthIndex, lastMonthIndex + 1);
    const account = PARTNER_REPORTS[viewKey].accountNumber;
    const related = [];
    for (const detailMonth of displayMonths) {
      const [journalRows, ledgerRows] = await Promise.all([
        fetchCSV(resolveCsvUrl("journal", detailMonth)),
        fetchCSV(resolveCsvUrl("ledger", detailMonth)),
      ]);
      const idx = journalRowIndexes(journalRows);
      const ledgerIdx = ledgerRowIndexes(ledgerRows);
      const required = [
        idx.transactionId, idx.lineId, idx.date, idx.description,
        idx.debitAccount, idx.debitAmount, idx.debitSubCode,
        idx.creditAccount, idx.creditAmount, idx.creditSubCode,
        ledgerIdx.transactionId, ledgerIdx.lineId, ledgerIdx.account, ledgerIdx.subCode,
      ];
      if (required.some(value => value < 0)) throw new Error(`Required trace columns are missing: ${detailMonth}`);
      const exactLineKeys = new Set();
      for (const row of ledgerRows.slice(1)) {
        if (String(row[ledgerIdx.account] || "").trim() !== account) continue;
        if (String(row[ledgerIdx.subCode] || "").trim() !== partner.code) continue;
        const transactionId = String(row[ledgerIdx.transactionId] || "").trim();
        const lineId = String(row[ledgerIdx.lineId] || "").trim();
        if (!transactionId || !lineId) {
          throw new Error(`Ledger trace ID is missing: ${detailMonth} ${partner.code}`);
        }
        exactLineKeys.add(`${transactionId}|${lineId}`);
      }
      for (const row of journalRows.slice(1)) {
        const transactionId = String(row[idx.transactionId] || "").trim();
        const lineId = String(row[idx.lineId] || "").trim();
        if (exactLineKeys.has(`${transactionId}|${lineId}`)) related.push({ row, idx, month: detailMonth });
      }
    }
    if (!related.length) {
      target.innerHTML = `${heading()}<p>${escapeHtml(text.journalEmpty)}</p>`;
      bindRangeControls();
      return;
    }
    const label = journalDetailText(viewKey);
    const accountLabel = (row, codeIdx, nameIdx) => [row[codeIdx], nameIdx >= 0 ? row[nameIdx] : ""].filter(Boolean).join(" ");
    const renderTable = (title, items, sectionClass) => {
      let html = `<section class="partner-journal-section ${sectionClass}"><h3>${escapeHtml(title)}</h3>`;
      if (!items.length) return html + `<p class="partner-journal-empty">${escapeHtml(text.journalSectionEmpty)}</p></section>`;
      html += `<div class="partner-journal-table-wrap"><table class="partner-journal-table"><thead><tr>
        <th>${currentLang === "en" ? "Month" : "\u5bfe\u8c61\u6708"}</th>
        <th>${currentLang === "en" ? "Transaction ID" : "\u4f1d\u7968ID"}</th>
        <th>${currentLang === "en" ? "Line ID" : "\u660e\u7d30\u884cID"}</th>
        <th>${escapeHtml(label.date)}</th><th>${escapeHtml(label.voucher)}</th><th>${escapeHtml(label.description)}</th>
        <th>${escapeHtml(label.debit)}</th><th>${escapeHtml(label.debitAmount)}</th>
        <th>${escapeHtml(label.credit)}</th><th>${escapeHtml(label.creditAmount)}</th></tr></thead><tbody>`;
      for (const item of items) {
        const row = item.row;
        const idx = item.idx;
        html += `<tr${item.month === month ? " class=\"is-current-month\"" : ""}><td>${escapeHtml(item.month)}</td><td>${escapeHtml(row[idx.transactionId])}</td><td>${escapeHtml(row[idx.lineId])}</td>
          <td>${escapeHtml(row[idx.date])}</td><td>${escapeHtml(idx.voucher >= 0 ? row[idx.voucher] : "")}</td><td>${escapeHtml(row[idx.description])}</td>
          <td>${escapeHtml(accountLabel(row, idx.debitAccount, idx.debitName))}</td><td>${formatNumberLike(row[idx.debitAmount])}</td>
          <td>${escapeHtml(accountLabel(row, idx.creditAccount, idx.creditName))}</td><td>${formatNumberLike(row[idx.creditAmount])}</td></tr>`;
      }
      return html + "</tbody></table></div></section>";
    };
    const displayedRange = `${displayMonths[0]} - ${displayMonths[displayMonths.length - 1]}`;
    target.innerHTML = `${heading()}<p class="partner-journal-range__period">${escapeHtml(rangeText.range)}: ${escapeHtml(displayedRange)}</p>
      <div class="partner-journal-grid">${renderTable(text.journalIncludedTitle, related, "is-included")}</div>`;
    bindRangeControls();
  } catch (error) {
    console.error(error);
    target.innerHTML = `${heading()}<p class="partner-report__error">${escapeHtml(error.message || error)}</p>`;
    bindRangeControls();
  }
}

function renderPartnerReport(rows, viewKey, month) {
  wrapEl.classList.add("partner-report-wrap");
  const text = reportText(viewKey);
  const partnerFilter = acctSel.value;
  const search = String(searchInput.value || "").trim().toLowerCase();
  const filtered = rows.filter(row =>
    (!partnerFilter || row.code === partnerFilter) &&
    (!search || row.name.toLowerCase().includes(search) || row.code.toLowerCase().includes(search))
  );
  const categoryKeys = viewKey === "receivables"
    ? ["discount", "note", "cash", "fee", "other"]
    : ["discount", "note", "cash", "other"];
  const amountKeys = ["opening", "occurrence", ...categoryKeys, "applied", "balance"];
  const totals = Object.fromEntries(amountKeys.map(key => [key, filtered.reduce((sum, row) => sum + row[key], 0)]));
  const amount = value => formatNumberLike(Math.round(value));
  const dataIssues = Array.isArray(rows.dataIssues) ? rows.dataIssues : [];
  const issueHtml = dataIssues.length ? `<div class="partner-report__error">
    <strong>${currentLang === "en" ? "Data inconsistency" : "\u30c7\u30fc\u30bf\u4e0d\u6574\u5408"}</strong>: ${dataIssues.length}
    <ul>${dataIssues.slice(0, 20).map(issue => `<li>${escapeHtml(issue.month)} / ${currentLang === "en" ? "Transaction" : "\u4f1d\u7968"} ${escapeHtml(issue.transactionId || "(blank)")} / ${currentLang === "en" ? "Line" : "\u660e\u7d30\u884c"} ${escapeHtml(issue.lineId || "(blank)")} / ${escapeHtml(issue.ledgerSide || "")}</li>`).join("")}</ul>
  </div>` : "";

  let html = `<section class="partner-report">
    <div class="partner-report__heading">
      <div class="partner-report__title"><h1>${escapeHtml(text.title)}</h1></div>
      <div class="partner-report__summary">
        <div><span>${escapeHtml(text.opening)}</span><strong>${amount(totals.opening)}</strong></div>
        <div><span>${escapeHtml(text.occurrence)}</span><strong>${amount(totals.occurrence)}</strong></div>
        <div><span>${escapeHtml(text.applied)}</span><strong>${amount(totals.applied)}</strong></div>
        <div class="balance"><span>${escapeHtml(text.balance)}</span><strong>${amount(totals.balance)}</strong></div>
      </div>
    </div>
    ${issueHtml}
    <div class="partner-report__calculation-note" role="note">
      <span>${escapeHtml(text.appliedFormula)}</span><span class="partner-report__calculation-separator" aria-hidden="true">｜</span>
      <span>${escapeHtml(text.balanceFormula)}</span><span class="partner-report__calculation-separator" aria-hidden="true">｜</span>
      <small>${escapeHtml(text.appliedNote)}</small>
      <span class="partner-report__unit">${escapeHtml(text.unit)}</span>
    </div>
    <div class="partner-report__table-wrap partner-report__status-wrap"><table id="dataTable" class="partner-report__table"><thead><tr>
      <th>${escapeHtml(text.partner)}</th><th>${escapeHtml(text.opening)}</th><th>${escapeHtml(text.occurrence)}</th>
      <th>${escapeHtml(text.discount)}</th><th>${escapeHtml(text.note)}</th><th>${escapeHtml(text.cash)}</th>
      ${viewKey === "receivables" ? `<th>${escapeHtml(text.extra)}</th>` : ""}<th>${escapeHtml(text.other)}</th><th>${escapeHtml(text.applied)}</th><th>${escapeHtml(text.balance)}</th>
    </tr></thead><tbody>`;
  for (const row of filtered) {
    html += `<tr class="partner-report__partner-row" data-partner-code="${escapeHtml(row.code)}" tabindex="0" role="button"><td><span class="partner-code">${escapeHtml(row.code)}</span>${escapeHtml(row.name)}</td>`;
    for (const key of amountKeys) html += `<td>${amount(row[key])}</td>`;
    html += "</tr>";
  }
  html += `</tbody><tfoot><tr><th>${escapeHtml(text.total)}</th>`;
  for (const key of amountKeys) html += `<th>${amount(totals[key])}</th>`;
  html += `</tr></tfoot></table></div><p class="partner-report__select-hint">${escapeHtml(text.selectHint)}</p>
    <section id="partnerJournalDetail" class="partner-journal-detail" aria-live="polite"></section></section>`;
  wrapEl.innerHTML = html;
  const byCode = new Map(filtered.map(row => [row.code, row]));
  const activate = event => {
    const tr = event.currentTarget;
    const partner = byCode.get(tr.dataset.partnerCode);
    if (!partner) return;
    for (const other of wrapEl.querySelectorAll(".partner-report__partner-row")) other.classList.toggle("is-selected", other === tr);
    renderPartnerJournalDetail(viewKey, month, partner);
  };
  for (const tr of wrapEl.querySelectorAll(".partner-report__partner-row")) {
    tr.addEventListener("click", activate);
    tr.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(event); }
    });
  }
}

async function refreshPartnerReport(month) {
  if (dataMode === "local" && !localRowsAll) {
    setStatus(currentLang === "en" ? "Upload a ledger CSV first." : "総勘定元帳CSVをアップロードしてください。");
    wrapEl.innerHTML = "";
    return;
  }
  setStatus(`${tViewLabel(currentView)} (${month})...`);
  const previous = acctSel.value;
  const rows = await buildPartnerReport(currentView, month);
  const options = rows.map(row => ({ value: row.code, label: row.name }));
  buildOptions(acctSel, options, {
    includeAll: true,
    allLabel: currentLang === "en" ? "(All partners)" : "（全取引先）",
  });
  if (previous && options.some(option => option.value === previous)) acctSel.value = previous;
  renderPartnerReport(rows, currentView, month);
  setStatus("");
}

// -------- Filtering (account + search + local month filter) --------
/*
EN: Filtering
Filters the loaded rows by:
- account selector (value is code; label may be name),
- free-text search (substring match across all cells),
- month filter in local-upload mode when a Month column exists.
JA: フィルタ
- 科目選択（値はコード、表示は名称でも可）
- 全セルを対象に部分一致検索
- ローカルCSV時のみ Month 列があれば月フィルタ
*/
function filterRows(rows, { accountValue = "", searchText = "", monthValue = "" } = {}) {
  if (!rows || rows.length === 0) return rows;

  const header = rows[0];
  const acctIdx = getAccountColumnIndexForView(header, currentView);
  const monthIdx = detectColumnIndex(header, MONTH_COL_CANDIDATES);
  const s = String(searchText || "").trim().toLowerCase();

  if (!accountValue && !s && !(dataMode === "local" && monthValue && monthIdx >= 0)) return rows;

  const out = [header];

  for (let i = 1; i < rows.length; i++) {
    const r = rows[i];

    // local month filter (only when column exists)
    if (dataMode === "local" && monthValue && monthIdx >= 0) {
      const mv = String(r?.[monthIdx] ?? "").trim();
      if (mv !== monthValue) continue;
    }

    if (accountValue && acctIdx >= 0) {
      const v = String(r?.[acctIdx] ?? "").trim();
      if (v !== accountValue) continue;
    }

    if (s) {
      let hit = false;
      for (let j = 0; j < header.length; j++) {
        const cell = String(r?.[j] ?? "").toLowerCase();
        if (cell.includes(s)) { hit = true; break; }
      }
      if (!hit) continue;
    }

    out.push(r);
  }
  return out;
}

function applyI18nTexts() {
  if (monthLabelTextEl) monthLabelTextEl.textContent = currentLang === "en" ? "Month" : "対象月";
  if (searchLabelTextEl) searchLabelTextEl.textContent = currentLang === "en" ? "Search" : "検索";
  const searchInput = document.getElementById("searchInput");
  if (searchInput) {
    searchInput.placeholder =
      SEARCH_PLACEHOLDER_I18N[currentLang] || SEARCH_PLACEHOLDER_I18N.en;
  }
  if (companyHeaderEl) {
    const company = INDEX?.company;
    if (company?.name) {
      const locality = [company.prefecture, company.address].filter(Boolean).join("");
      const address = [company.postal_code ? `〒${company.postal_code}` : "", locality, company.building]
        .filter(Boolean)
        .join(" ");
      companyHeaderEl.innerHTML = `<span class="company-header__name">${escapeHtml(company.name)}</span><span class="company-header__address">${escapeHtml(address)}</span>`;
      companyHeaderEl.hidden = false;
    } else {
      companyHeaderEl.innerHTML = "";
      companyHeaderEl.hidden = true;
    }
  }
}

// -------- App state --------
let INDEX = null;
let currentView = "ledger";
let lastLoadedUrl = "";
let lastLoadedRows = null;
let initialAccountFromUrl = "";
const DEFAULT_LEDGER_ACCOUNT = "10A100020";

// Build CSV URL using index.json (dataset + language + month)
function joinUrlPath(...parts) {
  return parts
    .filter(p => p !== null && p !== undefined && String(p).trim() !== "")
    .map(p => String(p).replace(/^\/+|\/+$/g, "")) // trim only slashes
    .join("/");
}

function resolveCsvUrl(viewKey, month) {
  const v = INDEX?.views?.[viewKey];
  if (!v) throw new Error(`Unknown view: ${viewKey}`);
  if (!DATA_ROOT) throw new Error("DATA_ROOT is not initialised.");

  const available = Array.isArray(v.available) ? v.available : [];
  const hasAllOnly = (available.length === 1 && available[0] === "ALL");
  const lang = currentLang;

  // Apply {lang}/{month} placeholders safely
  const applyTpl = (tpl) => String(tpl || "")
    .replace(/\{lang\}/g, lang)
    .replace(/\{month\}/g, month || "");

  const pickTpl = (hasAllOnly || !month)
    ? (v.fallback || v.path)
    : (available.includes(month) ? (v.path || v.fallback) : (v.fallback || v.path));

  const rel = applyTpl(pickTpl);

  // Most datasets are stored under DATA_ROOT/{lang}/... and index.json paths are view-relative.
  // If the path already begins with "{lang}/", do not double-prefix.
  const needsLangPrefix = !(rel.startsWith(`${lang}/`) || rel.startsWith(`./${lang}/`) || rel.startsWith(`../${lang}/`));
  const rel2 = needsLangPrefix ? `${lang}/${rel}` : rel;

  return joinUrlPath(DATA_ROOT, rel2);
}


function updateUrlQuery(params) {
  const url = new URL(location.href);
  for (const [k, v] of Object.entries(params)) {
    if (v === null || v === undefined || v === "") url.searchParams.delete(k);
    else url.searchParams.set(k, v);
  }
  history.replaceState(null, "", url.toString());
}

// -------- UI init --------
function initNav() {
  navEl.innerHTML = "";
  const viewKeys = Object.keys(INDEX.views || {});
  for (const key of viewKeys) {
    const btn = document.createElement("button");
    btn.textContent = tViewLabel(key);
    btn.dataset.key = key;
    btn.addEventListener("click", async () => {
      if (key === "ledger" && currentView !== "ledger") acctSel.value = "";
      currentView = key;
      await refresh({ skipReloadCsv: false }); // view change => reload (server)
    });
    navEl.appendChild(btn);
  }
}

function setActiveButton() {
  for (const btn of navEl.querySelectorAll("button")) {
    btn.classList.toggle("active", btn.dataset.key === currentView);
  }
}

function updateViewControls() {
  const partnerView = isPartnerReportView();
  const allAccountsOnly = currentView === "trial_balance";
  if (allAccountsOnly) acctSel.value = "";
  if (accountLabelEl) {
    accountLabelEl.hidden = allAccountsOnly;
    accountLabelEl.firstChild.textContent = partnerView
      ? (currentLang === "en" ? "Partner " : "取引先 ")
      : (currentLang === "en" ? "Account " : "科目 ");
  }
  if (toggleCodeColsBtn) toggleCodeColsBtn.hidden = partnerView;
  if (columnToggleGroupEl) columnToggleGroupEl.hidden = partnerView;
}

function initMonthSelect(months) {
  buildOptions(monthSel, months || [], { includeAll: false });

  monthSel.addEventListener("change", async () => {
    await refresh({ skipReloadCsv: false });
  });
}

function initAccountSelect() {
  buildOptions(acctSel, [], { includeAll: true, allLabel: currentLang === "en" ? "(All accounts)" : "（全科目）" });

  acctSel.addEventListener("change", async () => {
    await refresh({ skipReloadCsv: true });
  });
}

function initLangSelect() {
  if (!langSel) return;
  langSel.value = currentLang;

  langSel.addEventListener("change", async () => {
    currentLang = langSel.value || LANG_DEFAULT;
    localStorage.setItem("ledger_lang", currentLang);

    initNav();
    setActiveButton();
    applyI18nTexts();

    updateColumnToggleButtons();
    updateViewControls();

    // server mode: must reload a different file path
    // local mode: can keep rows, just re-render headers/format
    await refresh({ skipReloadCsv: (dataMode === "local") });
  });
}

function initSearch() {
  let timer = null;
  searchInput.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => refresh({ skipReloadCsv: true }), 250);
  });
}

function initUpload() {
  if (!fileInput) return;

  fileInput.addEventListener("change", async () => {
    const f = fileInput.files?.[0];
    if (!f) return;

    const text = await f.text();
    const rows = parseCSV(text);

    dataMode = "local";
    localRowsAll = rows;
    lastLoadedRows = rows;
    lastLoadedUrl = "(local upload)";

    // If uploaded data has Month column, derive month options from it
    const header = rows[0] || [];
    const mIdx = detectColumnIndex(header, MONTH_COL_CANDIDATES);
    if (mIdx >= 0) {
      const months = extractUniqueColumnValues(rows, mIdx);
      initMonthSelect(months);
      if (months.length) monthSel.value = months[0];
    } else {
      // keep current server months if no Month column
      initMonthSelect(INDEX?.months || []);
    }

    // Account selector: show account *name* (label) while keeping code as the value
    const acctIdx = getAccountColumnIndexForView(header, currentView);
    const acctNameIdx = detectAccountNameIndex(header, acctIdx);
    if (acctIdx >= 0) {
      let options;
      if (acctNameIdx >= 0) {
        options = extractAccountOptions(rows, acctIdx, acctNameIdx);

        // Disambiguate duplicated names by appending the code
        const cnt = new Map();
        for (const o of options) {
          const k = String(o.label ?? "");
          cnt.set(k, (cnt.get(k) || 0) + 1);
        }
        for (const o of options) {
          const k = String(o.label ?? "");
          if (k && (cnt.get(k) || 0) > 1) o.label = `${k} (${o.value})`;
        }
      } else {
        options = extractUniqueColumnValues(rows, acctIdx).map(v => ({ value: v, label: v }));
      }
      buildOptions(acctSel, options, { includeAll: true, allLabel: currentLang === "en" ? "(All accounts)" : "（全科目）" });
    } else {
      buildOptions(acctSel, [], { includeAll: true, allLabel: currentLang === "en" ? "(No account)" : "（科目なし）" });
      acctSel.value = "";
    }

    setStatus(`Loaded local CSV: ${f.name}`);
    await refresh({ skipReloadCsv: true });
  });

  const btn = document.getElementById("useServerBtn");
  if (btn) {
    btn.addEventListener("click", async () => {
      dataMode = "server";
      localRowsAll = null;
      lastLoadedRows = null;
      lastLoadedUrl = "";
      fileInput.value = "";

      initMonthSelect(INDEX?.months || []);
      initAccountSelect();

      await refresh({ skipReloadCsv: false });
    });
  }

}

// -------- Main refresh logic --------
async function refresh(opts = {}) {
  const { skipReloadCsv = false } = opts;

  setActiveButton();
  updateViewControls();

  const month = monthSel.value || (INDEX?.months?.[0] ?? "");
  const q = String(searchInput.value || "").trim();
  const acct = acctSel.value || "";

  updateUrlQuery({ view: currentView, month, account: acct, q, mode: dataMode, lang: currentLang, dataset: (DATASET !== DATASET_DEFAULT ? DATASET : null) });

  try {
    wrapEl.innerHTML = "";

    if (isPartnerReportView()) {
      await refreshPartnerReport(month);
      updateUrlQuery({
        view: currentView,
        month,
        account: acctSel.value || "",
        q,
        mode: dataMode,
        lang: currentLang,
        dataset: (DATASET !== DATASET_DEFAULT ? DATASET : null),
      });
      return;
    }

    // ---- Load rows ----
    if (dataMode === "server") {
      const url = resolveCsvUrl(currentView, month);
      setStatus(`Loading ${tViewLabel(currentView)} (${month}) [${currentLang}]...`);

      if (!skipReloadCsv || url !== lastLoadedUrl || !lastLoadedRows) {
        lastLoadedRows = await fetchCSV(url);
        lastLoadedUrl = url;

        // account options (value=code, label=name when available)
        const header = lastLoadedRows[0] || [];
        const acctIdx = getAccountColumnIndexForView(header, currentView);
        const acctNameIdx = detectAccountNameIndex(header, acctIdx);
        const prevVal = acctSel.value;

        if (acctIdx >= 0) {
          let options;

          if (acctNameIdx >= 0) {
            options = extractAccountOptions(lastLoadedRows, acctIdx, acctNameIdx);

            // If account names are duplicated, disambiguate by appending the code.
            const cnt = new Map();
            for (const o of options) {
              const k = String(o.label ?? "");
              cnt.set(k, (cnt.get(k) || 0) + 1);
            }
            for (const o of options) {
              const k = String(o.label ?? "");
              if (k && (cnt.get(k) || 0) > 1) o.label = `${k} (${o.value})`;
            }
          } else {
            options = extractUniqueColumnValues(lastLoadedRows, acctIdx).map(v => ({ value: v, label: v }));
          }

          buildOptions(acctSel, options, { includeAll: true, allLabel: currentLang === "en" ? "(All accounts)" : "（全科目）" });

          const preferredValue = currentView === "trial_balance"
            ? ""
            : (initialAccountFromUrl || prevVal || (currentView === "ledger" ? DEFAULT_LEDGER_ACCOUNT : ""));
          if (preferredValue && Array.from(acctSel.options).some(o => o.value === preferredValue)) acctSel.value = preferredValue;
          if (currentView === "trial_balance") acctSel.value = "";
          initialAccountFromUrl = "";
          updateUrlQuery({ account: acctSel.value || null });
        } else {
          buildOptions(acctSel, [], { includeAll: true, allLabel: currentLang === "en" ? "(No account)" : "（科目なし）" });
          acctSel.value = "";
        }
      }

    } else {
      // local upload mode
      if (!localRowsAll) {
        setStatus("No local CSV loaded.");
        wrapEl.innerHTML = "<div style='padding:12px'>Upload a CSV first.</div>";
        return;
      }
      lastLoadedRows = localRowsAll;
      setStatus(`Local CSV mode [${currentLang}]...`);
    }

    // ---- Filter + render ----
    const filtered = filterRows(lastLoadedRows, {
      accountValue: acctSel.value || "",
      searchText: q,
      monthValue: month
    });

    renderTable(filtered);

    setStatus("");

  } catch (e) {
    setStatus(String(e?.message || e));
  }
}

// -------- Bootstrap --------
/*
EN: Bootstrap
Loads data/index.json, initialises UI controls (nav/month/account/lang/search/upload),
then calls refresh() to load/render the first view.
JA: 初期化
data/index.json を読み込み、ナビ・月・科目・言語・検索・アップロード等のUIを初期化後、
refresh() で初期表示を行います。
*/
async function main() {
  try {
    await initDatasetAndPaths();
  } catch (error) {
    const message = String(error?.message || error);
    console.error(message);
    setStatus(message);
    wrapEl.innerHTML = `<div class="dataset-error"><strong>Dataset loading failed.</strong><pre>${escapeHtml(message)}</pre></div>`;
    return;
  }
  setStatus(`Loading index.json... (${INDEX_URL})`);
  INDEX = INDEX_BOOTSTRAP;
  // Ensure "tidy" view exists for Structured CSV monthly files.
  // Even if index.json does not define it, we add it here so the "Structured CSV" button is always shown.
  if (!INDEX.views) INDEX.views = {};
  if (!INDEX.views.tidy) {
    INDEX.views.tidy = {
      by: "month",
      path: "tidy/{month}.csv",
      fallback: "tidy/{month}.csv",
      available: Array.isArray(INDEX.months) ? INDEX.months : []
    };
  }
  INDEX.views.receivables = { virtual: true, source: "ledger" };
  INDEX.views.payables = { virtual: true, source: "ledger" };

  // restore state from URL if any
  const url = new URL(location.href);
  const qView = url.searchParams.get("view");
  const qLang = url.searchParams.get("lang");
  const qMonth = url.searchParams.get("month");
  initialAccountFromUrl = url.searchParams.get("account") || "";

  if (qLang && (qLang === "ja" || qLang === "en")) {
    currentLang = qLang;
    localStorage.setItem("ledger_lang", currentLang);
  }
  if (qView && INDEX.views && INDEX.views[qView]) currentView = qView;
  // init UI
  initNav();
  initMonthSelect(INDEX.months || []);
  if (qMonth && (INDEX.months || []).includes(qMonth)) monthSel.value = qMonth;
  initAccountSelect();
  initLangSelect();
  initSearch();
  initColumnToggleButtons();

  // set lang select
  if (langSel) langSel.value = currentLang;
  applyI18nTexts();

  await refresh({ skipReloadCsv: false });
}

main();
