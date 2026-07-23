/*
 * smoke_test_v4.js -- jsdom smoke test for the v4 client-server upgrade
 * (offline mode; the API paths are covered by test_api.py + live curl).
 *
 * Covers: search matching club and country, pagination controls, CSV export
 * builders, compare-modal radar SVG, and the model-transparency panel.
 *
 * Run: node smoke_test_v4.js
 */
const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "Scouting_App_Prototype.html"), "utf8");
const players = JSON.parse(fs.readFileSync(path.join(__dirname, "players_current.json"), "utf8"));

let passed = 0;
function assert(cond, msg) {
  if (!cond) throw new Error("ASSERTION FAILED: " + msg);
  passed++;
  console.log("  OK: " + msg);
}

const errors = [];
const dom = new JSDOM(html, { runScripts: "dangerously", pretendToBeVisual: true });
dom.window.onerror = m => errors.push(m);
const w = dom.window;
const document = w.document;

const rows = () => [...document.querySelectorAll("#table-body tr")];
function setSearch(v) {
  const s = document.getElementById("f-search");
  s.value = v;
  s.dispatchEvent(new w.Event("input", { bubbles: true }));
}
function click(el) { el.dispatchEvent(new w.Event("click", { bubbles: true })); }

console.log("\n--- 1. Pagination controls ---");
const totalPages = Math.ceil(players.length / 50);
assert(rows().length === 50, "default page size is 50 rows");
assert(document.getElementById("page-info").textContent === `Page 1 of ${totalPages}`,
  `pager reads 'Page 1 of ${totalPages}'`);
const firstNamePage1 = rows()[0].querySelector("td.name").textContent;
click(document.getElementById("page-next"));
assert(document.getElementById("page-info").textContent === `Page 2 of ${totalPages}`, "Next advances to page 2");
assert(rows()[0].querySelector("td.name").textContent !== firstNamePage1, "page 2 shows different rows");
click(document.getElementById("page-prev"));
assert(document.getElementById("page-info").textContent.startsWith("Page 1 of"), "Prev returns to page 1");
const ps = document.getElementById("page-size");
ps.value = "25";
ps.dispatchEvent(new w.Event("change", { bubbles: true }));
assert(rows().length === 25, "page-size select 25 shows 25 rows");
ps.value = "all";
ps.dispatchEvent(new w.Event("change", { bubbles: true }));
assert(rows().length === players.length, `page-size 'All' (offline) shows all ${players.length} rows`);
assert(document.getElementById("page-info").textContent === "Page 1 of 1", "pager collapses to 'Page 1 of 1' on All");

console.log("\n--- 2. Search matches club and country ---");
const hay = p => (p.name + " " + p.club + " " + p.country).toLowerCase();
const anchor = players[0];
const clubTok = anchor.club.split(" ")[0].toLowerCase();
const clubExpected = players.filter(p => hay(p).includes(clubTok)).length;
setSearch(clubTok);
assert(clubExpected > 0 && rows().length === clubExpected,
  `club search '${clubTok}' returns exactly the ${clubExpected} matching players`);
assert(rows().some(r => r.querySelector("td.name").textContent.includes(anchor.name)),
  `club search finds ${anchor.name} (club: ${anchor.club})`);
const countryTok = anchor.country.toLowerCase();
const countryExpected = players.filter(p => hay(p).includes(countryTok)).length;
setSearch(countryTok);
assert(rows().length === countryExpected,
  `country search '${countryTok}' returns exactly the ${countryExpected} matching players`);
assert(rows().some(r => r.children[4].textContent === anchor.country),
  "country search results include rows from that country");
setSearch("");

console.log("\n--- 3. CSV export builders ---");
const EXPECTED_HEADER = "name,position,country,tier,age,marketValue,hasAgent,contractExpires," +
  "undervaluedScore,flag,club,league,systemFit,displayMarketValue,marketValueEstimated";
function parseCsvLine(line) {
  const fields = [];
  let cur = "", inQ = false;
  for (let i = 0; i < line.length; i++) {
    const c = line[i];
    if (inQ) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++; }
      else if (c === '"') inQ = false;
      else cur += c;
    } else if (c === '"') inQ = true;
    else if (c === ",") { fields.push(cur); cur = ""; }
    else cur += c;
  }
  fields.push(cur);
  return fields;
}
const viewCsv = w.buildViewCsv();
const viewLines = viewCsv.split("\n");
assert(viewLines[0] === EXPECTED_HEADER, "view CSV header has the full column set");
assert(viewLines.length === players.length + 1, `view CSV has header + ${players.length} data rows`);
const nCols = EXPECTED_HEADER.split(",").length;
assert(viewLines.every(l => parseCsvLine(l).length === nCols),
  `every view CSV row parses to exactly ${nCols} fields (well-formed quoting)`);
const sampleFields = parseCsvLine(viewLines[1]);
assert(!isNaN(parseFloat(sampleFields[8])), "undervaluedScore column is numeric in CSV");
assert(sampleFields[14] === "yes" || sampleFields[14] === "no", "marketValueEstimated serialised as yes/no");

// shortlist CSV: star two players through the UI, then build
click(rows()[0].querySelector(".star-btn"));
click(rows()[1].querySelector(".star-btn"));
const slCsv = w.buildShortlistCsv();
const slLines = slCsv.split("\n");
assert(slLines.length === 3, "shortlist CSV has header + the 2 starred players");
assert(slLines[0] === EXPECTED_HEADER, "shortlist CSV shares the same header");
assert(slLines.every(l => parseCsvLine(l).length === nCols), "shortlist CSV rows are well-formed");

console.log("\n--- 4. Compare radar chart ---");
const cb0 = rows()[0].querySelector(".compare-cb");
cb0.checked = true;
cb0.dispatchEvent(new w.Event("change", { bubbles: true }));
const cb1 = rows()[1].querySelector(".compare-cb");
cb1.checked = true;
cb1.dispatchEvent(new w.Event("change", { bubbles: true }));
const name0 = rows()[0].querySelector("td.name").textContent.replace("NEW", "").trim();
const name1 = rows()[1].querySelector("td.name").textContent.replace("NEW", "").trim();
click(document.getElementById("compare-view-btn"));
assert(document.getElementById("compare-modal-overlay").classList.contains("open"), "compare modal opens");
const radarSvg = document.querySelector("#compare-radar-wrap svg");
assert(radarSvg !== null, "radar SVG rendered in the compare modal");
const polys = document.querySelectorAll("#compare-radar-wrap svg .radar-poly");
assert(polys.length === 2, `radar has exactly one polygon per compared player (got ${polys.length} for 2 players)`);
assert(radarSvg.querySelectorAll("text").length === 5, "radar has 5 axis labels");
const legendText = document.querySelector("#compare-radar-wrap .radar-legend").textContent;
assert(legendText.includes(name0) && legendText.includes(name1), "radar legend names both players");
assert(document.querySelector("#compare-table-wrap table") !== null, "comparison table still present below the radar");
click(document.getElementById("compare-modal-close"));

// 3 players -> 3 polygons
const cb2 = rows()[2].querySelector(".compare-cb");
cb2.checked = true;
cb2.dispatchEvent(new w.Event("change", { bubbles: true }));
click(document.getElementById("compare-view-btn"));
assert(document.querySelectorAll("#compare-radar-wrap svg .radar-poly").length === 3,
  "radar scales to 3 polygons for 3 players");
click(document.getElementById("compare-modal-close"));

console.log("\n--- 5. Model transparency panel ---");
click(rows()[0]);
assert(document.getElementById("modal-overlay").classList.contains("open"), "player modal opens");
const det = document.getElementById("m-transparency");
assert(det !== null && det.tagName === "DETAILS", "collapsible transparency panel present in modal");
assert(det.querySelector("summary").textContent.includes("How this score was calculated"),
  "panel is titled 'How this score was calculated'");
assert(!det.open, "panel starts collapsed");
det.open = true;
assert(det.open, "panel opens");
const factorRows = det.querySelectorAll(".transparency-table tbody tr");
assert(factorRows.length === 4, "panel shows all 4 scoring factors");
const headCells = [...det.querySelectorAll(".transparency-table thead th")].map(t => t.textContent);
assert(JSON.stringify(headCells) === JSON.stringify(["Factor", "Raw", "Percentile", "Weight", "Contribution"]),
  "factor table shows raw -> percentile -> weight -> contribution");
assert(det.textContent.includes("tier multiplier"), "panel shows the tier multiplier step");
const formula = document.getElementById("m-uv-formula");
assert(formula !== null, "final formula line present");
const ftext = formula.textContent;
assert(ftext.includes("Undervalued Score = (performance percentile") && ftext.includes("100"),
  "formula line spells out (performancePct - marketPct) x 100");
const shownScore = document.querySelector("#m-explain b").textContent;   // "Undervalued Score: X.Y"
const scoreVal = shownScore.replace("Undervalued Score:", "").trim();
assert(ftext.trim().endsWith(scoreVal), `formula substitutes the player's actual numbers (ends with ${scoreVal})`);
click(document.getElementById("modal-close"));

assert(errors.length === 0, `no uncaught JS errors (${errors.length}: ${errors.join("; ")})`);

console.log(`\nALL SMOKE TESTS PASSED (${passed} assertions).`);
dom.window.close();
