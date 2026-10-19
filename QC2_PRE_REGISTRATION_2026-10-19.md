# QC-2 Pre-Registration: EUR/CAD Nomination
**Date sealed:** 2026-10-19  
**Protocol ID:** QC-FX-2-EURCAD-2026-10-19  
**Author:** Steven Bwenje  
**Status:** SEALED — criterion may not be modified after this date  
**SHA-256:** `7454b53e586609f45c75c438c5454cedccc5f263e139881bdc701604fbf92e1a`

---

## 1. Purpose

Replace the AUD/CAD slot vacated by QC-1 (holdout negative, 2026-10-18).  
AUD/CAD was dropped after the QC-1 holdout returned −$6.30 USDT. This document
nominates EUR/CAD as the replacement candidate and pre-states the acceptance
criterion before any fold P&L results are examined.

---

## 2. Candidate

**Pair:** EUR/CAD  
**Selection basis:** Regime data only — no fold P&L examined before sealing.

| Metric | Value | Basis |
|---|---|---|
| Regime (scanner, 2026-10-19) | RANGING | ADX-14 = 17.3 |
| ADX | 17.3 | Well inside RANGING (< 20) |
| ATR% | 0.094% | Similar to existing portfolio pairs |
| Z-score (20-bar) | −0.96 | Neutral-to-favorable for ranging entry |
| Central banks | ECB vs BoC | Independent from all existing portfolio pairs |
| Currency overlap with GBP/JPY | None | No GBP, no JPY |
| Data available | 30,903 rows | EUR_CAD_1h.csv, 2021-10-19 → 2026-10-06 |

**Why not others:**
- **NZD/CAD**: NZD already in portfolio (NZD/CHF); CAD already in dropped pair
- **CAD/CHF**: ADX 24.2 borderline; double currency overlap (CAD + CHF)
- **AUD/USD**: AUD was the failing leg in AUD/CAD — carryover risk
- **EUR/GBP**: GBP overlaps with validated GBP/JPY

---

## 3. Acceptance Criterion (SEALED)

Evaluated on holdout window data **only** (2026-10-19 → 2027-01-17).  
Historical fold data (2021-2026) is **descriptive only** per CRRP-2026-10-02
and cannot change the pass/fail outcome.

**PASS:** `total_pnl_usdt > 0` AND `total_trades ≥ 15`  
**FAIL:** `total_pnl_usdt ≤ 0` OR `total_trades < 15`

This criterion is binary. No partial pass. No post-hoc adjustment.

---

## 4. Holdout Window

| | |
|---|---|
| **Start** | 2026-10-19 (this document's seal date) |
| **End (minimum)** | 2027-01-17 (90 days) |
| **Data source** | MT5 candles appended hourly by `refresh_forex_data` |
| **Evaluation date** | No earlier than 2027-01-17 |

Results must not be inspected before the end date. The window may be extended
(never shortened) if fewer than 15 trades have closed by 2027-01-17.

---

## 5. Relationship to Other Pre-Registered Rules

- **Full-ladder stop rule** (pre-registered 2026-10-18, `FULL_LADDER_STOP_RULE_PRE_REGISTRATION_2026-10-18.md`):
  applies independently to GBP/JPY; K selection locked until 2027-01-17.  
  The two pre-registrations share the same minimum window end date by coincidence —
  they are evaluated independently.
- **CRRP-2026-10-02:** All data prior to 2026-10-19 is pseudo-holdout.
  Historical fold results for EUR/CAD are descriptive context, not evidence.

---

## 6. What Happens After Sealing

1. EUR/CAD added to `QC_PORTFOLIO_SPECS` and `QC_PORTFOLIO_CSV_FILES` immediately.
2. `refresh_forex_data` appends live EUR/CAD 1h candles hourly (already in MT5 universe).
3. Paper engine begins tracking EUR/CAD positions alongside existing pairs.
4. On 2027-01-17 (earliest): open holdout, evaluate against criterion above.
5. If PASS: EUR/CAD enters the live portfolio alongside GBP/JPY.
6. If FAIL: EUR/CAD dropped; a QC-3 nomination follows the same process.
