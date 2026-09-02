# UADC and LedgerExplorer
## Reference Architecture

## 1. Document Status and Objectives

This document publishes the reference architecture connecting the XBRL GL Next Taxonomy Framework, Universal Adapter for Data Conversion (UADC), semantic-model-based Structured CSV, and LedgerExplorer. It is aligned with LedgerExplorer implementation baseline `306d76cc69f149c65a889b4811ee4ebad48ecdeb` and should be read with the [LedgerExplorer Architecture and Design Specification](LedgerExplorer_Architecture_and_Design_Specification.md) and the [repository README](../../README.md).

The architecture has the following objectives:

- application-independent use of accounting and transactional data;
- reusable source-data conversion;
- reusable visualization and analysis;
- a standardized semantic representation between producer and consumer;
- long-term accessibility independent of the original software;
- independent implementation of both producers and consumers.

This is a reference architecture and interoperability disclosure, not a product bundle and not a claim that all possible transformations, semantic rules, or analyses are implemented.

## 2. Components

### 2.1 XBRL GL Next Taxonomy Framework

The XBRL GL Next Taxonomy Framework defines related accounting and transactional semantics and supports machine-readable representation. It supplies a semantic foundation; it is not implemented or redefined by LedgerExplorer.

### 2.2 Universal Adapter for Data Conversion

Universal Adapter for Data Conversion (UADC) transforms application-specific representations into standardized semantic representations and may support transformations in the opposite direction where a binding defines them. Its responsibilities include source syntax handling and the application of syntax and semantic bindings.

This document does not redefine UADC's internal implementation. Only the boundary visible to LedgerExplorer is specified.

### 2.3 Structured CSV

Structured CSV is the common semantic data contract. It is not merely an ad hoc flat export: it can carry stable semantic identifiers, hierarchy/occurrence structure, values, and explicit relationship identifiers. Physical profiles and related tables must document the exact columns they use.

### 2.4 LedgerExplorer

LedgerExplorer consumes, visualizes, validates, traces, and analyzes the standardized representation within its implemented boundaries. It begins at the Structured CSV layer and does not parse the original accounting application format.

## 3. Separation of Responsibilities

Figure 1 summarizes the separation. Each verb identifies a primary responsibility, not ownership of the other components.

**Figure 1 — Define, transform, exchange, and use**

```text
XBRL GL Next Taxonomy Framework = Define
                  |
                  v
UADC                              = Transform
                  |
                  v
Structured CSV                    = Exchange / Preserve
                  |
                  v
LedgerExplorer                    = Use
```

Table 1 refines the boundary based on the current public LedgerExplorer implementation. UADC details outside that observable boundary remain governed by UADC's own specifications.

**Table 1 — Transformation and consumption boundary**

| Function | UADC boundary | LedgerExplorer boundary |
|---|---:|---:|
| Source-specific parsing | Yes | No |
| Syntax Binding | Yes | No |
| Semantic Binding | Yes | No |
| Structured CSV generation | Yes | Python tools can repartition and derive views, but do not perform source-specific UADC binding |
| Structured CSV consumption | Optional verification | Yes |
| Runtime taxonomy/HMD/`semantic_path` resolution | Binding concern | Not implemented in current viewer |
| Visualization and navigation | No core requirement | Yes |
| Relationship and selected reconciliation checks | No core requirement | Yes, for published sample flows |
| General accounting or AI analysis | Separate extension | LedgerExplorer extension point; not generally implemented |

## 4. End-to-End Architecture

Figure 2 shows multiple producers and consumers. UADC is a reusable producer path, while Structured CSV is the decoupling point.

**Figure 2 — End-to-end open architecture**

```text
ERP A ---------+
ERP B ---------+
Flat CSV ------+
UBL -----------+
CII -----------+--- UADC --- Structured CSV --- LedgerExplorer
XBRL GL -------+                    |
                                    +--- Audit tools
                                    +--- Analytics
                                    +--- Archive / search tools
                                    `--- AI-assisted review (extension)
```

The formats on the left illustrate source categories that a suitable binding could support. Their appearance in Figure 2 does not assert that every binding is implemented in the referenced LedgerExplorer repository.

## 5. Independence from Source and Consumer

The producer does not need to know LedgerExplorer. LedgerExplorer does not need to know the original source format.

Consequently:

- UADC can produce Structured CSV for consumers other than LedgerExplorer;
- another converter can produce the same documented Structured CSV contract;
- LedgerExplorer can consume conforming data without knowing which converter produced it;
- another application can consume the same Structured CSV without reproducing LedgerExplorer's UI.

This is interface-level interoperability. It requires a precise profile, compatible semantics, identifiers, encoding, dates, hierarchy/occurrence rules, and relationship rules; the filename extension alone is insufficient.

## 6. Semantic Contract

The semantic contract has both physical and semantic aspects.

Table 2 describes the information needed for reliable exchange. Some aspects are present in the current sample; others require a producer profile or future integration contract.

**Table 2 — Structured CSV semantic contract**

| Contract aspect | Purpose | Current LedgerExplorer status |
|---|---|---|
| Physical encoding and CSV rules | Deterministic parsing and exchange | UTF-8/BOM-tolerant parsing; quoted CSV supported |
| Stable field identifiers | Connect values to known accounting concepts | Current sample uses codes such as `JP07a`, `JP08a`, and related XBRL GL-derived identifiers |
| Hierarchy and occurrence | Preserve repeated groups and sparse structured rows | Current raw splitter preserves order and sparse layout; general HMD interpretation is not implemented |
| Business identifiers | Join journal, document, open-item, and settlement records | Explicit IDs and link tables are implemented in the sample |
| Taxonomy-related meaning | Associate fields with formal semantics | LHM definitions assist the Python path; runtime taxonomy resolution is not implemented |
| `semantic_path` | Portable semantic mapping key | Related design boundary; formal end-to-end mapping is not yet implemented here |
| Provenance | Distinguish original, converted, derived, and displayed data | Architectural requirement; repository documents authoritative sample inputs and derived tables |

The contract must distinguish semantic identity from display labels. Japanese and English sample data share identifiers, dates, codes, amounts, and relationships while localized display strings may differ.

## 7. Current Example Data Route

The public repository demonstrates the following confirmed route:

**Figure 3 — Published sample consumption route**

```text
Existing accounting Structured CSV snapshot
        |
        v
LedgerExplorer Python preparation/export tools
        |
        +--- monthly Structured CSV (raw rows preserved)
        +--- journal / ledger / trial balance / BS / PL views
        `--- published document and settlement relation tables
                    |
                    v
            LedgerExplorer static browser
```

The repository README states that recreating the authoritative Structured CSV snapshots from the original PCA Accounting export is outside this repository's rebuild scope. Therefore the public LedgerExplorer repository does not, by itself, demonstrate a complete current UADC conversion from that proprietary source. A future verified route may be documented as:

```text
Accounting application representation
        -> UADC with explicit bindings
        -> XBRL GL Next-based Structured CSV profile
        -> LedgerExplorer
```

That route becomes an implementation claim only when the producer, binding, inputs, outputs, and verification evidence are published or otherwise traceable.

## 8. Processing and Trust Boundaries

Figure 4 identifies the main trust transitions.

**Figure 4 — Transformation, publication, and consumption boundaries**

```text
[Source-system trust domain]
        |
        | export / evidence
        v
[UADC transformation boundary]
        |
        | validated Structured CSV + provenance
        v
[Publication or controlled exchange boundary]
        |
        | untrusted external input to consumer
        v
[LedgerExplorer validation and presentation boundary]
```

Transformation success does not make data trustworthy by itself. Producers should report bindings, source identity, conversion settings, rejected records, and output hashes. Consumers should validate the expected profile, identifiers, relationships, amounts, and acceptable resource limits. Original evidence and derived Structured CSV must remain distinguishable.

Neither UADC nor LedgerExplorer should treat data fields as executable content. A consumer must escape rendered content and keep private datasets outside public paths.

## 9. Long-Term Data Architecture

The long-term pattern combines:

```text
Original system data or authoritative evidence
        +
documented standardized semantic representation
        +
independent consuming tools
```

The standardized representation reduces the need to preserve the original application's UI or proprietary database solely to understand exported transactions. Semantic identifiers and explicit relationships support migration, search, audit preparation, and new analysis.

This architecture does not assert that Structured CSV is a legal original or that it can replace retention obligations. Provenance, authoritative evidence, applicable law, and organizational controls remain separate concerns.

## 10. Other Producers and Consumers

LedgerExplorer is one consumer, and UADC is one producer approach. Figure 5 shows the intended openness.

**Figure 5 — Replaceable producers and consumers**

```text
UADC -------------------+
Other conforming tool --+--> Structured CSV --> LedgerExplorer
                                             --> Audit application
                                             --> Analytics tool
                                             --> Archive / search tool
                                             `-> AI-assisted analysis (extension)
```

Conformance is established through the documented contract and verification, not by product name. Specialized consumers may implement only a defined profile, provided they declare that scope.

## 11. Reference Implementation, Not Product Lock-In

UADC and LedgerExplorer demonstrate interoperability through published boundaries. They are not intended to create a proprietary platform that requires a particular vendor, programming language, database, UI framework, or cloud service.

The reference implementation is valuable because its code and sample relationships make the architecture inspectable. Independent implementations remain possible when they preserve the same documented semantics and contract. The architecture therefore favors replaceable transformation and consumption components over product lock-in.

## 12. Compatibility and Extension Rules

Extensions should:

- identify the exact Structured CSV profile and version;
- preserve stable semantic and business identifiers;
- state whether hierarchy and occurrence are physical, semantic, or derived;
- distinguish source values from normalized and calculated values;
- publish validation failures rather than silently repairing them;
- keep UADC-specific conversion logic out of consumer presentation code;
- keep LedgerExplorer-specific display assumptions out of the common exchange contract;
- label planned taxonomy, HMD, `semantic_path`, audit, and AI capabilities as extensions until implemented and verified.

These rules preserve the central design: conversion technology and consuming applications are decoupled by a common semantic representation.
