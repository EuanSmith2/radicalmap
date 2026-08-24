let network, allNodes, allEdges, showEdges = true, structuredMode = false;
let currentVideoDbId = null;
let showFlaggedOnly = false;
let allComments = [];

// Keep in sync with CATEGORY_SHADES in analysis/graph_builder.py
const CATEGORY_COLORS = {
  antisemitic:       "#c05038",
  white_nationalist: "#c9a227",
  conspiracy:        "#7a94ab",
  radicalisation:    "#7f9c6a",
  dehumanisation:    "#a97a99",
  esoteric:          "#9a8bb8",
  incel:             "#b3766a",
  islamophobic:      "#ab8a5f",
  accelerationist:   "#c07272",
  uncategorised:     "#6e7069",
};

const TRANSCRIPT_KEYWORDS = [
  "jew","goy","goyim","vril","aryan","1488","88","reich","nazi","heil","foid",
  "white","race","nation","blood","european","crusade","esoteric","hyperborea",
  "agartha","tartaria","noticing","based","redpill","blackpill","npc","truther",
  "telegram","discord","great replacement","white genocide","14 words",
];

async function init() {
  const res = await fetch("/api/graph");
  const data = await res.json();

  // Stats bar
  const flaggedTotal = data.nodes
    .filter(n => n.type === "video")
    .reduce((s, n) => s + (n.flagged_comment_count || 0), 0);
  document.getElementById("s-videos").textContent    = data.stats.total_videos;
  document.getElementById("s-hashtags").textContent  = data.stats.total_hashtags;
  document.getElementById("s-comments").textContent  = data.stats.total_comments || 0;
  document.getElementById("s-links").textContent     = data.stats.total_links;
  document.getElementById("s-flagged").textContent   = flaggedTotal;

  renderLegend(data.categories);

  allNodes = new vis.DataSet(data.nodes);
  allEdges = new vis.DataSet(data.edges);

  const container = document.getElementById("network");
  network = new vis.Network(container, { nodes: allNodes, edges: allEdges }, {
    physics: {
      solver: "forceAtlas2Based",
      forceAtlas2Based: { gravitationalConstant: -65, springLength: 130, springConstant: 0.04 },
      stabilization: { iterations: 350 },
    },
    interaction: { hover: true, tooltipDelay: 120 },
    nodes: {
      borderWidth: 1.5,
      font: { face: "'IBM Plex Mono', ui-monospace, monospace", size: 11 },
    },
    edges: {
      smooth: { type: "continuous" },
      arrows: { to: { enabled: false } },
      hoverWidth: 0.5,
    },
  });

  network.on("click", (params) => {
    if (params.nodes.length > 0) {
      const node = allNodes.get(params.nodes[0]);
      showDetail(node);
    } else {
      closeDetail();
    }
  });

  document.querySelectorAll(".filter, .severity-filter").forEach(cb => {
    cb.addEventListener("change", applyFilters);
  });
  document.getElementById("filter-flagged").addEventListener("change", (e) => {
    showFlaggedOnly = e.target.checked;
    applyFilters();
  });
}

function renderLegend(categories) {
  const el = document.getElementById("legend");
  let html = "";
  for (const [key, cat] of Object.entries(categories)) {
    html += `<div class="legend-item">
      <span class="legend-dot" style="background:${CATEGORY_COLORS[key] || "#ccc"}"></span>
      <span>${cat.label}</span>
    </div>`;
  }
  html += `
    <div style="margin-top:0.5rem; padding-top:0.5rem; border-top:1px solid var(--rule)">
      <div class="legend-item"><span class="legend-box"></span><span>Video (clean)</span></div>
      <div class="legend-item"><span class="legend-box" style="border-color:#c05038"></span><span>Video (flagged comments)</span></div>
      <div class="legend-item"><span class="legend-diamond"></span><span>Hashtag</span></div>
      <div class="legend-item"><span class="legend-circle"></span><span>External link</span></div>
    </div>`;
  el.innerHTML = html;
}

function applyFilters() {
  const activeTypes = Array.from(document.querySelectorAll(".filter:checked")).map(c => c.value);
  const activeSevs  = Array.from(document.querySelectorAll(".severity-filter:checked")).map(c => c.value);
  allNodes.forEach(node => {
    const typeOk  = activeTypes.includes(node.type);
    const sevOk   = node.type !== "hashtag" || activeSevs.includes(node.severity);
    const flagOk  = !showFlaggedOnly || node.type !== "video" || node.flagged_comment_count > 0;
    allNodes.update({ id: node.id, hidden: !(typeOk && sevOk && flagOk) });
  });
}

// ── Detail panel ──────────────────────────────────────────────

function showDetail(node) {
  const panel = document.getElementById("detail-panel");
  panel.classList.add("open");
  currentVideoDbId = null;
  document.getElementById("tab-bar").style.display = "none";
  switchTab("overview");

  if (node.type === "hashtag") renderHashtag(node);
  else if (node.type === "video") renderVideo(node);
  else if (node.type === "external_link") renderExternal(node);
}

function renderHashtag(node) {
  document.getElementById("detail-header").innerHTML = `
    <div class="detail-type-label">Hashtag · ${node.group}</div>
    <div class="detail-title">${node.label}</div>
    <div class="badge-row">
      <span class="badge badge-${node.severity}">${node.severity} severity</span>
      <span class="badge badge-cat" style="color:${CATEGORY_COLORS[node.group] || "var(--mid)"}">${node.group.replace(/_/g, " ")}</span>
    </div>`;
  document.getElementById("detail-content").innerHTML = `
    <div class="detail-section">
      <div class="usage-num">${node.usage_count}</div>
      <div class="usage-label">appearances in dataset</div>
    </div>
    ${node.title ? `<div class="detail-section">
      <div class="detail-section-label">Definition</div>
      <p class="detail-text">${node.title}</p>
      ${node.origin ? `<p class="detail-source">— ${escHtml(node.origin)}</p>` : ""}
    </div>` : ""}`;
}

function renderVideo(node) {
  currentVideoDbId = node.db_id;
  const videoEl = node.local_video
    ? `<video controls><source src="${node.local_video}" type="video/mp4"></video>`
    : `<p class="detail-meta" style="font-style:italic">Not downloaded locally — <a class="detail-link" href="${node.url}" target="_blank" rel="noopener">View on TikTok ↗</a></p>`;

  const flagBadge = node.flagged_comment_count > 0
    ? `<span class="badge badge-flagged">${node.flagged_comment_count} flagged comments</span>` : "";
  const transBadge = node.has_transcript
    ? `<span class="badge badge-cat">transcript</span>` : "";

  document.getElementById("detail-header").innerHTML = `
    <div class="detail-type-label">Video</div>
    <div class="detail-title">${node.label}</div>
    <div class="badge-row">${flagBadge}${transBadge}</div>`;

  document.getElementById("detail-content").innerHTML = `
    ${videoEl}
    ${node.caption ? `<div class="detail-section">
      <div class="detail-section-label">Caption</div>
      <p class="detail-text">${escHtml(node.caption)}</p>
    </div>` : ""}
    <div class="detail-section">
      <div class="detail-section-label">Metrics</div>
      <div class="stat-grid">
        <div class="stat-cell"><span class="stat-val">${fmtNum(node.view_count)}</span><span class="stat-lbl">views</span></div>
        <div class="stat-cell"><span class="stat-val">${fmtNum(node.like_count)}</span><span class="stat-lbl">likes</span></div>
        <div class="stat-cell"><span class="stat-val">${fmtNum(node.comment_count)}</span><span class="stat-lbl">comments</span></div>
        <div class="stat-cell"><span class="stat-val">${fmtNum(node.save_count)}</span><span class="stat-lbl">saves</span></div>
        <div class="stat-cell"><span class="stat-val">${fmtNum(node.share_count)}</span><span class="stat-lbl">shares</span></div>
      </div>
      <p class="detail-meta" style="margin-top:0.5rem">
        <a class="detail-link" href="${node.url}" target="_blank" rel="noopener">View on TikTok ↗</a>
      </p>
    </div>`;

  document.getElementById("tab-bar").style.display = "flex";
  // Pre-load comments and transcript
  loadComments(node.db_id);
  loadTranscript(node.db_id, node.caption || "");
}

function renderExternal(node) {
  document.getElementById("detail-header").innerHTML = `
    <div class="detail-type-label">External Link · ${node.platform}</div>
    <div class="detail-title" style="font-size:1rem;word-break:break-all">${node.full_url}</div>`;
  document.getElementById("detail-content").innerHTML = `
    <div class="detail-section">
      <div class="detail-section-label">Platform</div>
      <p class="detail-meta">${node.platform}</p>
    </div>
    <a class="detail-link" href="https://${node.full_url}" target="_blank" rel="noopener">Open ↗</a>`;
}

async function loadComments(dbId) {
  const el = document.getElementById("comments-content");
  el.innerHTML = `<p class="no-comments">Loading…</p>`;
  try {
    const res = await fetch(`/api/video/${dbId}/comments`);
    const data = await res.json();
    allComments = data.comments || [];
    // Default to the flagged view only when there's something flagged to show;
    // otherwise land on All so a clean video still shows its comments.
    const hasFlagged = allComments.some(c => c.flagged);
    renderComments(hasFlagged ? "flagged" : "all");
  } catch {
    el.innerHTML = `<p class="no-comments">Could not load comments.</p>`;
  }
}

function renderComments(filter = "flagged") {
  const el = document.getElementById("comments-content");
  const flagged  = allComments.filter(c => c.flagged);
  const total    = allComments.length;
  const toShow   = filter === "all" ? allComments : flagged;

  // Update tab button label
  document.querySelector('[data-tab="comments"]').textContent =
    `Comments (${flagged.length > 0 ? flagged.length + " flagged" : total})`;

  let html = "";
  if (total > 0) {
    html += `<div class="comment-filter-row">
      <button class="comment-filter-btn ${filter==="flagged"?"active":""}" onclick="renderComments('flagged')">Flagged (${flagged.length})</button>
      <button class="comment-filter-btn ${filter==="all"?"active":""}" onclick="renderComments('all')">All (${total})</button>
    </div>`;
  }

  if (!toShow.length) {
    html += `<p class="no-comments">${filter === "flagged" ? "No flagged comments found." : "No comments available."}</p>`;
  } else {
    toShow.forEach(c => {
      const sevClass = c.flagged ? `flagged-${c.severity}` : "clean";
      const flagTags = c.flagged
        ? (c.flag_categories || "").split(",").filter(Boolean).map(cat =>
            `<span class="flag-tag ${c.severity}">${cat.trim()}</span>`
          ).join("") + `<span class="flag-tag ${c.severity}">${c.severity}</span>`
        : "";
      html += `<div class="comment-card ${sevClass}">
        <div class="comment-meta">
          <span class="comment-author">@${escHtml(c.author || "unknown")}</span>
          <span class="comment-likes">♥ ${fmtNum(c.like_count)}</span>
        </div>
        <div class="comment-text">${escHtml(c.text || "")}</div>
        ${flagTags ? `<div class="comment-flags">${flagTags}</div>` : ""}
      </div>`;
    });
  }
  el.innerHTML = html;
}

async function loadTranscript(dbId, captionFallback) {
  const el = document.getElementById("transcript-content");
  el.innerHTML = `<p class="no-transcript">Loading…</p>`;
  try {
    const res = await fetch(`/api/video/${dbId}/transcript`);
    const data = await res.json();
    const t = data.transcript;
    if (t && t.content) {
      const highlighted = highlightKeywords(escHtml(t.content));
      el.innerHTML = `
        <p class="transcript-meta">Auto-generated · ${t.language || "en"} · ${t.content.split(" ").length} words</p>
        <div class="transcript-body">${highlighted}</div>`;
      document.querySelector('[data-tab="transcript"]').textContent = "Transcript";
    } else if (captionFallback) {
      el.innerHTML = `
        <p class="transcript-meta">No auto-transcript — showing caption text</p>
        <div class="transcript-body">${highlightKeywords(escHtml(captionFallback))}</div>`;
      document.querySelector('[data-tab="transcript"]').textContent = "Caption";
    } else {
      el.innerHTML = `<p class="no-transcript">No transcript available for this video.</p>`;
    }
  } catch {
    el.innerHTML = `<p class="no-transcript">Could not load transcript.</p>`;
  }
}

function highlightKeywords(text) {
  let result = text;
  TRANSCRIPT_KEYWORDS.forEach(kw => {
    const re = new RegExp(`\\b(${kw.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})\\b`, "gi");
    result = result.replace(re, `<span class="transcript-kw">$1</span>`);
  });
  return result;
}

function switchTab(tab) {
  document.querySelectorAll(".tab-btn").forEach(b => b.classList.toggle("active", b.dataset.tab === tab));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.toggle("active", c.id === `tab-${tab}`));
}

function closeDetail() {
  document.getElementById("detail-panel").classList.remove("open");
  currentVideoDbId = null;
}

function toggleEdges() {
  showEdges = !showEdges;
  allEdges.forEach(e => allEdges.update({ id: e.id, hidden: !showEdges }));
}

function toggleLayout() {
  structuredMode = !structuredMode;
  const btn = document.getElementById("btn-layout");

  if (structuredMode) {
    btn.textContent = "Force layout";
    btn.style.background = "var(--accent)";
    btn.style.color = "#12130f";
    applyStructuredLayout();
  } else {
    btn.textContent = "Structured layout";
    btn.style.background = "";
    btn.style.color = "";
    // Return to physics
    network.setOptions({ physics: { enabled: true } });
    allNodes.forEach(n => network.releaseNode(n.id));
    setTimeout(() => network.setOptions({ physics: { enabled: false } }), 2500);
  }
}

function applyStructuredLayout() {
  network.setOptions({ physics: { enabled: false } });

  // Separate nodes by type
  const videos   = allNodes.get({ filter: n => n.type === "video"   && !n.hidden });
  const hashtags = allNodes.get({ filter: n => n.type === "hashtag" && !n.hidden });

  // Sort hashtags: by usage_count desc (most popular → top), then alphabetical
  hashtags.sort((a, b) => (b.usage_count || 0) - (a.usage_count || 0) || (a.label || "").localeCompare(b.label || ""));

  // Sort videos: flagged first, then by label
  videos.sort((a, b) => (b.flagged_comment_count || 0) - (a.flagged_comment_count || 0) || (a.label || "").localeCompare(b.label || ""));

  const VIEW_H    = 900;
  const TAG_X     = -600;   // hashtags column: left
  const VID_X     =  600;   // videos column: right
  const TAG_STEP  = Math.max(60, VIEW_H / Math.max(hashtags.length, 1));
  const VID_STEP  = Math.max(70, VIEW_H / Math.max(videos.length, 1));
  const TAG_TOP   = -((hashtags.length - 1) * TAG_STEP) / 2;
  const VID_TOP   = -((videos.length   - 1) * VID_STEP) / 2;

  hashtags.forEach((n, i) => {
    network.moveNode(n.id, TAG_X, TAG_TOP + i * TAG_STEP);
    network.body.nodes[n.id].options.fixed = { x: true, y: true };
  });

  videos.forEach((n, i) => {
    network.moveNode(n.id, VID_X, VID_TOP + i * VID_STEP);
    network.body.nodes[n.id].options.fixed = { x: true, y: true };
  });

  network.fit({ animation: { duration: 600, easingFunction: "easeInOutQuad" } });
}

function escHtml(str) {
  return String(str || "").replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}

function fmtNum(n) {
  if (!n) return "—";
  if (n >= 1e6) return (n/1e6).toFixed(1) + "M";
  if (n >= 1e3) return (n/1e3).toFixed(1) + "k";
  return n.toLocaleString();
}

init();
