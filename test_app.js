const fs = require("fs");
const path = require("path");
const { JSDOM } = require("jsdom");

const html = fs.readFileSync(path.join(__dirname, "Scouting_App_Prototype.html"), "utf8");

function newDom() {
  const errors = [];
  const dom = new JSDOM(html, {
    runScripts: "dangerously",
    resources: "usable",
    pretendToBeVisual: true,
  });
  dom.window.onerror = (msg) => errors.push(msg);
  return { dom, errors };
}

function assert(cond, msg) {
  if (!cond) throw new Error("ASSERTION FAILED: " + msg);
  console.log("  OK: " + msg);
}

function rowCount(doc) {
  return doc.querySelectorAll("#table-body tr").length;
}

function runPass(passNum) {
  console.log(`\n=== PASS ${passNum} ===`);
  const { dom, errors } = newDom();
  const { document } = dom.window;
  const w = dom.window;

  const totalPlayers = 1000;

  // 0. Pagination default: offline mode starts at 50/page, page 1
  const expectedFirstPage = Math.min(50, totalPlayers);
  assert(rowCount(document) === expectedFirstPage, `initial render shows first page of ${expectedFirstPage} rows (50/page default)`);
  assert(document.getElementById("stat-count").textContent === String(totalPlayers), "stat-count shows the FULL filtered total, not just the page");
  assert(document.getElementById("page-info").textContent.startsWith("Page 1 of"), "pager shows 'Page 1 of ...' on load");

  // switch the page size to "All" (offline-only option) so every subsequent
  // assertion sees the complete result set, exactly like the pre-pagination app
  const pageSizeSel = document.getElementById("page-size");
  pageSizeSel.value = "all";
  pageSizeSel.dispatchEvent(new w.Event("change", { bubbles: true }));

  // 1. Initial render
  assert(rowCount(document) === totalPlayers, `initial render shows all ${totalPlayers} players`);
  assert(document.getElementById("stat-count").textContent === String(totalPlayers), "stat-count matches row count");
  const countryOptions = document.querySelectorAll("#f-country option").length;
  assert(countryOptions === 33, `country filter populated (32 countries + 'All' = 33 options), got ${countryOptions}`);

  // 2. Default sort is Undervalued Score desc -> first row numeric
  const firstRowScoreText = document.querySelector("#table-body tr td.uv-score").textContent;
  assert(!isNaN(parseFloat(firstRowScoreText)), "first row undervalued score is numeric: " + firstRowScoreText);

  // 3. Name sort toggle
  const nameHeader = [...document.querySelectorAll("thead th")].find(th => th.dataset.key === "name");
  nameHeader.dispatchEvent(new w.Event("click", { bubbles: true }));
  const firstNameDesc = document.querySelector("#table-body tr td.name").textContent;
  nameHeader.dispatchEvent(new w.Event("click", { bubbles: true }));
  const firstNameAsc = document.querySelector("#table-body tr td.name").textContent;
  assert(firstNameAsc !== firstNameDesc, `sort toggle changes order (desc first="${firstNameDesc}", asc first="${firstNameAsc}")`);

  const uvHeader = [...document.querySelectorAll("thead th")].find(th => th.dataset.key === "undervaluedScore");
  uvHeader.dispatchEvent(new w.Event("click", { bubbles: true }));

  // 4. Position filter
  const posSelect = document.getElementById("f-position");
  posSelect.value = "FW";
  posSelect.dispatchEvent(new w.Event("input", { bubbles: true }));
  const fwRows = rowCount(document);
  assert(fwRows > 0 && fwRows < totalPlayers, `position filter (FW) narrows results: ${fwRows} rows`);
  const allFW = [...document.querySelectorAll("#table-body tr")].every(tr => tr.children[3].textContent === "FW");
  assert(allFW, "all visible rows are FW after position filter");

  // 5. Country filter stacked on position
  const countrySelect = document.getElementById("f-country");
  const someCountry = [...document.querySelectorAll("#f-country option")][1].value;
  countrySelect.value = someCountry;
  countrySelect.dispatchEvent(new w.Event("input", { bubbles: true }));
  const stackedRows = rowCount(document);
  assert(stackedRows <= fwRows, `country filter further narrows results: ${stackedRows} rows`);

  // 6. Reset filters
  document.getElementById("reset-filters").dispatchEvent(new w.Event("click", { bubbles: true }));
  assert(rowCount(document) === totalPlayers, "reset filters restores all players");

  // 7. Search filter (dynamically chosen unique name from current dataset)
  const search = document.getElementById("f-search");
  search.value = "godoy";
  search.dispatchEvent(new w.Event("input", { bubbles: true }));
  assert(rowCount(document) === 1, `search 'godoy' returns exactly 1 row, got ${rowCount(document)}`);
  assert(document.querySelector("#table-body tr td.name").textContent.includes("Michael Godoy"), "search result is correct player");
  search.value = "";
  search.dispatchEvent(new w.Event("input", { bubbles: true }));

  // 8. Age slider filter
  const ageSlider = document.getElementById("f-age");
  ageSlider.value = "18";
  ageSlider.dispatchEvent(new w.Event("input", { bubbles: true }));
  const allUnder18 = [...document.querySelectorAll("#table-body tr")].every(tr => parseInt(tr.children[6].textContent, 10) <= 18);
  assert(allUnder18, "age filter (<=18) respected");
  ageSlider.value = "26";
  ageSlider.dispatchEvent(new w.Event("input", { bubbles: true }));

  // 9. Agent checkbox filter
  const noCb = [...document.querySelectorAll(".agent-cb")].find(cb => cb.value === "No");
  noCb.checked = false;
  noCb.dispatchEvent(new w.Event("change", { bubbles: true }));
  const noAgentHidden = [...document.querySelectorAll("#table-body tr")].every(tr => {
    const tag = tr.querySelector(".agent-tag");
    return tag.textContent.trim() !== "No";
  });
  assert(noAgentHidden, "unchecking 'No agent' hides all unrepresented players");
  noCb.checked = true;
  noCb.dispatchEvent(new w.Event("change", { bubbles: true }));
  assert(rowCount(document) === totalPlayers, "re-checking restores all players");

  // 10. Weight sliders change scores
  const gaSlider = document.getElementById("w-ga");
  gaSlider.value = "80";
  gaSlider.dispatchEvent(new w.Event("input", { bubbles: true }));
  const progSlider = document.getElementById("w-prog");
  progSlider.value = "5";
  progSlider.dispatchEvent(new w.Event("input", { bubbles: true }));
  assert(document.getElementById("w-ga-val").textContent === "80%", "weight label updates to 80%");

  // 11. Reset weights
  document.getElementById("reset-weights").dispatchEvent(new w.Event("click", { bubbles: true }));
  assert(document.getElementById("w-ga-val").textContent === "25%", "reset weights restores GA to 25%");

  // 12. Open modal
  const firstRow = document.querySelector("#table-body tr");
  const clickedName = firstRow.querySelector("td.name").textContent.replace("NEW", "").trim();
  firstRow.dispatchEvent(new w.Event("click", { bubbles: true }));
  assert(document.getElementById("modal-overlay").classList.contains("open"), "modal opens on row click");
  assert(document.getElementById("m-name").textContent === clickedName, "modal shows correct player name: " + clickedName);
  const barBlocks = document.querySelectorAll("#m-bars .bar-block").length;
  assert(barBlocks === 6, `modal shows 6 stat bars, got ${barBlocks}`);
  const explainText = document.getElementById("m-explain").textContent;
  assert(explainText.includes("Undervalued Score"), "modal explanation mentions Undervalued Score");

  // 12b. Date Added / Last Updated pills
  const pillsHtml = document.getElementById("m-pills").innerHTML;
  assert(pillsHtml.includes("Added"), "Date Added pill present");
  assert(pillsHtml.includes("Updated"), "Last Updated pill present");

  // 12c. Tactical System Fit section (separate from Contact & Verification)
  const systemHtml = document.getElementById("m-system").innerHTML;
  assert(systemHtml.includes("Tactical System Fit"), "system fit section header present");
  assert(document.querySelector("#m-system .system-badge") !== null, "system fit badge rendered");
  assert(document.querySelector("#m-system .system-note").textContent.length > 20, "system fit rationale text rendered");

  // 12d. Contact & Verification section
  const contactHtml = document.getElementById("m-contact").innerHTML;
  assert(contactHtml.includes("Contact &amp; Verification"), "contact section header present");
  const mailLink = document.querySelector("#m-contact a[href^='mailto:']");
  assert(mailLink !== null, "club contact email mailto link present");
  const tmLink = document.querySelector("#m-contact a[href*='transfermarkt.com']");
  assert(tmLink !== null, "Transfermarkt verification link present");
  assert(tmLink.getAttribute("href").includes("schnellsuche"), "Transfermarkt link is a live search, not a fabricated profile URL");
  assert(contactHtml.includes("Preferred route"), "preferred contact route shown");
  assert(contactHtml.includes("Also check"), "federation registry cross-check shown");

  // 13. Close modal via close button
  document.getElementById("modal-close").dispatchEvent(new w.Event("click", { bubbles: true }));
  assert(!document.getElementById("modal-overlay").classList.contains("open"), "modal closes via close button");

  // 14. Open modal again, close via overlay click
  firstRow.dispatchEvent(new w.Event("click", { bubbles: true }));
  const overlay = document.getElementById("modal-overlay");
  const overlayClickEvent = new w.MouseEvent("click", { bubbles: true });
  Object.defineProperty(overlayClickEvent, "target", { value: overlay });
  overlay.dispatchEvent(overlayClickEvent);
  assert(!overlay.classList.contains("open"), "modal closes via overlay click");

  // 15. Tier filter
  const tierSelect = document.getElementById("f-tier");
  tierSelect.value = "4";
  tierSelect.dispatchEvent(new w.Event("input", { bubbles: true }));
  const tierRows = rowCount(document);
  const allTierMatch = [...document.querySelectorAll("#table-body tr")].every(tr => tr.children[5].textContent === "4");
  assert(allTierMatch && tierRows > 0, `tier filter (4) works, ${tierRows} rows all tier 4`);
  document.getElementById("reset-filters").dispatchEvent(new w.Event("click", { bubbles: true }));

  // 16. Cross-check computed Undervalued Score against known spreadsheet values (from recalculated .xlsx)
  const expected = {"Wesly Figueroa": 77.5956284153005, "Romell Palacios": 14.7540983606557, "Nika Kapanadze": -19.3877551020408, "Ened Krasniqi": -96.969696969697};
  const rows = [...document.querySelectorAll("#table-body tr")];
  for (const [name, exp] of Object.entries(expected)) {
    const tr = rows.find(r => r.querySelector("td.name").textContent.includes(name));
    assert(tr !== undefined, `player "${name}" exists in rendered table`);
    const got = parseFloat(tr.querySelector("td.uv-score").textContent);
    assert(Math.abs(got - exp) < 0.5, `${name}: undervalued score ${got} matches spreadsheet ${exp}`);
  }

  // 17. Flag cross-check
  const flagExpected = {"Wesly Figueroa": "High Priority - Unrepresented"};
  for (const [name, exp] of Object.entries(flagExpected)) {
    const tr = rows.find(r => r.querySelector("td.name").textContent.includes(name));
    const badge = tr.querySelector(".badge");
    assert(badge.textContent === exp, `${name}: flag "${badge.textContent}" matches expected "${exp}"`);
  }

  // 17b. System Fit cross-check against recalculated spreadsheet
  const sysRow = rows.find(r => r.querySelector("td.name").textContent.includes("Hoang Le"));
  assert(sysRow !== undefined, "system fit test subject exists in table");
  sysRow.dispatchEvent(new w.Event("click", { bubbles: true }));
  const sysBadgeText = document.querySelector("#m-system .system-badge").textContent;
  assert(sysBadgeText === "High Press / Gegenpressing", `system fit "${sysBadgeText}" matches spreadsheet "High Press / Gegenpressing"`);
  document.getElementById("modal-close").dispatchEvent(new w.Event("click", { bubbles: true }));

  
  // 18. Minor-safety warning
  const minorRow = rows.find(r => r.querySelector("td.name").textContent.includes("Hoang Le"));
  assert(minorRow !== undefined, "minor test subject exists in table");
  minorRow.dispatchEvent(new w.Event("click", { bubbles: true }));
  const minorContactHtml = document.getElementById("m-contact").innerHTML;
  assert(minorContactHtml.includes("minor-warning"), "minor warning block rendered for a player under 18");
  assert(minorContactHtml.includes("youth/academy office"), "minor route points to club youth office, not direct contact");
  document.getElementById("modal-close").dispatchEvent(new w.Event("click", { bubbles: true }));

  
  // 19. Agent-routing
  const agentedRow = rows.find(r => r.querySelector("td.name").textContent.includes("Mustafa Mahmoud"));
  assert(agentedRow !== undefined, "agented test subject exists in table");
  agentedRow.dispatchEvent(new w.Event("click", { bubbles: true }));
  const agentedContactHtml = document.getElementById("m-contact").innerHTML;
  assert(agentedContactHtml.includes("player's agent"), "agented player routes through their agent");
  document.getElementById("modal-close").dispatchEvent(new w.Event("click", { bubbles: true }));


  // 20. Country filter option count
  const allCountryOptions = [...document.querySelectorAll("#f-country option")].map(o => o.value).filter(Boolean);
  assert(allCountryOptions.length === 32, `32 countries present in filter, got ${allCountryOptions.length}`);

  // 21. Weekly-growth "NEW" badge: players from the latest batch are tagged
  const newBadges = document.querySelectorAll(".new-badge").length;
  assert(newBadges === 890, `${newBadges} NEW badges rendered, expected 890 (players added ${"2026-07-17"})`);
  const statNew = document.getElementById("stat-new").textContent;
  assert(statNew === "890", `stat-new chip shows 890, got ${statNew}`);

  // 22. Realistic Transfer Targets section
  const anyRow = document.querySelector("#table-body tr");
  anyRow.dispatchEvent(new w.Event("click", { bubbles: true }));
  const transferHtml = document.getElementById("m-transfer").innerHTML;
  assert(transferHtml.includes("Realistic Transfer Targets"), "transfer targets section header present");
  const transferItems = document.querySelectorAll("#m-transfer .transfer-item").length;
  assert(transferItems === 5, `transfer targets shows exactly 5 candidates, got ${transferItems}`);
  const transferRanks = [...document.querySelectorAll("#m-transfer .transfer-rank")].map(el => el.textContent);
  assert(JSON.stringify(transferRanks) === JSON.stringify(["1","2","3","4","5"]), `transfer targets ranked 1-5, got ${transferRanks}`);
  const fitPercents = [...document.querySelectorAll("#m-transfer .transfer-fit b")].map(el => parseInt(el.textContent, 10));
  const isSortedDesc = fitPercents.every((v, i) => i === 0 || fitPercents[i-1] >= v);
  assert(isSortedDesc, `transfer targets sorted by fit % descending: ${fitPercents}`);
  const moveTypeBadges = document.querySelectorAll("#m-transfer .move-type-badge").length;
  assert(moveTypeBadges === 5, "every transfer target has a move-type badge");
  document.getElementById("modal-close").dispatchEvent(new w.Event("click", { bubbles: true }));

  // 22b. Transfer targets are deterministic (stable across modal re-opens for the same player)
  anyRow.dispatchEvent(new w.Event("click", { bubbles: true }));
  const clubsFirstOpen = [...document.querySelectorAll("#m-transfer .transfer-club")].map(el => el.textContent);
  document.getElementById("modal-close").dispatchEvent(new w.Event("click", { bubbles: true }));
  anyRow.dispatchEvent(new w.Event("click", { bubbles: true }));
  const clubsSecondOpen = [...document.querySelectorAll("#m-transfer .transfer-club")].map(el => el.textContent);
  assert(JSON.stringify(clubsFirstOpen) === JSON.stringify(clubsSecondOpen), "transfer targets are stable across repeated modal opens");
  document.getElementById("modal-close").dispatchEvent(new w.Event("click", { bubbles: true }));

  // JS runtime errors check
  assert(errors.length === 0, `no uncaught JS errors during pass (${errors.length} found: ${errors.join("; ")})`);

  dom.window.close();
  console.log(`=== PASS ${passNum} COMPLETE -- all checks passed ===`);
}

/* Run all 3 passes by default; `node test_app.js N` runs pass N alone so the
   three passes can be executed as three separate processes on slow machines
   (identical coverage -- each pass is a fully fresh DOM either way). */
const onlyPass = parseInt(process.argv[2] || "0", 10);
if (onlyPass >= 1 && onlyPass <= 3) {
  runPass(onlyPass);
  console.log(`\nPASS ${onlyPass} PASSED.`);
} else {
  for (let i = 1; i <= 3; i++) {
    runPass(i);
  }
  console.log("\nALL 3 PASSES PASSED.");
}
