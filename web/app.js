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
    documents: "業務文書",
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
    documents: "Business Documents",
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
const modeSwitchEl = document.getElementById("modeSwitch");
const documentTypeNavEl = document.getElementById("documentTypeNav");
const modeNavSeparatorEl = document.getElementById("modeNavSeparator");
const aboutSeparatorEl = document.getElementById("aboutSeparator");
const aboutLinkEl = document.getElementById("aboutLink");
const statusEl = document.getElementById("status");
const wrapEl = document.getElementById("tableWrap");
const monthSel = document.getElementById("monthSelect");
const asOfSel = document.getElementById("asOfSelect");
const acctSel = document.getElementById("accountSelect");
const searchInput = document.getElementById("searchInput");
const langSel = document.getElementById("langSelect");
const fileInput = document.getElementById("fileInput");
const useServerBtn = document.getElementById("useServerBtn");
const accountLabelEl = document.getElementById("accountLabel");
const searchLabelEl = document.getElementById("searchLabel");
const columnToggleGroupEl = document.getElementById("columnToggleGroup");
const companyHeaderEl = document.getElementById("companyHeader");
const monthLabelTextEl = document.getElementById("monthLabelText");
const asOfLabelEl = document.getElementById("asOfLabel");
const asOfLabelTextEl = document.getElementById("asOfLabelText");
const searchLabelTextEl = document.getElementById("searchLabelText");
const fontSizeControlEl = document.getElementById("fontSizeControl");
const fontSizeLabelEl = document.getElementById("fontSizeLabel");

const FONT_SIZE_OPTIONS = new Set(["small", "medium", "large"]);
let currentFontSize = localStorage.getItem("ledger_font_size") || "medium";
if (!FONT_SIZE_OPTIONS.has(currentFontSize)) currentFontSize = "medium";

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

function isDocumentView(viewKey = currentView) {
  return viewKey === "documents";
}

// -------- Helpers --------
function setStatus(msg) { statusEl.textContent = msg; }

function applyFontSize() {
  document.documentElement.dataset.fontSize = currentFontSize;
  if (!fontSizeControlEl) return;
  const labels = currentLang === "en"
    ? { label: "Text", aria: "Display text size", small: "Small", medium: "Medium", large: "Large" }
    : { label: "文字", aria: "表示文字サイズ", small: "小", medium: "中", large: "大" };
  fontSizeControlEl.setAttribute("aria-label", labels.aria);
  if (fontSizeLabelEl) fontSizeLabelEl.textContent = labels.label;
  for (const button of fontSizeControlEl.querySelectorAll("button[data-font-size]")) {
    const size = button.dataset.fontSize;
    button.textContent = labels[size] || size;
    button.setAttribute("aria-pressed", String(size === currentFontSize));
  }
}

function initFontSizeControl() {
  if (!fontSizeControlEl) return;
  for (const button of fontSizeControlEl.querySelectorAll("button[data-font-size]")) {
    button.addEventListener("click", () => {
      const size = button.dataset.fontSize;
      if (!FONT_SIZE_OPTIONS.has(size)) return;
      currentFontSize = size;
      localStorage.setItem("ledger_font_size", currentFontSize);
      applyFontSize();
    });
  }
  applyFontSize();
}

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

function documentDetailText() {
  return currentLang === "en" ? {
    title: "Related document",
    close: "Close",
    loading: "Loading document...",
    none: "No related document is registered for this journal line.",
    type: "Document type",
    number: "Document number",
    managementId: "Management ID",
    partner: "Trading partner",
    documentMonth: "Document month",
    selectedEntry: "Selected journal entry",
    description: "Description",
    debit: "Debit",
    credit: "Credit",
    invoice: "Invoice reference",
    settlement: "Settlement reference",
    transaction: "Transaction / line",
    amount: "Amount",
    noticeDate: "Notice date",
    account: "Bank / settlement account",
    scheduled: "Scheduled settlement",
    parties: "Document parties",
    issuer: "Issuer",
    recipient: "Recipient",
    department: "Department",
    person: "Person",
    application: "Cash application",
    recognitionBasis: "Recognition basis",
    invoiceBasis: "Invoice basis",
    original: "Original amount",
    applied: "Applied amount",
    open: "Open amount",
    applicationDate: "Application date",
    settlementDocument: "Settlement document",
    cash: "Cash / bank",
    note: "Note",
    fee: "Bank fee",
    discount: "Discount / return",
    offset: "Offset",
    adjustment: "Other adjustment",
    itemDetails: "Document line items",
    item: "Item",
    quantity: "Qty",
    unitPrice: "Unit price (gross)",
    net: "Net",
    taxCategory: "Tax category",
    tax: "Tax",
    gross: "Gross",
    taxSummary: "Totals by tax category",
    roundingNote: "Included tax is calculated from each tax-rate gross subtotal and rounded down to the nearest yen.",
    total: "Document total",
    referenceOnly: "Reference document (document line details are outside the published monthly data)",
    sourceNote: "The document is identified by trading partner, transaction and line IDs; invoice-to-settlement relationships are shown from cash application records. The description is supporting context only.",
  } : {
    title: "関連文書",
    close: "閉じる",
    loading: "文書を読み込んでいます…",
    none: "この仕訳明細に対応する関連文書は登録されていません。",
    type: "文書種別",
    number: "文書番号",
    managementId: "管理番号",
    partner: "取引先",
    documentMonth: "文書月",
    selectedEntry: "選択した仕訳",
    description: "摘要文",
    debit: "借方",
    credit: "貸方",
    invoice: "請求文書",
    settlement: "精算情報",
    transaction: "伝票ID／明細行ID",
    amount: "金額",
    noticeDate: "通知日",
    account: "入出金口座",
    scheduled: "精算予定月",
    parties: "文書当事者",
    issuer: "発行者",
    recipient: "受領者",
    department: "部署",
    person: "担当者",
    application: "消込確認",
    recognitionBasis: "認識基準",
    invoiceBasis: "請求書基準",
    original: "請求金額",
    applied: "消込額",
    open: "未消込額",
    applicationDate: "消込日",
    settlementDocument: "入出金・調整文書",
    cash: "現金・預金",
    note: "手形",
    fee: "振込料",
    discount: "値引・返品",
    offset: "相殺",
    adjustment: "その他調整",
    itemDetails: "文書明細",
    item: "品目",
    quantity: "数量",
    unitPrice: "税込単価",
    net: "税抜金額",
    taxCategory: "税区分",
    tax: "消費税額",
    gross: "税込金額",
    taxSummary: "税区分別合計",
    roundingNote: "内税額は税率別の税込合計から計算し、1円未満を切り捨てています。",
    total: "文書合計",
    referenceOnly: "参照文書（文書明細は公開用月次データの対象外）",
    sourceNote: "文書は取引先ID・伝票ID・明細行IDで特定し、請求と入出金の関係はCash Applicationから表示します。摘要文は照合の参考としてのみ使用します。",
  };
}

function csvValue(rows, row, name) {
  const index = (rows?.[0] || []).indexOf(name);
  return index >= 0 ? String(row?.[index] || "").trim() : "";
}

function settlementDocumentInfo(viewKey, item, partner, link, linkValue) {
  const row = item.row;
  const idx = item.idx;
  const receivables = viewKey === "receivables";
  const accountCode = String(row[receivables ? idx.debitAccount : idx.creditAccount] || "").trim();
  const accountNameIndex = receivables ? idx.debitSubName : idx.creditSubName;
  const accountName = accountNameIndex >= 0 ? String(row[accountNameIndex] || "").trim() : "";
  const cashSettlement = accountCode === "10A100020";
  const documentType = currentLang === "en"
    ? (receivables
      ? (cashSettlement ? "Bank receipt notice" : "Customer remittance advice")
      : (cashSettlement ? "Bank transfer receipt" : "Supplier payment notice"))
    : (receivables
      ? (cashSettlement ? "銀行入金通知" : "取引先入金通知")
      : (cashSettlement ? "銀行振込受付書" : "仕入先支払通知"));
  return {
    type: documentType,
    id: link ? linkValue(link, "settlement_document_id") : "",
    number: link ? linkValue(link, "settlement_document_number") : "",
    accountName,
  };
}

async function journalEntryDate(month, transactionId, lineId) {
  if (!month || !transactionId || !lineId) return "";
  const rows = await fetchOptionalCSV(resolveCsvUrl("journal", month));
  const idx = journalRowIndexes(rows);
  if (idx.transactionId < 0 || idx.lineId < 0 || idx.date < 0) return "";
  const matched = rows.slice(1).find(row =>
    String(row[idx.transactionId] || "").trim() === String(transactionId).trim() &&
    String(row[idx.lineId] || "").trim() === String(lineId).trim()
  );
  return matched ? String(matched[idx.date] || "").trim() : "";
}

async function relatedDocumentData(item, partner, viewKey) {
  const row = item.row;
  const idx = item.idx;
  const transactionId = String(row[idx.transactionId] || "").trim();
  const lineId = String(row[idx.lineId] || "").trim();
  const description = String(row[idx.description] || "").trim();
  const partnerType = viewKey === "receivables" ? "C" : "S";
  const linkRows = await fetchOptionalCSV(joinUrlPath(DATA_ROOT, currentLang, "source", "transaction_document_link.csv"));
  const linkHeader = linkRows[0] || [];
  const linkIndex = name => linkHeader.indexOf(name);
  const linkValue = (link, name) => {
    const index = linkIndex(name);
    return index >= 0 ? String(link[index] || "").trim() : "";
  };
  const links = linkRows.slice(1).filter(link =>
    linkValue(link, "partner_type") === partnerType &&
    linkValue(link, "partner_code") === String(partner.code) &&
    ((linkValue(link, "invoice_month") === item.month &&
      linkValue(link, "invoice_transaction_id") === transactionId &&
      linkValue(link, "invoice_line_id") === lineId) ||
     (linkValue(link, "settlement_month") === item.month &&
      linkValue(link, "settlement_transaction_id") === transactionId &&
      linkValue(link, "settlement_line_id") === lineId))
  );

  let tidyRows = [];
  let tidyRow = null;
  const tidyMonths = Array.isArray(INDEX?.views?.tidy?.available) ? INDEX.views.tidy.available : [];
  if (tidyMonths.includes(item.month)) {
    tidyRows = await fetchCSV(resolveCsvUrl("tidy", item.month));
    const tidyHeader = tidyRows[0] || [];
    const transactionIndex = tidyHeader.indexOf("JP07a");
    const lineIndex = tidyHeader.indexOf("JP08a");
    tidyRow = tidyRows.slice(1).find(candidate =>
      String(candidate[transactionIndex] || "").trim() === transactionId &&
      String(candidate[lineIndex] || "").trim() === lineId
    ) || null;
  }

  const documentId = tidyRow ? csvValue(tidyRows, tidyRow, "Document_ID") : "";
  const documentNumber = tidyRow ? csvValue(tidyRows, tidyRow, "Document_Number") : "";
  const documentType = tidyRow ? csvValue(tidyRows, tidyRow, "Document_Type") : "";
  const relatedDocumentId = tidyRow ? csvValue(tidyRows, tidyRow, "Related_Document_ID") : "";
  const relatedDocumentNumber = tidyRow ? csvValue(tidyRows, tidyRow, "Related_Document_Number") : "";
  const relatedDocumentType = tidyRow ? csvValue(tidyRows, tidyRow, "Related_Document_Type") : "";
  const link = links[0] || null;
  const invoiceId = link ? linkValue(link, "invoice_document_id") : "";
  const invoiceNumber = link ? linkValue(link, "invoice_document_number") : "";
  const isInvoice = Boolean(link &&
    linkValue(link, "invoice_month") === item.month &&
    linkValue(link, "invoice_transaction_id") === transactionId &&
    linkValue(link, "invoice_line_id") === lineId);
  const invoiceDate = isInvoice
    ? String(row[idx.date] || "").trim()
    : (link ? await journalEntryDate(
      linkValue(link, "invoice_month"),
      linkValue(link, "invoice_transaction_id"),
      linkValue(link, "invoice_line_id")
    ) : "");
  const settlementInfo = settlementDocumentInfo(viewKey, item, partner, link, linkValue);
  const resolvedDocumentId = isInvoice
    ? (documentId || invoiceId)
    : (documentId || settlementInfo.id);
  const candidateNumber = isInvoice
    ? (documentNumber || invoiceNumber)
    : (documentNumber || settlementInfo.number);
  const invoiceDocumentId = invoiceId || relatedDocumentId;
  const [detailRows, businessDocumentRows, documentPartyRows, openItemRows, settlementRows, applicationRows] = await Promise.all([
    fetchOptionalCSV(joinUrlPath(DATA_ROOT, currentLang, "source", "transaction_document_detail.csv")),
    fetchOptionalCSV(joinUrlPath(DATA_ROOT, currentLang, "source", "business_document.csv")),
    fetchOptionalCSV(joinUrlPath(DATA_ROOT, currentLang, "source", "business_document_party.csv")),
    fetchOptionalCSV(joinUrlPath(DATA_ROOT, currentLang, "source", "ar_ap_open_item.csv")),
    fetchOptionalCSV(joinUrlPath(DATA_ROOT, currentLang, "source", "cash_settlement.csv")),
    fetchOptionalCSV(joinUrlPath(DATA_ROOT, currentLang, "source", "cash_application.csv")),
  ]);
  const documentDetails = isInvoice && resolvedDocumentId
    ? detailRows.slice(1).filter(detail => csvValue(detailRows, detail, "Document_ID") === resolvedDocumentId)
    : [];
  const businessDocument = businessDocumentRows.slice(1).find(candidate =>
    csvValue(businessDocumentRows, candidate, "Document_ID") === resolvedDocumentId
  ) || null;
  const resolvedNumber = businessDocument
    ? csvValue(businessDocumentRows, businessDocument, "Document_Number")
    : candidateNumber;
  const resolvedType = businessDocument
    ? csvValue(businessDocumentRows, businessDocument, "Document_Type_Name")
    : (isInvoice ? (documentType || relatedDocumentType) : settlementInfo.type);
  let openItem = openItemRows.slice(1).find(candidate =>
    csvValue(openItemRows, candidate, "Invoice_Document_ID") === (isInvoice ? resolvedDocumentId : invoiceDocumentId)
  ) || null;
  let settlement = settlementRows.slice(1).find(candidate =>
    csvValue(settlementRows, candidate, "Settlement_Document_ID") === (isInvoice ? settlementInfo.id : resolvedDocumentId)
  ) || null;
  let application = null;
  if (openItem) {
    const openItemId = csvValue(openItemRows, openItem, "Open_Item_ID");
    application = applicationRows.slice(1).find(candidate =>
      csvValue(applicationRows, candidate, "Open_Item_ID") === openItemId
    ) || null;
  }
  if (!application && settlement) {
    const settlementId = csvValue(settlementRows, settlement, "Settlement_ID");
    application = applicationRows.slice(1).find(candidate =>
      csvValue(applicationRows, candidate, "Settlement_ID") === settlementId
    ) || null;
  }
  if (!openItem && application) {
    const openItemId = csvValue(applicationRows, application, "Open_Item_ID");
    openItem = openItemRows.slice(1).find(candidate =>
      csvValue(openItemRows, candidate, "Open_Item_ID") === openItemId
    ) || null;
  }
  if (!settlement && application) {
    const settlementId = csvValue(applicationRows, application, "Settlement_ID");
    settlement = settlementRows.slice(1).find(candidate =>
      csvValue(settlementRows, candidate, "Settlement_ID") === settlementId
    ) || null;
  }
  const businessDocumentId = businessDocument ? csvValue(businessDocumentRows, businessDocument, "Document_ID") : "";
  const documentParties = businessDocumentId
    ? documentPartyRows.slice(1).filter(candidate => csvValue(documentPartyRows, candidate, "Document_ID") === businessDocumentId)
    : [];
  const debitAccount = [row[idx.debitAccount], idx.debitName >= 0 ? row[idx.debitName] : ""].filter(Boolean).join(" ");
  const creditAccount = [row[idx.creditAccount], idx.creditName >= 0 ? row[idx.creditName] : ""].filter(Boolean).join(" ");
  return {
    transactionId, lineId, description, partner,
    date: String(row[idx.date] || "").trim(),
    debitAccount, debitAmount: row[idx.debitAmount],
    creditAccount, creditAmount: row[idx.creditAmount],
    documentType: resolvedType,
    documentId: resolvedDocumentId,
    documentNumber: resolvedNumber,
    documentMonth: isInvoice ? (link ? linkValue(link, "invoice_month") : item.month) : (link ? linkValue(link, "settlement_month") : item.month),
    invoiceDate,
    relatedDocumentId: isInvoice ? settlementInfo.id : invoiceDocumentId,
    relatedDocumentNumber: isInvoice
      ? settlementInfo.number
      : (openItem ? csvValue(openItemRows, openItem, "Invoice_Document_Number") : invoiceNumber),
    settlementAccount: settlementInfo.accountName,
    scheduledMonth: tidyRow ? csvValue(tidyRows, tidyRow, "Settlement_Scheduled_Month") : "",
    link, linkValue, isInvoice,
    detailRows, documentDetails,
    businessDocumentRows, businessDocument,
    documentPartyRows, documentParties,
    openItemRows, openItem,
    settlementRows, settlement,
    applicationRows, application,
    referenceOnly: Boolean(link && !tidyMonths.includes(isInvoice ? linkValue(link, "invoice_month") : linkValue(link, "settlement_month"))),
  };
}

async function renderRelatedDocumentDetail(target, item, partner, viewKey) {
  const text = documentDetailText();
  target.hidden = false;
  target.innerHTML = `<div class="partner-document__header"><h3>${escapeHtml(text.title)}</h3><button type="button" class="partner-document__close" aria-label="${escapeHtml(text.close)}">×</button></div><p>${escapeHtml(text.loading)}</p>`;
  const close = () => {
    target.hidden = true;
    target.closest(".partner-detail-layout")?.classList.remove("has-document");
    target.closest(".partner-journal-detail")?.querySelectorAll(".partner-journal-table tbody tr.is-selected, .partner-journal-table tbody tr.is-related-entry").forEach(row => {
      row.classList.remove("is-selected", "is-related-entry");
    });
  };
  target.querySelector(".partner-document__close")?.addEventListener("click", close);
  try {
    const data = await relatedDocumentData(item, partner, viewKey);
    if (!data.documentId && !data.documentNumber) {
      target.innerHTML = `<div class="partner-document__header"><h3>${escapeHtml(text.title)}</h3><button type="button" class="partner-document__close" aria-label="${escapeHtml(text.close)}">×</button></div><p class="partner-document__empty">${escapeHtml(text.none)}</p><p class="partner-document__source-note">${escapeHtml(text.sourceNote)}</p>`;
      target.querySelector(".partner-document__close")?.addEventListener("click", close);
      return null;
    }
    const linkValue = data.linkValue;
    const link = data.link;
    const invoiceMonth = link ? linkValue(link, "invoice_month") : "";
    const settlementMonth = link ? linkValue(link, "settlement_month") : data.scheduledMonth;
    const invoiceTransaction = link ? `${linkValue(link, "invoice_transaction_id")} / ${linkValue(link, "invoice_line_id")}` : "";
    const settlementTransaction = link ? `${linkValue(link, "settlement_transaction_id")} / ${linkValue(link, "settlement_line_id")}` : "";
    const relationAmount = link ? linkValue(link, "amount") : "";
    const type = data.documentType || (data.isInvoice
      ? (viewKey === "receivables"
        ? (currentLang === "en" ? "Sales invoice" : "売上請求書")
        : (currentLang === "en" ? "Purchase invoice" : "仕入請求書"))
      : (currentLang === "en" ? "Settlement document" : "入出金・調整文書"));
    const documentReference = (number, id) => {
      const shownNumber = String(number || "").trim() || (currentLang === "en" ? "\u2014" : "\uff0d");
      if (!id) return shownNumber;
      return currentLang === "en"
        ? `${shownNumber} (${text.managementId}: ${id})`
        : `${shownNumber}\uff08${text.managementId}\uff1a${id}\uff09`;
    };
    const detailAmount = (detail, name) => formatNumberLike(csvValue(data.detailRows, detail, name));
    const taxGroups = new Map();
    for (const detail of data.documentDetails) {
      const category = csvValue(data.detailRows, detail, "Tax_Category");
      const rate = numberValue(csvValue(data.detailRows, detail, "Tax_Rate")) ||
        numberValue((category.match(/(\d+)\s*%/) || [])[1]);
      const key = `${category}\u0000${rate}`;
      const group = taxGroups.get(key) || { category, rate, gross: 0 };
      group.gross += numberValue(csvValue(data.detailRows, detail, "Gross_Amount"));
      taxGroups.set(key, group);
    }
    const detailRowsHtml = data.documentDetails.map(detail => `<tr>
      <td>${escapeHtml(csvValue(data.detailRows, detail, "Line_Number"))}</td>
      <td>${escapeHtml(csvValue(data.detailRows, detail, "Item_Description"))}</td>
      <td>${escapeHtml(csvValue(data.detailRows, detail, "Quantity"))} ${escapeHtml(csvValue(data.detailRows, detail, "Unit"))}</td>
      <td>${detailAmount(detail, "Unit_Price")}</td>
      <td>${escapeHtml(csvValue(data.detailRows, detail, "Tax_Category"))}</td>
      <td>${detailAmount(detail, "Gross_Amount")}</td>
    </tr>`).join("");
    const taxSummaryHtml = [...taxGroups.values()].map(group => {
      const tax = group.rate > 0 ? Math.floor(group.gross * group.rate / (100 + group.rate)) : 0;
      const net = group.gross - tax;
      return `<tr><th>${escapeHtml(group.category)}</th><td>${formatNumberLike(net)}</td><td>${formatNumberLike(tax)}</td><td>${formatNumberLike(group.gross)}</td></tr>`;
    }).join("");
    const detailTotal = data.documentDetails.reduce((sum, detail) => sum + numberValue(csvValue(data.detailRows, detail, "Gross_Amount")), 0);
    const formalValue = (rows, record, name) => record ? csvValue(rows, record, name) : "";
    const partyByRole = role => data.documentParties.find(candidate =>
      csvValue(data.documentPartyRows, candidate, "Role_Code") === role
    ) || null;
    const partyCard = (role, labelText) => {
      const party = partyByRole(role);
      if (!party) return "";
      const partyName = formalValue(data.documentPartyRows, party, "Party_Name");
      const department = formalValue(data.documentPartyRows, party, "Department_Name");
      const person = formalValue(data.documentPartyRows, party, "Person_Name");
      const position = formalValue(data.documentPartyRows, party, "Position_Name");
      return `<div><dt>${escapeHtml(labelText)}</dt><dd><strong>${escapeHtml(partyName)}</strong><span>${escapeHtml(text.department)}: ${escapeHtml(department || "-")}</span><span>${escapeHtml(text.person)}: ${escapeHtml([person, position].filter(Boolean).join(" / ") || "-")}</span></dd></div>`;
    };
    const documentPartiesHtml = partyCard("ISSUER", text.issuer) + partyCard("RECIPIENT", text.recipient);
    const openItemValue = name => formalValue(data.openItemRows, data.openItem, name);
    const settlementValue = name => formalValue(data.settlementRows, data.settlement, name);
    const applicationValue = name => formalValue(data.applicationRows, data.application, name);
    const applicationComponents = [
      [text.cash, "Cash_Amount"], [text.note, "Note_Amount"], [text.fee, "Fee_Amount"],
      [text.discount, "Discount_Amount"], [text.offset, "Offset_Amount"], [text.adjustment, "Other_Adjustment_Amount"],
    ].filter(([, name]) => numberValue(applicationValue(name)) !== 0);
    const applicationHtml = data.openItem && data.application ? `<section class="partner-document__application"><h4>${escapeHtml(text.application)}</h4>
      <dl>
        <div><dt>${escapeHtml(text.invoice)}</dt><dd>${escapeHtml(documentReference(openItemValue("Invoice_Document_Number"), openItemValue("Invoice_Document_ID")))}</dd></div>
        <div><dt>${escapeHtml(text.recognitionBasis)}</dt><dd>${escapeHtml(openItemValue("Recognition_Basis") === "INVOICE_BASIS" ? text.invoiceBasis : openItemValue("Recognition_Basis"))}</dd></div>
        <div><dt>${escapeHtml(text.original)}</dt><dd class="amount">${formatNumberLike(openItemValue("Original_Amount"))}</dd></div>
        <div><dt>${escapeHtml(text.applied)}</dt><dd class="amount">${formatNumberLike(applicationValue("Applied_Amount"))}</dd></div>
        <div><dt>${escapeHtml(text.open)}</dt><dd class="amount">${formatNumberLike(openItemValue("Open_Amount"))}</dd></div>
        <div><dt>${escapeHtml(text.applicationDate)}</dt><dd>${escapeHtml(applicationValue("Application_Date"))}</dd></div>
        <div><dt>${escapeHtml(text.settlementDocument)}</dt><dd>${escapeHtml(documentReference(settlementValue("Settlement_Document_Number") || data.relatedDocumentNumber, settlementValue("Settlement_Document_ID") || data.relatedDocumentId))}</dd></div>
      </dl>
      ${applicationComponents.length ? `<table><thead><tr><th>${escapeHtml(currentLang === "en" ? "Component" : "消込区分")}</th><th>${escapeHtml(text.amount)}</th></tr></thead><tbody>${applicationComponents.map(([labelText, name]) => `<tr><th>${escapeHtml(labelText)}</th><td>${formatNumberLike(applicationValue(name))}</td></tr>`).join("")}</tbody></table>` : ""}
    </section>` : "";
    target.innerHTML = `<div class="partner-document__header"><h3>${escapeHtml(text.title)}</h3><button type="button" class="partner-document__close" aria-label="${escapeHtml(text.close)}">×</button></div>
      <article class="partner-document__sheet">
        <div class="partner-document__title"><span>${escapeHtml(type)}</span><strong>${escapeHtml(documentReference(data.documentNumber, data.documentId))}</strong></div>
        ${data.referenceOnly ? `<p class="partner-document__reference">${escapeHtml(text.referenceOnly)}</p>` : ""}
        <dl class="partner-document__meta">
          <div><dt>${escapeHtml(text.partner)}</dt><dd>${escapeHtml(data.partner.name)}</dd></div>
          <div><dt>${escapeHtml(data.isInvoice ? (currentLang === "en" ? "Document date" : "\u6587\u66f8\u65e5\u4ed8") : text.documentMonth)}</dt><dd>${escapeHtml(data.isInvoice ? (data.invoiceDate || data.date || "-") : data.documentMonth)}</dd></div>
          <div><dt>${escapeHtml(text.number)}</dt><dd>${escapeHtml(documentReference(data.documentNumber, data.documentId))}</dd></div>
          ${!data.isInvoice ? `<div><dt>${escapeHtml(text.noticeDate)}</dt><dd>${escapeHtml(data.date || "-")}</dd></div>` : ""}
          ${!data.isInvoice && data.settlementAccount ? `<div><dt>${escapeHtml(text.account)}</dt><dd>${escapeHtml(data.settlementAccount)}</dd></div>` : ""}
          ${!data.isInvoice ? `<div><dt>${escapeHtml(text.amount)}</dt><dd>${formatNumberLike(relationAmount || data.debitAmount || data.creditAmount)}</dd></div>` : ""}
          ${data.scheduledMonth ? `<div><dt>${escapeHtml(text.scheduled)}</dt><dd>${escapeHtml(data.scheduledMonth)}</dd></div>` : ""}
        </dl>
        ${documentPartiesHtml ? `<section class="partner-document__parties"><h4>${escapeHtml(text.parties)}</h4><dl>${documentPartiesHtml}</dl></section>` : ""}
        ${data.documentDetails.length ? `<section class="partner-document__items"><h4>${escapeHtml(text.itemDetails)}</h4>
          <div class="partner-document__items-wrap"><table><thead><tr><th>#</th><th>${escapeHtml(text.item)}</th><th>${escapeHtml(text.quantity)}</th><th>${escapeHtml(text.unitPrice)}</th><th>${escapeHtml(text.taxCategory)}</th><th>${escapeHtml(text.gross)}</th></tr></thead><tbody>${detailRowsHtml}</tbody></table></div>
          <div class="partner-document__tax-summary"><h4>${escapeHtml(text.taxSummary)}</h4><table><thead><tr><th>${escapeHtml(text.taxCategory)}</th><th>${escapeHtml(text.net)}</th><th>${escapeHtml(text.tax)}</th><th>${escapeHtml(text.gross)}</th></tr></thead><tbody>${taxSummaryHtml}<tr class="total"><th>${escapeHtml(text.total)}</th><td colspan="2"></td><td>${formatNumberLike(detailTotal)}</td></tr></tbody></table><p class="partner-document__rounding-note">${escapeHtml(text.roundingNote)}</p></div>
        </section>` : ""}
        <section class="partner-document__entry"><h4>${escapeHtml(text.selectedEntry)}</h4>
          <p class="partner-document__description"><span>${escapeHtml(text.description)}</span>${escapeHtml(data.description || "-")}</p>
          <table><tbody>
            <tr><th>${escapeHtml(text.transaction)}</th><td>${escapeHtml(`${data.transactionId} / ${data.lineId}`)}</td></tr>
            <tr><th>${escapeHtml(text.debit)}</th><td>${escapeHtml(data.debitAccount)}<strong>${formatNumberLike(data.debitAmount)}</strong></td></tr>
            <tr><th>${escapeHtml(text.credit)}</th><td>${escapeHtml(data.creditAccount)}<strong>${formatNumberLike(data.creditAmount)}</strong></td></tr>
          </tbody></table>
        </section>
        ${applicationHtml}
        ${link ? `<section class="partner-document__relation"><h4>${escapeHtml(data.isInvoice ? text.settlement : text.invoice)}</h4>
          <dl>
            <div><dt>${escapeHtml(text.number)}</dt><dd>${escapeHtml(documentReference(data.relatedDocumentNumber, data.relatedDocumentId))}</dd></div>
            <div><dt>${escapeHtml(data.isInvoice ? text.documentMonth : (currentLang === "en" ? "Document date" : "\u6587\u66f8\u65e5\u4ed8"))}</dt><dd>${escapeHtml(data.isInvoice ? settlementMonth : (data.invoiceDate || "-"))}</dd></div>
            <div><dt>${escapeHtml(text.transaction)}</dt><dd>${escapeHtml(data.isInvoice ? settlementTransaction : invoiceTransaction)}</dd></div>
            <div><dt>${escapeHtml(text.amount)}</dt><dd class="amount">${formatNumberLike(relationAmount)}</dd></div>
          </dl>
        </section>` : ""}
        <p class="partner-document__source-note">${escapeHtml(text.sourceNote)}</p>
      </article>`;
    target.querySelector(".partner-document__close")?.addEventListener("click", close);
    return data;
  } catch (error) {
    console.error(error);
    target.innerHTML = `<div class="partner-document__header"><h3>${escapeHtml(text.title)}</h3><button type="button" class="partner-document__close" aria-label="${escapeHtml(text.close)}">×</button></div><p class="partner-report__error">${escapeHtml(error.message || error)}</p>`;
    target.querySelector(".partner-document__close")?.addEventListener("click", close);
    return null;
  }
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
  const pastMonths = options.pastMonths === undefined
    ? 2
    : Math.max(0, Math.min(3, Number(options.pastMonths) || 0));
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
      for (const [itemIndex, item] of items.entries()) {
        const row = item.row;
        const idx = item.idx;
        html += `<tr class="partner-journal-row${item.month === month ? " is-current-month" : ""}" data-journal-index="${itemIndex}" data-month="${escapeHtml(item.month)}" data-transaction-id="${escapeHtml(row[idx.transactionId])}" data-line-id="${escapeHtml(row[idx.lineId])}" tabindex="0" role="button"><td>${escapeHtml(item.month)}</td><td>${escapeHtml(row[idx.transactionId])}</td><td>${escapeHtml(row[idx.lineId])}</td>
          <td>${escapeHtml(row[idx.date])}</td><td>${escapeHtml(idx.voucher >= 0 ? row[idx.voucher] : "")}</td><td>${escapeHtml(row[idx.description])}</td>
          <td>${escapeHtml(accountLabel(row, idx.debitAccount, idx.debitName))}</td><td>${formatNumberLike(row[idx.debitAmount])}</td>
          <td>${escapeHtml(accountLabel(row, idx.creditAccount, idx.creditName))}</td><td>${formatNumberLike(row[idx.creditAmount])}</td></tr>`;
      }
      return html + "</tbody></table></div></section>";
    };
    const displayedRange = `${displayMonths[0]} - ${displayMonths[displayMonths.length - 1]}`;
    target.innerHTML = `${heading()}<p class="partner-journal-range__period">${escapeHtml(rangeText.range)}: ${escapeHtml(displayedRange)}</p>
      <div class="partner-detail-layout"><div class="partner-journal-grid">${renderTable(text.journalIncludedTitle, related, "is-included")}</div>
      <aside id="partnerDocumentDetail" class="partner-document-detail" aria-live="polite" hidden></aside></div>`;
    bindRangeControls();
    const detailLayout = target.querySelector(".partner-detail-layout");
    const documentTarget = target.querySelector("#partnerDocumentDetail");
    const selectJournalRow = async journalRow => {
      const itemIndex = Number(journalRow.dataset.journalIndex);
      const item = related[itemIndex];
      if (!item || !documentTarget) return;
      target.querySelectorAll(".partner-journal-row.is-selected, .partner-journal-row.is-related-entry").forEach(row => {
        row.classList.remove("is-selected", "is-related-entry");
      });
      journalRow.classList.add("is-selected");
      detailLayout?.classList.add("has-document");
      const documentData = await renderRelatedDocumentDetail(documentTarget, item, partner, viewKey);
      if (!documentData?.link) return;
      const relatedMonth = documentData.isInvoice
        ? documentData.linkValue(documentData.link, "settlement_month")
        : documentData.linkValue(documentData.link, "invoice_month");
      const relatedTransaction = documentData.isInvoice
        ? documentData.linkValue(documentData.link, "settlement_transaction_id")
        : documentData.linkValue(documentData.link, "invoice_transaction_id");
      const relatedLine = documentData.isInvoice
        ? documentData.linkValue(documentData.link, "settlement_line_id")
        : documentData.linkValue(documentData.link, "invoice_line_id");
      for (const candidate of target.querySelectorAll(".partner-journal-row")) {
        if (candidate === journalRow) continue;
        if (candidate.dataset.month === relatedMonth &&
          candidate.dataset.transactionId === relatedTransaction &&
            candidate.dataset.lineId === relatedLine) {
          candidate.classList.add("is-related-entry");
          candidate.title = currentLang === "en"
            ? "Related journal entry — select to display its source document"
            : "関連する仕訳です。選択すると、この仕訳の根拠文書を表示します。";
        }
      }
    };
    for (const journalRow of target.querySelectorAll(".partner-journal-row")) {
      journalRow.addEventListener("click", () => selectJournalRow(journalRow));
      journalRow.addEventListener("keydown", event => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          selectJournalRow(journalRow);
        }
      });
    }
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
    acctSel.value = partner.code;
    updateUrlQuery({ account: partner.code });
    renderPartnerReport(rows, viewKey, month);
  };
  for (const tr of wrapEl.querySelectorAll(".partner-report__partner-row")) {
    tr.addEventListener("click", activate);
    tr.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); activate(event); }
    });
  }
  if (partnerFilter) {
    const selectedPartner = byCode.get(partnerFilter);
    const selectedRow = wrapEl.querySelector(`.partner-report__partner-row[data-partner-code="${CSS.escape(partnerFilter)}"]`);
    if (selectedPartner && selectedRow) {
      selectedRow.classList.add("is-selected");
      renderPartnerJournalDetail(viewKey, month, selectedPartner);
    }
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

function businessDocumentText() {
  return currentLang === "en" ? {
    title: "Business Documents",
    all: "All",
    salesInvoices: "Sales invoices",
    purchaseInvoices: "Purchase invoices",
    receipts: "Receipt-related",
    payments: "Payment-related",
    adjustments: "Adjustment documents",
    month: "Target month",
    asOfMonth: "As-of month",
    partner: "Partner",
    status: "Status",
    allMonths: "All months",
    allPartners: "All partners",
    allStatuses: "All statuses",
    issueOnly: "Issues only",
    documentDate: "Document date",
    documentNumber: "Document number",
    managementId: "Management ID",
    type: "Document type",
    ledgerType: "AR/AP",
    amount: "Amount",
    applied: "Applied",
    open: "Open",
    relatedCount: "Related",
    selectHint: "Select a document to display its related documents, journal entries and application status in the right-side panel.",
    selected: "Selected document",
    relatedDocuments: "Related documents",
    relatedJournals: "Related journal entries",
    applications: "Cash applications",
    parties: "Issuer / recipient",
    items: "Document line items",
    noRelated: "No directly related document is registered.",
    noRelatedAsOf: "No related document exists as of the selected month end.",
    noJournal: "No related journal entry is registered.",
    noApplication: "No cash application is registered.",
    noApplicationAsOf: "No cash application exists as of the selected month end.",
    noRows: "No documents match the selected filters.",
    relation: "Relationship",
    transaction: "Transaction / line",
    description: "Description",
    debit: "Debit",
    credit: "Credit",
    applicationDate: "Application date",
    settlement: "Settlement",
    matched: "Matched",
    unposted: "Unposted",
    unapplied: "Unsettled",
    partial: "Partially settled",
    overapplied: "Overapplied",
    mismatch: "Amount mismatch",
    invalid: "Invalid link",
    issueTitle: "Inconsistency",
    missingJournal: "No journal link",
    missingOpenItem: "No AR/AP open item",
    missingSettlement: "No settlement record",
    amountMismatch: "Document and accounting amounts differ",
  } : {
    title: "業務文書",
    all: "すべて",
    salesInvoices: "売上請求書",
    purchaseInvoices: "仕入請求書",
    receipts: "入金関連",
    payments: "支払関連",
    adjustments: "調整文書",
    month: "対象月",
    asOfMonth: "基準月",
    partner: "取引先",
    status: "状態",
    allMonths: "全期間",
    allPartners: "全取引先",
    allStatuses: "全状態",
    issueOnly: "不一致のみ",
    documentDate: "文書日付",
    documentNumber: "文書番号",
    managementId: "管理番号",
    type: "文書種類",
    ledgerType: "債権債務",
    amount: "金額",
    applied: "消込済額",
    open: "未消込額",
    relatedCount: "関連",
    selectHint: "文書を選択すると、関連文書、関連仕訳及び消込状況を右ペインに表示します。",
    selected: "選択した文書",
    relatedDocuments: "関連する文書",
    relatedJournals: "関連する仕訳",
    applications: "消込状況",
    parties: "発行者・受領者",
    items: "文書明細",
    noRelated: "直接関連する文書は登録されていません。",
    noRelatedAsOf: "基準月末時点の関連文書はありません。",
    noJournal: "関連仕訳は登録されていません。",
    noApplication: "消込レコードは登録されていません。",
    noApplicationAsOf: "基準月末時点の消込レコードはありません。",
    noRows: "選択した条件に該当する文書はありません。",
    relation: "関係",
    transaction: "伝票ID／明細行ID",
    description: "摘要文",
    debit: "借方",
    credit: "貸方",
    applicationDate: "消込日",
    settlement: "精算文書",
    matched: "一致",
    unposted: "未計上",
    unapplied: "未清算",
    partial: "一部清算",
    overapplied: "過剰消込",
    mismatch: "金額不一致",
    invalid: "リンク不正",
    issueTitle: "不一致内容",
    missingJournal: "仕訳リンクがありません",
    missingOpenItem: "債権債務明細がありません",
    missingSettlement: "精算レコードがありません",
    amountMismatch: "文書金額と会計金額が一致しません",
  };
}

function documentTypeGroup(typeCode) {
  const code = String(typeCode || "").toUpperCase();
  if (code === "SALES_INVOICE") return "salesInvoices";
  if (code === "PURCHASE_INVOICE") return "purchaseInvoices";
  if (code === "BANK_RECEIPT_NOTICE" || code === "NOTE_RECEIPT") return "receipts";
  if (code === "BANK_TRANSFER_RECEIPT" || code === "NOTE_ISSUE") return "payments";
  return "adjustments";
}

const BUSINESS_DOCUMENT_MAX_AS_OF_MONTH = "2022-05";

function businessDocumentMonthRange(startMonth, endMonth = BUSINESS_DOCUMENT_MAX_AS_OF_MONTH) {
  const [startYear, startNumber] = String(startMonth || "").split("-").map(Number);
  const [endYear, endNumber] = String(endMonth || "").split("-").map(Number);
  if (!startYear || !startNumber || !endYear || !endNumber || startMonth > endMonth) return [endMonth];
  const months = [];
  let year = startYear;
  let month = startNumber;
  for (let count = 0; count < 36; count += 1) {
    const value = `${year}-${String(month).padStart(2, "0")}`;
    months.push(value);
    if (value === endMonth) break;
    month += 1;
    if (month > 12) {
      year += 1;
      month = 1;
    }
  }
  return months;
}

function businessDocumentMonthEnd(month) {
  const [year, monthNumber] = String(month || "").split("-").map(Number);
  if (!year || !monthNumber) return "9999-12-31";
  return new Date(Date.UTC(year, monthNumber, 0)).toISOString().slice(0, 10);
}

function isBusinessDocumentWithinPeriod(document, cutoffDate) {
  const date = String(document?.Document_Date || "").trim();
  return !date || date <= cutoffDate;
}

function recordsFromCsv(rows) {
  return (rows || []).slice(1).map(row => {
    const record = {};
    for (const [index, name] of (rows[0] || []).entries()) record[name] = String(row[index] || "").trim();
    return record;
  });
}

async function loadBusinessDocumentModel({ cutoffDate = businessDocumentMonthEnd(BUSINESS_DOCUMENT_MAX_AS_OF_MONTH) } = {}) {
  const partnerFile = currentLang === "en" ? "trading_partner_en.csv" : "trading_partner.csv";
  const source = name => joinUrlPath(DATA_ROOT, currentLang, "source", name);
  const [documentRows, partyRows, detailRows, openRows, settlementRows, applicationRows, journalLinkRows, transactionLinkRows, partnerRows] = await Promise.all([
    fetchCSV(source("business_document.csv")),
    fetchOptionalCSV(source("business_document_party.csv")),
    fetchOptionalCSV(source("transaction_document_detail.csv")),
    fetchOptionalCSV(source("ar_ap_open_item.csv")),
    fetchOptionalCSV(source("cash_settlement.csv")),
    fetchOptionalCSV(source("cash_application.csv")),
    fetchOptionalCSV(source("journal_document_link.csv")),
    fetchOptionalCSV(source("transaction_document_link.csv")),
    fetchOptionalCSV(source(partnerFile)),
  ]);
  const documents = recordsFromCsv(documentRows);
  const parties = recordsFromCsv(partyRows);
  const details = recordsFromCsv(detailRows);
  const openItems = recordsFromCsv(openRows);
  const settlements = recordsFromCsv(settlementRows);
  const applications = recordsFromCsv(applicationRows);
  const effectiveApplications = applications.filter(application =>
    !application.Application_Date || application.Application_Date <= cutoffDate
  );
  const journalLinks = recordsFromCsv(journalLinkRows);
  const transactionLinks = recordsFromCsv(transactionLinkRows);
  const partners = recordsFromCsv(partnerRows);
  const documentById = new Map(documents.map(document => [document.Document_ID, document]));
  const openByDocument = new Map(openItems.map(item => [item.Invoice_Document_ID, item]));
  const settlementByDocument = new Map(settlements.map(item => [item.Settlement_Document_ID, item]));
  const applicationsByOpen = new Map();
  const applicationsBySettlement = new Map();
  for (const application of effectiveApplications) {
    if (!applicationsByOpen.has(application.Open_Item_ID)) applicationsByOpen.set(application.Open_Item_ID, []);
    applicationsByOpen.get(application.Open_Item_ID).push(application);
    if (!applicationsBySettlement.has(application.Settlement_ID)) applicationsBySettlement.set(application.Settlement_ID, []);
    applicationsBySettlement.get(application.Settlement_ID).push(application);
  }
  const journalLinksByDocument = new Map();
  for (const link of journalLinks) {
    if (!journalLinksByDocument.has(link.Document_ID)) journalLinksByDocument.set(link.Document_ID, []);
    journalLinksByDocument.get(link.Document_ID).push(link);
  }
  const partnerNames = new Map();
  for (const partner of partners) {
    const category = String(partner.category || "").toLowerCase();
    const partnerType = category.includes("得意") || category.includes("customer") ? "C"
      : (category.includes("仕入") || category.includes("supplier") ? "S" : "");
    if (partnerType) partnerNames.set(`${partnerType}:${partner.code}`, partner.name);
  }
  const statusFor = document => {
    const text = businessDocumentText();
    const issues = [];
    const amount = numberValue(document.Gross_Amount);
    const links = journalLinksByDocument.get(document.Document_ID) || [];
    if (!links.length) issues.push(text.missingJournal);
    const group = documentTypeGroup(document.Document_Type_Code);
    let applied = 0;
    let open = 0;
    let status = "matched";
    const invoiceDocument = group === "salesInvoices" || group === "purchaseInvoices";
    if (invoiceDocument) {
      const item = openByDocument.get(document.Document_ID);
      if (!item) {
        issues.push(text.missingOpenItem);
        status = "invalid";
      } else {
        const original = numberValue(item.Original_Amount);
        const matchedApplications = applicationsByOpen.get(item.Open_Item_ID) || [];
        applied = matchedApplications.reduce((sum, application) => sum + numberValue(application.Applied_Amount), 0);
        open = original - applied;
        if (original !== amount) issues.push(text.amountMismatch);
        if (applied > original) status = "overapplied";
        else if (applied === 0 && original !== 0) status = "unapplied";
        else if (applied < original) status = "partial";
      }
    } else {
      const settlement = settlementByDocument.get(document.Document_ID);
      if (!settlement) {
        issues.push(text.missingSettlement);
        status = "invalid";
      } else {
        const matchedApplications = applicationsBySettlement.get(settlement.Settlement_ID) || [];
        applied = matchedApplications.reduce((sum, application) => sum + numberValue(application.Applied_Amount), 0);
        open = amount - applied;
        if (applied > amount) status = "overapplied";
        else if (applied === 0 && amount !== 0) status = "unapplied";
        else if (applied < amount) status = "partial";
      }
    }
    if (issues.some(issue => issue === text.amountMismatch)) status = "mismatch";
    else if (!links.length && status === "matched") status = "unposted";
    return { status, issues, amount, applied, open };
  };
  const enriched = documents.map(document => {
    const state = statusFor(document);
    const partnerKey = `${document.Partner_Type}:${document.Partner_Code}`;
    return {
      ...document,
      ...state,
      partnerName: partnerNames.get(partnerKey) || partnerKey,
      month: String(document.Document_Date || "").slice(0, 7),
      typeGroup: documentTypeGroup(document.Document_Type_Code),
    };
  });
  return {
    documents: enriched, documentById, parties, details, openItems, settlements, applications: effectiveApplications,
    journalLinksByDocument, openByDocument, settlementByDocument, applicationsByOpen,
    applicationsBySettlement, transactionLinks, partnerNames,
  };
}

function relatedDocumentIds(model, document) {
  const ids = new Set();
  const openItem = model.openByDocument.get(document.Document_ID);
  const settlement = model.settlementByDocument.get(document.Document_ID);
  if (openItem) {
    for (const application of model.applicationsByOpen.get(openItem.Open_Item_ID) || []) {
      const matched = model.settlements.find(item => item.Settlement_ID === application.Settlement_ID);
      if (matched?.Settlement_Document_ID) ids.add(matched.Settlement_Document_ID);
    }
  }
  if (settlement) {
    for (const application of model.applicationsBySettlement.get(settlement.Settlement_ID) || []) {
      const matched = model.openItems.find(item => item.Open_Item_ID === application.Open_Item_ID);
      if (matched?.Invoice_Document_ID) ids.add(matched.Invoice_Document_ID);
    }
  }
  for (const link of model.transactionLinks) {
    if (link.invoice_document_id === document.Document_ID && link.settlement_document_id) ids.add(link.settlement_document_id);
    if (link.settlement_document_id === document.Document_ID && link.invoice_document_id) ids.add(link.invoice_document_id);
  }
  ids.delete(document.Document_ID);
  return [...ids];
}

async function documentJournalRows(model, document) {
  const links = model.journalLinksByDocument.get(document.Document_ID) || [];
  const month = String(document.Document_Date || "").slice(0, 7);
  const available = Array.isArray(INDEX?.views?.journal?.available) ? INDEX.views.journal.available : [];
  let rows = [];
  let indexes = null;
  if (month && available.includes(month)) {
    rows = await fetchOptionalCSV(resolveCsvUrl("journal", month));
    indexes = journalRowIndexes(rows);
  }
  return links.map(link => {
    let row = null;
    if (indexes && indexes.transactionId >= 0 && indexes.lineId >= 0) {
      row = rows.slice(1).find(candidate =>
        String(candidate[indexes.transactionId] || "").trim() === link.Transaction_ID &&
        String(candidate[indexes.lineId] || "").trim() === link.Line_ID
      ) || null;
    }
    return { link, row, indexes, month };
  });
}

async function renderBusinessDocumentView() {
  wrapEl.classList.remove("partner-report-wrap");
  wrapEl.classList.add("business-documents-wrap");
  const text = businessDocumentText();
  setStatus(currentLang === "en" ? "Loading business documents..." : "業務文書を読み込んでいます…");
  const url = new URL(location.href);
  const selectedMonth = monthSel.value || "2021-04";
  const asOfMonth = asOfSel?.value || selectedMonth;
  const cutoffDate = businessDocumentMonthEnd(asOfMonth);
  const model = await loadBusinessDocumentModel({ cutoffDate });
  const requestedType = url.searchParams.get("docType") || "all";
  const selectedType = requestedType === "invoices" ? "all" : requestedType;
  const selectedPartner = url.searchParams.get("docPartner") || "";
  const selectedStatus = url.searchParams.get("docStatus") || "";
  const selectedDocumentId = url.searchParams.get("document") || "";
  const scopedDocuments = model.documents.filter(document =>
    isBusinessDocumentWithinPeriod(document, cutoffDate)
  );
  const targetMonthDocuments = scopedDocuments.filter(document => {
    if (!selectedMonth || document.month === selectedMonth) return true;
    const invoiceDocument = document.typeGroup === "salesInvoices" || document.typeGroup === "purchaseInvoices";
    return invoiceDocument && document.month < selectedMonth && document.open > 0;
  });
  const partnerOptions = [...new Map(targetMonthDocuments.map(document => [
    `${document.Partner_Type}:${document.Partner_Code}`,
    { value: `${document.Partner_Type}:${document.Partner_Code}`, label: document.partnerName },
  ])).values()].sort((a, b) => a.label.localeCompare(b.label, currentLang));
  const statusKeys = ["matched", "unposted", "unapplied", "partial", "overapplied", "mismatch", "invalid"];
  const filtered = targetMonthDocuments.filter(document =>
    (selectedType === "all" || document.typeGroup === selectedType) &&
    (!selectedPartner || `${document.Partner_Type}:${document.Partner_Code}` === selectedPartner) &&
    (!selectedStatus || (selectedStatus === "issues" ? document.status !== "matched" : document.status === selectedStatus))
  );
  const documentReference = document => {
    const number = document.Document_Number || "－";
    return `${number}（${text.managementId}：${document.Document_ID}）`;
  };
  const statusLabel = key => text[key] || key;
  const selected = scopedDocuments.find(document => document.Document_ID === selectedDocumentId) || null;
  wrapEl.classList.toggle("has-business-document-detail", Boolean(selected));
  let html = `<section class="business-documents">
    <div class="business-documents__heading"><h1>${escapeHtml(text.title)}</h1></div>
    <div class="business-documents__filters">
      <label>${escapeHtml(text.partner)}<select id="documentPartnerFilter"><option value="">${escapeHtml(text.allPartners)}</option>${partnerOptions.map(option => `<option value="${escapeHtml(option.value)}"${option.value === selectedPartner ? " selected" : ""}>${escapeHtml(option.label)}</option>`).join("")}</select></label>
      <label>${escapeHtml(text.status)}<select id="documentStatusFilter"><option value="">${escapeHtml(text.allStatuses)}</option><option value="issues"${selectedStatus === "issues" ? " selected" : ""}>${escapeHtml(text.issueOnly)}</option>${statusKeys.map(key => `<option value="${key}"${selectedStatus === key ? " selected" : ""}>${escapeHtml(statusLabel(key))}</option>`).join("")}</select></label>
    </div>
    <div class="business-documents__table-wrap"><table class="business-documents__table"><thead><tr>
      <th>${escapeHtml(text.status)}</th><th>${escapeHtml(text.documentDate)}</th><th>${escapeHtml(text.documentNumber)}</th>
      <th>${escapeHtml(text.managementId)}</th><th>${escapeHtml(text.type)}</th><th>${escapeHtml(text.ledgerType)}</th>
      <th>${escapeHtml(text.partner)}</th><th>${escapeHtml(text.amount)}</th><th>${escapeHtml(text.applied)}</th><th>${escapeHtml(text.open)}</th>
    </tr></thead><tbody>`;
  if (!filtered.length) html += `<tr><td colspan="10">${escapeHtml(text.noRows)}</td></tr>`;
  for (const document of filtered) {
    html += `<tr class="business-documents__row status-${escapeHtml(document.status)}${document.Document_ID === selectedDocumentId ? " is-selected" : ""}" data-document-id="${escapeHtml(document.Document_ID)}" tabindex="0" role="button">
      <td><span class="document-status document-status--${escapeHtml(document.status)}">${escapeHtml(statusLabel(document.status))}</span></td>
      <td>${escapeHtml(document.Document_Date)}</td><td>${escapeHtml(document.Document_Number || "－")}</td><td>${escapeHtml(document.Document_ID)}</td>
      <td>${escapeHtml(document.Document_Type_Name)}</td><td>${escapeHtml(document.Partner_Type === "C" ? "AR" : "AP")}</td>
      <td>${escapeHtml(document.partnerName)}</td><td>${formatNumberLike(document.amount)}</td><td>${formatNumberLike(document.applied)}</td><td>${formatNumberLike(document.open)}</td></tr>`;
  }
  html += `</tbody></table></div><p class="business-documents__hint">${escapeHtml(text.selectHint)}</p>`;
  if (selected) {
    const relatedIds = relatedDocumentIds(model, selected);
    const related = relatedIds
      .map(id => model.documents.find(document => document.Document_ID === id))
      .filter(document => document && isBusinessDocumentWithinPeriod(document, cutoffDate));
    const journalRows = await documentJournalRows(model, selected);
    const openItem = model.openByDocument.get(selected.Document_ID);
    const settlement = model.settlementByDocument.get(selected.Document_ID);
    const selectedApplications = openItem
      ? (model.applicationsByOpen.get(openItem.Open_Item_ID) || [])
      : (settlement ? (model.applicationsBySettlement.get(settlement.Settlement_ID) || []) : []);
    const selectedParties = model.parties.filter(party => party.Document_ID === selected.Document_ID);
    const selectedDetails = model.details.filter(detail => detail.Document_ID === selected.Document_ID);
    const closeLabel = currentLang === "en" ? "Close document details" : "文書詳細を閉じる";
    html += `<aside class="business-document-detail" aria-label="${escapeHtml(text.selected)}">
      <div class="business-document-detail__header">
        <h2>${escapeHtml(text.selected)}：${escapeHtml(documentReference(selected))}</h2>
        <button type="button" class="business-document-detail__close" aria-label="${escapeHtml(closeLabel)}" title="${escapeHtml(closeLabel)}">×</button>
      </div>
      <div class="business-document-detail__body">
      <dl class="business-document-detail__meta">
        <div><dt>${escapeHtml(text.type)}</dt><dd>${escapeHtml(selected.Document_Type_Name)}</dd></div>
        <div><dt>${escapeHtml(text.partner)}</dt><dd>${escapeHtml(selected.partnerName)}</dd></div>
        <div><dt>${escapeHtml(text.amount)}</dt><dd class="amount">${formatNumberLike(selected.amount)}</dd></div>
        <div><dt>${escapeHtml(text.status)}</dt><dd>${escapeHtml(statusLabel(selected.status))}</dd></div>
      </dl>`;
    if (selected.issues.length) {
      html += `<div class="business-document-detail__issues"><strong>${escapeHtml(text.issueTitle)}</strong><ul>${selected.issues.map(issue => `<li>${escapeHtml(issue)}</li>`).join("")}</ul></div>`;
    }
    if (selectedParties.length) {
      html += `<h3>${escapeHtml(text.parties)}</h3><div class="business-document-detail__parties">${selectedParties.map(party =>
        `<div><strong>${escapeHtml(party.Role_Code)}</strong><span>${escapeHtml(party.Party_Name)}</span><span>${escapeHtml([party.Department_Name, party.Person_Name].filter(Boolean).join(" / "))}</span></div>`
      ).join("")}</div>`;
    }
    if (selectedDetails.length) {
      html += `<h3>${escapeHtml(text.items)}</h3><div class="business-document-detail__table-wrap"><table><thead><tr><th>#</th><th>${escapeHtml(documentDetailText().item)}</th><th>${escapeHtml(documentDetailText().quantity)}</th><th>${escapeHtml(documentDetailText().taxCategory)}</th><th>${escapeHtml(documentDetailText().gross)}</th></tr></thead><tbody>${selectedDetails.map(detail =>
        `<tr><td>${escapeHtml(detail.Line_Number)}</td><td>${escapeHtml(detail.Item_Description)}</td><td>${escapeHtml(detail.Quantity)} ${escapeHtml(detail.Unit)}</td><td>${escapeHtml(detail.Tax_Category)}</td><td>${formatNumberLike(detail.Gross_Amount)}</td></tr>`
      ).join("")}</tbody></table></div>`;
    }
    html += `<h3>${escapeHtml(text.relatedDocuments)}</h3><div class="business-document-detail__table-wrap"><table class="business-document-related"><thead><tr><th>${escapeHtml(text.documentDate)}</th><th>${escapeHtml(text.type)}</th><th>${escapeHtml(text.documentNumber)}</th><th>${escapeHtml(text.managementId)}</th><th>${escapeHtml(text.amount)}</th><th>${escapeHtml(text.status)}</th></tr></thead><tbody>`;
    html += related.length ? related.map(document => `<tr data-document-id="${escapeHtml(document.Document_ID)}" tabindex="0" role="button"><td>${escapeHtml(document.Document_Date)}</td><td>${escapeHtml(document.Document_Type_Name)}</td><td>${escapeHtml(document.Document_Number || "－")}</td><td>${escapeHtml(document.Document_ID)}</td><td>${formatNumberLike(document.amount)}</td><td>${escapeHtml(statusLabel(document.status))}</td></tr>`).join("")
      : `<tr><td colspan="6">${escapeHtml(text.noRelatedAsOf)}</td></tr>`;
    html += `</tbody></table></div><h3>${escapeHtml(text.relatedJournals)}</h3><div class="business-document-detail__table-wrap"><table><thead><tr><th>${escapeHtml(text.transaction)}</th><th>${escapeHtml(text.documentDate)}</th><th>${escapeHtml(text.description)}</th><th>${escapeHtml(text.debit)}</th><th>${escapeHtml(text.credit)}</th></tr></thead><tbody>`;
    html += journalRows.length ? journalRows.map(item => {
      const idx = item.indexes;
      const row = item.row;
      const account = (code, name) => [code, name].filter(Boolean).join(" ");
      return `<tr><td>${escapeHtml(`${item.link.Transaction_ID} / ${item.link.Line_ID}`)}</td><td>${escapeHtml(row && idx ? row[idx.date] : item.month)}</td>
        <td>${escapeHtml(row && idx ? row[idx.description] : item.link.Relationship_Type)}</td>
        <td>${escapeHtml(row && idx ? account(row[idx.debitAccount], idx.debitName >= 0 ? row[idx.debitName] : "") : "")}${row && idx ? `<strong>${formatNumberLike(row[idx.debitAmount])}</strong>` : ""}</td>
        <td>${escapeHtml(row && idx ? account(row[idx.creditAccount], idx.creditName >= 0 ? row[idx.creditName] : "") : "")}${row && idx ? `<strong>${formatNumberLike(row[idx.creditAmount])}</strong>` : ""}</td></tr>`;
    }).join("") : `<tr><td colspan="5">${escapeHtml(text.noJournal)}</td></tr>`;
    html += `</tbody></table></div><h3>${escapeHtml(text.applications)}</h3><div class="business-document-detail__table-wrap"><table><thead><tr><th>${escapeHtml(text.applicationDate)}</th><th>${escapeHtml(text.amount)}</th><th>${escapeHtml(text.settlement)}</th><th>${escapeHtml(text.status)}</th></tr></thead><tbody>`;
    html += selectedApplications.length ? selectedApplications.map(application => {
      const matchedSettlement = model.settlements.find(item => item.Settlement_ID === application.Settlement_ID);
      return `<tr><td>${escapeHtml(application.Application_Date)}</td><td>${formatNumberLike(application.Applied_Amount)}</td><td>${escapeHtml(matchedSettlement?.Settlement_Document_ID || application.Settlement_ID)}</td><td>${escapeHtml(application.Status)}</td></tr>`;
    }).join("") : `<tr><td colspan="4">${escapeHtml(text.noApplicationAsOf)}</td></tr>`;
    html += `</tbody></table></div></div></aside>`;
  }
  html += "</section>";
  wrapEl.innerHTML = html;
  const updateFilter = (key, value) => {
    updateUrlQuery({ [key]: value || null, document: null });
    renderBusinessDocumentView().catch(error => {
      console.error(error);
      setStatus(String(error?.message || error));
    });
  };
  wrapEl.querySelector("#documentPartnerFilter")?.addEventListener("change", event => updateFilter("docPartner", event.target.value));
  wrapEl.querySelector("#documentStatusFilter")?.addEventListener("change", event => updateFilter("docStatus", event.target.value));
  wrapEl.querySelector(".business-document-detail__close")?.addEventListener("click", () => {
    updateUrlQuery({ document: null });
    renderBusinessDocumentView().catch(error => {
      console.error(error);
      setStatus(String(error?.message || error));
    });
  });
  const selectDocument = element => {
    updateUrlQuery({ document: element.dataset.documentId });
    renderBusinessDocumentView().catch(error => {
      console.error(error);
      setStatus(String(error?.message || error));
    });
  };
  for (const row of wrapEl.querySelectorAll(".business-documents__row, .business-document-related tr[data-document-id]")) {
    row.addEventListener("click", () => selectDocument(row));
    row.addEventListener("keydown", event => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectDocument(row);
      }
    });
  }
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
  if (asOfLabelTextEl) asOfLabelTextEl.textContent = currentLang === "en" ? "As-of month" : "基準月";
  if (searchLabelTextEl) searchLabelTextEl.textContent = currentLang === "en" ? "Search" : "検索";
  if (aboutLinkEl) {
    aboutLinkEl.textContent = currentLang === "en" ? "ABOUT" : "概要";
    aboutLinkEl.href = currentLang === "en" ? "./about_en.html" : "./about.html";
  }
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
      const demoNotice = currentLang === "en"
        ? "All data shown is fictional demonstration data. The accounting period is from April 2021 to March 2022; only transactions required to illustrate receipt and payment relationships include reference data from the two months before and after this period."
        : "※ 本画面のデータはすべて架空のデモデータです。会計取引の対象期間は2021年4月から2022年3月までですが、入出金との対応確認に必要な取引に限り、対象期間外の前後2か月分も参考データとして設定しています。";
      companyHeaderEl.innerHTML = `<span class="company-header__name">${escapeHtml(company.name)}</span><span class="company-header__address">${escapeHtml(address)}</span><span class="company-header__notice">${escapeHtml(demoNotice)}</span>`;
      companyHeaderEl.hidden = false;
    } else {
      companyHeaderEl.innerHTML = "";
      companyHeaderEl.hidden = true;
    }
  }
  renderModeSwitch();
  applyFontSize();
}

// -------- App state --------
let INDEX = null;
let currentView = "ledger";
let lastAccountingView = "ledger";
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
  const viewKeys = Object.keys(INDEX.views || {}).filter(key => key !== "documents");
  const groupEnds = new Set(["tidy", "trial_balance", "pnl"]);
  for (const [index, key] of viewKeys.entries()) {
    const btn = document.createElement("button");
    btn.textContent = tViewLabel(key);
    btn.dataset.key = key;
    btn.addEventListener("click", async () => {
      if (key === "ledger" && currentView !== "ledger") acctSel.value = "";
      lastAccountingView = key;
      currentView = key;
      await refresh({ skipReloadCsv: false }); // view change => reload (server)
    });
    navEl.appendChild(btn);
    if (groupEnds.has(key) && index < viewKeys.length - 1) {
      const separator = document.createElement("span");
      separator.className = "nav-separator";
      separator.dataset.after = key;
      separator.textContent = "｜";
      separator.setAttribute("aria-hidden", "true");
      navEl.appendChild(separator);
    }
  }
}

function renderModeSwitch() {
  if (!modeSwitchEl) return;
  const accountingLabel = isDocumentView()
    ? (currentLang === "en" ? "Back to accounting" : "会計帳簿へ戻る")
    : (currentLang === "en" ? "Accounting ledgers" : "会計帳簿");
  const documentsLabel = currentLang === "en" ? "Business documents" : "業務文書";
  modeSwitchEl.setAttribute("aria-label", currentLang === "en" ? "Display mode" : "表示モード");
  modeSwitchEl.innerHTML = `<button type="button" data-app-mode="accounting">${escapeHtml(accountingLabel)}</button><button type="button" data-app-mode="documents">${escapeHtml(documentsLabel)}</button>`;
  for (const button of modeSwitchEl.querySelectorAll("[data-app-mode]")) {
    const documentsMode = button.dataset.appMode === "documents";
    button.classList.toggle("active", documentsMode === isDocumentView());
    button.addEventListener("click", async () => {
      if (documentsMode) {
        if (!isDocumentView()) lastAccountingView = currentView;
        currentView = "documents";
      } else {
        currentView = lastAccountingView && lastAccountingView !== "documents" ? lastAccountingView : "ledger";
      }
      await refresh({ skipReloadCsv: false });
    });
  }
  renderDocumentTypeNav();
}

function renderDocumentTypeNav() {
  if (!documentTypeNavEl) return;
  const documentView = isDocumentView();
  documentTypeNavEl.hidden = !documentView;
  if (!documentView) {
    documentTypeNavEl.innerHTML = "";
    return;
  }
  const text = businessDocumentText();
  const requestedType = new URL(location.href).searchParams.get("docType") || "all";
  const selectedType = requestedType === "invoices" ? "all" : requestedType;
  const typeKeys = ["all", "salesInvoices", "purchaseInvoices", "receipts", "payments", "adjustments"];
  documentTypeNavEl.setAttribute("aria-label", currentLang === "en" ? "Document type" : "対象文書");
  documentTypeNavEl.innerHTML = typeKeys.map(key =>
    `<button type="button" data-document-type="${key}" class="${selectedType === key ? "active" : ""}">${escapeHtml(text[key])}</button>`
  ).join("");
  for (const button of documentTypeNavEl.querySelectorAll("[data-document-type]")) {
    button.addEventListener("click", async () => {
      const key = button.dataset.documentType;
      updateUrlQuery({ docType: key === "all" ? null : key, document: null });
      await refresh({ skipReloadCsv: true });
    });
  }
}

function setActiveButton() {
  for (const btn of navEl.querySelectorAll("button")) {
    btn.classList.toggle("active", btn.dataset.key === currentView);
  }
  updateStatementNavAvailability();
  renderModeSwitch();
}

function currentAsOfMonth() {
  return asOfSel?.value || monthSel?.value || "";
}

function statementsAvailable() {
  return currentAsOfMonth() >= "2022-03";
}

function updateStatementNavAvailability() {
  const available = statementsAvailable();
  for (const button of navEl?.querySelectorAll('button[data-key="balance_sheet"], button[data-key="pnl"]') || []) {
    button.hidden = !available;
  }
  const statementSeparator = navEl?.querySelector('.nav-separator[data-after="pnl"]');
  if (statementSeparator) statementSeparator.hidden = !available;
}

function updateViewControls() {
  const partnerView = isPartnerReportView();
  const documentView = isDocumentView();
  const allAccountsOnly = currentView === "trial_balance" || currentView === "balance_sheet" || currentView === "pnl";
  const annualReportView = currentView === "balance_sheet" || currentView === "pnl";
  const searchableView = currentView === "tidy" || currentView === "journal";
  if (allAccountsOnly) acctSel.value = "";
  if (!searchableView) searchInput.value = "";
  if (searchLabelEl) searchLabelEl.hidden = !searchableView;
  if (monthSel) {
    monthSel.hidden = annualReportView;
    const monthLabel = monthSel.closest("label");
    if (monthLabel) monthLabel.hidden = annualReportView;
  }
  if (asOfLabelEl) asOfLabelEl.hidden = annualReportView;
  if (monthLabelTextEl) {
    if (annualReportView) {
      const firstMonth = (INDEX?.months || []).find(value => /^\d{4}-\d{2}$/.test(String(value)));
      const fiscalYear = firstMonth ? String(firstMonth).slice(0, 4) : "";
      monthLabelTextEl.textContent = currentLang === "en"
        ? `Fiscal year${fiscalYear ? ` ${fiscalYear}` : ""}`
        : `対象年度${fiscalYear ? ` ${fiscalYear}年度` : ""}`;
    } else {
      monthLabelTextEl.textContent = currentLang === "en" ? "Month" : "対象月";
    }
  }
  if (accountLabelEl) {
    accountLabelEl.hidden = allAccountsOnly || documentView;
    accountLabelEl.firstChild.textContent = partnerView
      ? (currentLang === "en" ? "Partner " : "取引先 ")
      : (currentLang === "en" ? "Account " : "科目 ");
  }
  if (toggleCodeColsBtn) toggleCodeColsBtn.hidden = partnerView || documentView;
  if (columnToggleGroupEl) columnToggleGroupEl.hidden = partnerView || documentView;
  if (navEl) navEl.hidden = documentView;
  if (aboutLinkEl?.closest("button")) aboutLinkEl.closest("button").hidden = documentView;
  if (aboutSeparatorEl) aboutSeparatorEl.hidden = documentView;
  if (modeNavSeparatorEl) modeNavSeparatorEl.hidden = false;
  renderDocumentTypeNav();
}

function initMonthSelect(months) {
  buildOptions(monthSel, months || [], { includeAll: false });

  monthSel.addEventListener("change", async () => {
    setAsOfMonthOptions(asOfSel?.value);
    await refresh({ skipReloadCsv: false });
  });
}

function setAsOfMonthOptions(preferredValue = "") {
  if (!asOfSel) return;
  const targetMonth = monthSel.value || (INDEX?.months?.[0] ?? "2021-04");
  const options = businessDocumentMonthRange(targetMonth);
  buildOptions(asOfSel, options, { includeAll: false });
  asOfSel.value = options.includes(preferredValue) ? preferredValue : targetMonth;
  updateStatementNavAvailability();
}

function initAsOfSelect(initialValue = "") {
  if (!asOfSel) return;
  setAsOfMonthOptions(initialValue);
  asOfSel.addEventListener("change", async () => {
    if (!statementsAvailable() && (currentView === "balance_sheet" || currentView === "pnl")) {
      currentView = "trial_balance";
    }
    await refresh({ skipReloadCsv: !isDocumentView() });
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

  if (!statementsAvailable() && (currentView === "balance_sheet" || currentView === "pnl")) {
    currentView = "trial_balance";
  }
  setActiveButton();
  updateViewControls();

  const month = monthSel.value || (INDEX?.months?.[0] ?? "");
  const asOfMonth = currentAsOfMonth() || month;
  const searchableView = currentView === "tidy" || currentView === "journal";
  const q = searchableView ? String(searchInput.value || "").trim() : "";
  const acct = isDocumentView() ? "" : (acctSel.value || "");

  updateUrlQuery({
    view: currentView,
    month,
    asOf: asOfMonth === month ? null : asOfMonth,
    account: acct,
    q,
    mode: dataMode,
    lang: currentLang,
    dataset: (DATASET !== DATASET_DEFAULT ? DATASET : null),
  });

  try {
    wrapEl.innerHTML = "";
    wrapEl.classList.remove("business-documents-wrap", "has-business-document-detail");

    if (isDocumentView()) {
      await renderBusinessDocumentView();
      updateUrlQuery({
        view: currentView,
        month,
        asOf: asOfMonth === month ? null : asOfMonth,
        docMonth: null,
        subsequent: null,
        account: null,
        q: null,
        mode: dataMode,
        lang: currentLang,
        dataset: (DATASET !== DATASET_DEFAULT ? DATASET : null),
      });
      return;
    }

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
  INDEX.views.documents = { virtual: true, source: "business_document" };

  // restore state from URL if any
  const url = new URL(location.href);
  const qView = url.searchParams.get("view");
  const qLang = url.searchParams.get("lang");
  const qMonth = url.searchParams.get("month");
  const qAsOf = url.searchParams.get("asOf");
  initialAccountFromUrl = url.searchParams.get("account") || "";

  if (qLang && (qLang === "ja" || qLang === "en")) {
    currentLang = qLang;
    localStorage.setItem("ledger_lang", currentLang);
  }
  if (qView && INDEX.views && INDEX.views[qView]) currentView = qView;
  if (currentView !== "documents") lastAccountingView = currentView;
  // init UI
  initNav();
  initMonthSelect(INDEX.months || []);
  if (qMonth && (INDEX.months || []).includes(qMonth)) monthSel.value = qMonth;
  initAsOfSelect(qAsOf || "");
  initAccountSelect();
  initLangSelect();
  initFontSizeControl();
  initSearch();
  initColumnToggleButtons();
  renderModeSwitch();

  // set lang select
  if (langSel) langSel.value = currentLang;
  applyI18nTexts();

  await refresh({ skipReloadCsv: false });
}

main();
