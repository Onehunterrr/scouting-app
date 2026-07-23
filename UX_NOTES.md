# UI/UX Notes — Global Lower-Tier Scouting Prototype

A running list of user-experience observations and recommendations. Items marked
**[done]** are already shipped; the rest are prioritized suggestions.

## Shipped this round
- **[done] RevOps Insights panel** — a collapsible section above the table with three live cards:
  - *Data Health scan* — grades the whole database (A/B/C + %) on completeness: missing market values, unknown representation, missing club contacts, stale records, and duplicate-review candidates.
  - *Pipeline Funnel* — flag tiers reframed as pipeline stages with stage-to-stage conversion %.
  - *Routing Engine* — the contact-assignment rules shown explicitly as IF/THEN with live counts.
- **[done] "Data confidence" chip** in the summary bar — surfaces the data-health grade at a glance and color-codes it (green/amber/red). Reinforces the "confidence in your pipeline" idea.

## High-value next (low effort)
1. **Debounce search & filter re-renders.** At 5,000 records the offline mode recomputes scores on every keystroke. A ~150 ms debounce keeps typing smooth and is a one-function change. (In API mode this is already server-side.)
2. **Sticky filter/summary header.** When you scroll a long table, the filters and the insights scroll away. Making the summary bar + toolbar sticky keeps context in view.
3. **Empty/loading states with personality.** The table shows a plain "no players match" — add a one-line hint ("try widening the age range or clearing the country filter") and a subtle skeleton/spinner during API fetches so it never looks frozen.
4. **Column visibility toggle.** 12 columns is a lot on smaller screens. Let users hide columns they don't need; remember the choice in localStorage.

## Medium effort, high polish
5. **Saved views / filter presets.** Let a logged-in user name and save a filter+weight combination ("Young unrepresented GKs, tier 2") and reload it in one click — this is a real RevOps pattern (saved segments).
6. **Bulk actions on the table.** Select multiple rows to shortlist or export at once, not just one-by-one.
7. **Keyboard shortcut cheatsheet.** A small "?" overlay listing the shortcuts (arrows / Enter / /). The nav exists; make it discoverable.
8. **Density toggle.** A "comfortable / compact" row-height switch for power users scanning thousands of rows.

## Visual / accessibility
9. **Contrast + focus audit.** The dark "Deep Ember" palette looks great; run a formal WCAG contrast pass on the muted greys, and confirm every interactive control has a visible focus ring (most do).
10. **Consistent number formatting.** Money is nicely formatted; make percentiles, ages, and counts consistent (thousands separators everywhere, fixed decimals).
11. **Tooltips on jargon.** Terms like "Undervalued Score," "System Fit," and "Sweeper Actions" would benefit from hover tooltips for first-time viewers (helpful in a demo/interview).

## Nice-to-have / storytelling
12. **A one-screen "SCAN report" export** — package the Data Health card + funnel into a shareable one-pager (mirrors the RevBlack SCAN → ROADMAP flow).
13. **Onboarding highlight tour** — a 4-step first-visit walkthrough pointing at filters, scoring weights, the insights panel, and a player profile.

_Last updated: with the 5,000-player + RevOps Insights release._
