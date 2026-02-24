let competitors = [];
let methodology = { scoring_model: [], limitations: [] };
let rubric = [];

let activeName = "roam-code";
let matrixMode = "simple";

// Current category weights (mutable via sliders)
let weights = {};
let defaultWeights = {};

function toneForStatus() {
  return "tone-neutral";
}

// Category bar color mapping
const catColors = {
  static_analysis: "#0f6f95",
  graph_intelligence: "#2a8f8a",
  git_temporal: "#f08c2f",
  agent_integration: "#5f7f9e",
  security_governance: "#c44d56",
  ecosystem: "#ba7d2a",
  unique_capabilities: "#147f57",
};

function loadError(message) {
  const detail = document.getElementById("detail");
  if (detail) {
    detail.innerHTML = `<h3>Unable to load data</h3><p class="tone-bad">${message}</p>`;
  }
}

// ---------------------------------------------------------------------------
// Client-side score recomputation with custom weights
// ---------------------------------------------------------------------------

function recomputeWithWeights(entry) {
  if (!entry.scores || !entry.scores.categories) return entry.scores || {};
  const cats = entry.scores.categories;
  let weightedTotal = 0;
  const newCats = cats.map((cat) => {
    const w = weights[cat.id] || 0;
    const fraction = cat.max > 0 ? cat.score / cat.max : 0;
    weightedTotal += fraction * w;
    return { ...cat };
  });
  const total = Math.round(weightedTotal);
  const byId = {};
  newCats.forEach((c) => { byId[c.id] = c; });
  const sa = byId.static_analysis || { score: 0, max: 20 };
  const gi = byId.graph_intelligence || { score: 0, max: 10 };
  const gt = byId.git_temporal || { score: 0, max: 10 };
  const ai = byId.agent_integration || { score: 0, max: 16 };
  const ea = byId.ecosystem || { score: 0, max: 19 };
  const safe = (n, d) => d ? n / d : 0;
  const mapY = Math.round((safe(sa.score, sa.max) * 0.50 + safe(gi.score, gi.max) * 0.25 + safe(gt.score, gt.max) * 0.25) * 100);
  const mapX = Math.round((safe(ai.score, ai.max) * 0.70 + safe(ea.score, ea.max) * 0.30) * 100);
  return { ...entry.scores, total, map_x: mapX, map_y: mapY, categories: newCats };
}

function getEffectiveScores(entry) {
  return recomputeWithWeights(entry);
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

async function loadSiteData() {
  const response = await fetch("./data/landscape.json", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`HTTP ${response.status} while loading landscape.json`);
  }
  const payload = await response.json();

  competitors = Array.isArray(payload.competitors) ? payload.competitors : [];
  methodology = payload.methodology || methodology;
  rubric = Array.isArray(payload.rubric) ? payload.rubric : [];

  // Initialize weights from rubric
  rubric.forEach((cat) => {
    weights[cat.id] = cat.default_weight;
    defaultWeights[cat.id] = cat.default_weight;
  });

  if (competitors.length > 0 && !competitors.some((entry) => entry.name === activeName)) {
    activeName = competitors[0].name;
  }

  const countEl = document.getElementById("competitor-count");
  if (countEl) {
    countEl.textContent = String(competitors.length);
  }

  const updatedEl = document.getElementById("last-updated");
  if (updatedEl && payload.tracker_updated_iso) {
    updatedEl.textContent = payload.tracker_updated_iso;
  }

  const roam = competitors.find((entry) => entry.name === "roam-code");
  const mcpEl = document.getElementById("roam-mcp-count");
  if (mcpEl && roam) {
    mcpEl.textContent = String(roam.mcp);
  }

  const cliEl = document.getElementById("roam-cli-count");
  if (cliEl && roam) {
    cliEl.textContent = String(roam.cli_commands || "135");
  }
}

// ---------------------------------------------------------------------------
// Landscape map
// ---------------------------------------------------------------------------

function renderMap() {
  const entries = competitors;
  const map = document.getElementById("map-points");
  map.innerHTML = "";

  if (entries.length === 0) return;

  if (!entries.some((entry) => entry.name === activeName)) {
    activeName = entries[0].name;
  }

  // Determine top 5 by score for persistent labels
  const ranked = entries.slice().sort((a, b) => {
    const sa = getEffectiveScores(a), sb = getEffectiveScores(b);
    return sb.total - sa.total || sb.map_y - sa.map_y;
  });
  const top5Names = new Set(ranked.slice(0, 5).map((e) => e.name));

  entries.forEach((entry, index) => {
    const scores = getEffectiveScores(entry);
    const x = scores.map_x;
    const y = scores.map_y;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = `point${entry.name === "roam-code" ? " is-roam" : ""}${entry.name === activeName ? " is-active" : ""}${top5Names.has(entry.name) ? " is-top5" : ""}`;
    btn.style.left = `${x}%`;
    btn.style.bottom = `${y}%`;
    btn.style.animationDelay = `${index * 22}ms`;
    btn.innerHTML = `<span class="dot" aria-hidden="true"></span><span class="label">${entry.name}</span>`;
    btn.title = `${entry.name} | Total ${scores.total}/100 | Depth ${y} | Agent ${x}`;
    btn.onclick = () => {
      activeName = entry.name;
      renderMap();
    };
    map.appendChild(btn);
  });
  renderDetail();
}

// ---------------------------------------------------------------------------
// Detail card
// ---------------------------------------------------------------------------

function renderCategoryBar(cat, color) {
  const pct = cat.max > 0 ? Math.round((cat.score / cat.max) * 100) : 0;
  return `<div class="cat-bar-row">
    <span class="cat-bar-label">${cat.label}</span>
    <span class="cat-bar-score">${cat.score}/${cat.max}</span>
    <div class="cat-bar-track"><div class="cat-bar-fill" style="width:${pct}%;background:${color}"></div></div>
  </div>`;
}

function renderCriteriaTable(criteria) {
  const rows = criteria.map((c) => {
    const val = c.type === "binary" ? (c.value ? "Yes" : "No") : String(c.value);
    const subj = c.type === "subjective" ? ' <span class="subjective-marker">(subjective)</span>' : "";
    return `<tr><td>${c.label}${subj}</td><td>${val}</td><td>${c.points}/${c.max}</td></tr>`;
  }).join("");
  return `<table class="criteria-table"><thead><tr><th>Criterion</th><th>Value</th><th>Points</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function rankedCompetitors() {
  return competitors
    .slice()
    .sort((a, b) => {
      const sa = getEffectiveScores(a), sb = getEffectiveScores(b);
      return sb.total - sa.total || sb.map_y - sa.map_y;
    });
}

function renderDetail() {
  const target = competitors.find((entry) => entry.name === activeName) || competitors[0];
  if (!target) return;

  const host = document.getElementById("detail");
  const scores = getEffectiveScores(target);
  const cats = scores.categories || [];
  const totalPct = Math.round((scores.total / (scores.max_total || 100)) * 100);
  const localLabel = target.local ? "Yes" : "No";

  // Rank + navigation
  const ranked = rankedCompetitors();
  const rankIdx = ranked.findIndex((e) => e.name === target.name);
  const rank = rankIdx >= 0 ? rankIdx + 1 : null;
  const hasPrev = rankIdx > 0;
  const hasNext = rankIdx >= 0 && rankIdx < ranked.length - 1;

  const barHtml = cats.map((cat) => renderCategoryBar(cat, catColors[cat.id] || "#999")).join("");

  const criteriaHtml = cats.map((cat) => {
    return `<div class="criteria-section" data-cat="${cat.id}">
      <h5>${cat.label}</h5>
      ${renderCriteriaTable(cat.criteria || [])}
    </div>`;
  }).join("");

  const rankBadge = rank ? `<span class="rank-badge">#${rank}</span>` : "";
  const navHtml = ranked.length > 1 ? `<div class="detail-nav">
    <button type="button" class="nav-arrow nav-prev${hasPrev ? "" : " disabled"}" id="detail-prev" aria-label="Previous">&larr;</button>
    <span class="nav-pos">${rank}/${ranked.length}</span>
    <button type="button" class="nav-arrow nav-next${hasNext ? "" : " disabled"}" id="detail-next" aria-label="Next">&rarr;</button>
  </div>` : "";

  host.innerHTML = `
    <div class="detail-header">
      <h3>${rankBadge} ${target.name}</h3>
      ${navHtml}
    </div>
    <p class="${toneForStatus(target.status)}">${target.status}</p>
    <div class="total-score-bar">
      <span class="total-label">Total: ${scores.total}/100</span>
      <div class="cat-bar-track total-track"><div class="cat-bar-fill total-fill" style="width:${totalPct}%"></div></div>
    </div>
    <div class="cat-bars">${barHtml}</div>
    <details class="criteria-details">
      <summary>View all ${scores.total_criteria || 45} criteria</summary>
      ${criteriaHtml}
    </details>
    <p class="detail-note scored-by">Scored by: ${target.relationship === "self" ? "self" : "maintainers"} | Subjective: ${scores.subjective_count || 1}/${scores.total_criteria || 45} | Last verified: ${target.last_verified || "N/A"}</p>
    <p class="detail-note scored-by">Version evaluated: ${target.version_evaluated || "N/A"}${target.repo_url ? ` | <a href="${target.repo_url}" target="_blank" rel="noopener">Source</a>` : ""}</p>
    <div class="detail-kpis">
      <article><h4>MCP tools</h4><p>${target.mcp}</p></article>
      <article><h4>100% local</h4><p>${localLabel}</p></article>
      <article><h4>Graph layer</h4><p>${target.graph}</p></article>
      <article><h4>CLI commands</h4><p>${target.cli_commands || "N/A"}</p></article>
    </div>
    <p class="detail-note">${target.note}</p>
  `;

  // Wire up nav arrows
  const prevBtn = document.getElementById("detail-prev");
  const nextBtn = document.getElementById("detail-next");
  if (prevBtn && hasPrev) {
    prevBtn.onclick = () => {
      activeName = ranked[rankIdx - 1].name;
      renderMap();
    };
  }
  if (nextBtn && hasNext) {
    nextBtn.onclick = () => {
      activeName = ranked[rankIdx + 1].name;
      renderMap();
    };
  }
}

// ---------------------------------------------------------------------------
// Weight sliders
// ---------------------------------------------------------------------------

function renderWeightSliders() {
  const host = document.getElementById("weight-sliders");
  if (!host || rubric.length === 0) return;

  host.innerHTML = rubric.map((cat) => {
    const w = weights[cat.id] || 0;
    const color = catColors[cat.id] || "#999";
    return `<div class="slider-row">
      <label class="slider-label" for="slider-${cat.id}">${cat.label}</label>
      <input type="range" id="slider-${cat.id}" data-cat="${cat.id}" min="0" max="50" value="${w}" class="weight-slider" style="accent-color:${color}">
      <span class="slider-value" id="slider-val-${cat.id}">${w}</span>
    </div>`;
  }).join("");

  host.querySelectorAll("input.weight-slider").forEach((input) => {
    input.addEventListener("input", (e) => {
      const catId = e.target.getAttribute("data-cat");
      weights[catId] = Number(e.target.value);
      const valEl = document.getElementById(`slider-val-${catId}`);
      if (valEl) valEl.textContent = String(weights[catId]);
      updateWeightTotal();
      renderMap();
      renderTable();
    });
  });

  const resetBtn = document.getElementById("reset-weights");
  if (resetBtn) {
    resetBtn.onclick = () => {
      Object.keys(defaultWeights).forEach((id) => {
        weights[id] = defaultWeights[id];
        const slider = document.getElementById(`slider-${id}`);
        if (slider) slider.value = String(defaultWeights[id]);
        const valEl = document.getElementById(`slider-val-${id}`);
        if (valEl) valEl.textContent = String(defaultWeights[id]);
      });
      updateWeightTotal();
      renderMap();
      renderTable();
    };
  }
  updateWeightTotal();
}

function updateWeightTotal() {
  const total = Object.values(weights).reduce((a, b) => a + b, 0);
  const el = document.getElementById("weight-total-display");
  if (el) el.textContent = `Total weight: ${total}`;
}

// ---------------------------------------------------------------------------
// Rubric rendering (methodology section)
// ---------------------------------------------------------------------------

function renderRubric() {
  const host = document.getElementById("rubric-container");
  if (!host || rubric.length === 0) return;

  host.innerHTML = rubric.map((cat) => {
    const color = catColors[cat.id] || "#999";
    const rows = (cat.criteria || []).map((c) => {
      const typeLabel = c.type === "binary" ? "binary" : c.type === "subjective" ? "subjective" : "tiered";
      return `<tr><td>${c.label}</td><td>${typeLabel}</td><td>${c.max}</td></tr>`;
    }).join("");
    return `<div class="rubric-category">
      <h4>${cat.label} <span class="rubric-cat-max">(${cat.max_points} pts, default ${cat.default_weight}%)</span></h4>
      <table class="rubric-table"><thead><tr><th>Criterion</th><th>Type</th><th>Max</th></tr></thead><tbody>${rows}</tbody></table>
    </div>`;
  }).join("");
}

// ---------------------------------------------------------------------------
// Matrix table
// ---------------------------------------------------------------------------

function renderMatrixModeControls() {
  const host = document.getElementById("matrix-mode-controls");
  if (!host) return;
  const buttons = Array.from(host.querySelectorAll("button[data-mode]"));
  buttons.forEach((button) => {
    const mode = String(button.getAttribute("data-mode") || "simple");
    button.classList.toggle("active", mode === matrixMode);
    button.onclick = () => {
      matrixMode = mode === "full" ? "full" : "simple";
      renderMatrixModeControls();
      renderTable();
    };
  });
}

function renderTable() {
  const rows = rankedCompetitors();
  const head = document.getElementById("matrix-head");
  const body = document.getElementById("matrix-body");
  const table = document.querySelector(".table-wrap table");

  const catMiniBar = (entry) => {
    const s = getEffectiveScores(entry);
    const cats = s.categories || [];
    return cats.map((c) => {
      const pct = c.max > 0 ? Math.round((c.score / c.max) * 100) : 0;
      const color = catColors[c.id] || "#999";
      return `<span class="mini-bar" title="${c.label}: ${c.score}/${c.max}"><span class="mini-fill" style="width:${pct}%;background:${color}"></span></span>`;
    }).join("");
  };

  // Per-category cell with inline bar + score
  const catCell = (entry, catId) => {
    const s = getEffectiveScores(entry);
    const cat = (s.categories || []).find((c) => c.id === catId);
    if (!cat) return "-";
    const pct = cat.max > 0 ? Math.round((cat.score / cat.max) * 100) : 0;
    const color = catColors[catId] || "#999";
    return `<span class="mini-bar" style="width:2.5rem"><span class="mini-fill" style="width:${pct}%;background:${color}"></span></span> <span class="cat-cell-score">${cat.score}/${cat.max}</span>`;
  };

  const catShortLabels = {
    static_analysis: "SA",
    graph_intelligence: "Graph",
    git_temporal: "Git",
    agent_integration: "Agent",
    security_governance: "Sec",
    ecosystem: "Eco",
    unique_capabilities: "Uniq",
  };

  const simpleColumns = [
    { label: "Project", render: (entry) => entry.name },
    { label: "Total", render: (entry) => `<strong>${getEffectiveScores(entry).total}</strong>/100` },
    { label: "Breakdown", render: (entry) => catMiniBar(entry) },
    { label: "MCP", render: (entry) => entry.mcp },
    { label: "100% Local", render: (entry) => (entry.local ? "Yes" : "No") },
  ];

  const catIds = rubric.map((c) => c.id);
  const fullColumns = [
    { label: "Project", render: (entry) => entry.name },
    { label: "Total", render: (entry) => `<strong>${getEffectiveScores(entry).total}</strong>/100` },
    ...catIds.map((id) => ({
      label: catShortLabels[id] || id,
      render: (entry) => catCell(entry, id),
    })),
    { label: "MCP", render: (entry) => entry.mcp },
    { label: "Local", render: (entry) => (entry.local ? "Yes" : "No") },
  ];

  const columns = matrixMode === "full" ? fullColumns : simpleColumns;
  if (head) {
    head.innerHTML = `<tr>${columns.map((column) => `<th>${column.label}</th>`).join("")}</tr>`;
  }

  if (table) {
    table.setAttribute("data-mode", matrixMode);
  }

  body.innerHTML = rows
    .map((entry) => `<tr>${columns.map((column) => `<td>${column.render(entry)}</td>`).join("")}</tr>`)
    .join("");
}

// ---------------------------------------------------------------------------
// Boot
// ---------------------------------------------------------------------------

async function boot() {
  try {
    await loadSiteData();
  } catch (error) {
    loadError(error.message || String(error));
    return;
  }

  renderMatrixModeControls();
  renderMap();
  renderTable();
  renderWeightSliders();
  renderRubric();
}

void boot();
