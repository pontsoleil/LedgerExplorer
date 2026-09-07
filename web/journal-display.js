(function (root, factory) {
  const api = factory();
  if (typeof module === "object" && module.exports) module.exports = api;
  root.LedgerJournalDisplay = api;
}(typeof globalThis !== "undefined" ? globalThis : this, function () {
  "use strict";

  const debitName = /^(Debit_|JP06e_|JP02j_|GE05kw_|JP05a_|BS04fb_)/;
  const creditName = /^(Credit_|JP06f_|JP02k_|GE05kB_|JP05b_|BS04fc_)/;

  function cell(row, index) {
    return index >= 0 ? String(row[index] ?? "").trim() : "";
  }

  function buildDisplayRows(rows, datasetId) {
    if (!Array.isArray(rows) || rows.length < 2) return rows;
    const header = rows[0];
    const entryKeyIndex = header.indexOf("Entry_Key");
    const debitOccurrenceIndex = header.indexOf("Debit_Occurrence");
    const creditOccurrenceIndex = header.indexOf("Credit_Occurrence");
    const debitAmountIndex = header.indexOf("Debit_Amount");
    const creditAmountIndex = header.indexOf("Credit_Amount");
    const semanticPathIndex = header.indexOf("Semantic_Path");
    const bindingPathIndex = header.indexOf("Binding_Path");
    if ([entryKeyIndex, debitOccurrenceIndex, creditOccurrenceIndex, debitAmountIndex, creditAmountIndex].some(index => index < 0)) return rows;

    const debitIndexes = new Set();
    const creditIndexes = new Set();
    header.forEach((name, index) => {
      if (debitName.test(name) || index === debitAmountIndex || index === debitOccurrenceIndex) debitIndexes.add(index);
      if (creditName.test(name) || index === creditAmountIndex || index === creditOccurrenceIndex) creditIndexes.add(index);
    });

    const items = [];
    const groups = new Map();
    rows.slice(1).forEach((row, sourceIndex) => {
      const debit = cell(row, debitOccurrenceIndex) !== "";
      const credit = cell(row, creditOccurrenceIndex) !== "";
      const key = cell(row, entryKeyIndex);
      if (!key || debit === credit) {
        items.push({ order: sourceIndex, rows: [row] });
        return;
      }
      let group = groups.get(key);
      if (!group) {
        group = { order: sourceIndex, debit: [], credit: [] };
        groups.set(key, group);
        items.push(group);
      }
      group[debit ? "debit" : "credit"].push(row);
    });

    for (const group of groups.values()) {
      const count = Math.max(group.debit.length, group.credit.length);
      group.rows = [];
      for (let index = 0; index < count; index += 1) {
        const debit = group.debit[index] || null;
        const credit = group.credit[index] || null;
        const base = debit || credit;
        const merged = base.slice();
        for (let column = 0; column < header.length; column += 1) {
          if (debitIndexes.has(column)) merged[column] = debit ? debit[column] : "";
          else if (creditIndexes.has(column)) merged[column] = credit ? credit[column] : "";
          else merged[column] = (debit && debit[column] !== "") ? debit[column] : (credit ? credit[column] : base[column]);
        }
        merged.journalTrace = {
          datasetId,
          entryKey: cell(base, entryKeyIndex),
          debitOccurrence: debit ? cell(debit, debitOccurrenceIndex) : "",
          creditOccurrence: credit ? cell(credit, creditOccurrenceIndex) : "",
          debitSemanticPath: debit ? cell(debit, semanticPathIndex) : "",
          creditSemanticPath: credit ? cell(credit, semanticPathIndex) : "",
          debitBindingPath: debit ? cell(debit, bindingPathIndex) : "",
          creditBindingPath: credit ? cell(credit, bindingPathIndex) : ""
        };
        group.rows.push(merged);
      }
    }

    const body = items.sort((a, b) => a.order - b.order).flatMap(item => item.rows);
    return [header, ...body];
  }

  return { buildDisplayRows };
}));
