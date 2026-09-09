"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, (m) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
let channels = [];
let channelsMeta = {};

let bt;
function banner(kind, msg, sticky) {
  const b = $("banner");
  b.className = "banner show " + kind; b.textContent = msg;
  clearTimeout(bt); if (!sticky) bt = setTimeout(() => (b.className = "banner"), 6000);
}

/* -------- tabs -------- */
document.querySelectorAll(".admin-tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".admin-tabs button").forEach((x) => x.classList.toggle("on", x === b));
    ["channels", "history", "errors"].forEach((t) => ($("tab-" + t).hidden = t !== b.dataset.tab));
    if (b.dataset.tab === "history" || b.dataset.tab === "errors") loadLogs();
  }));

/* -------- channels -------- */
async function loadChannels() {
  const d = await (await fetch("data/channels.json")).json();
  channels = d.channels || [];
  channelsMeta = { _comment: d._comment || "제휴채널 마스터", updated_at: d.updated_at };
  renderChannels();
}

function renderChannels() {
  const tb = $("ch-body"); tb.innerHTML = "";
  channels.sort((a, b) => (a.sort ?? 999) - (b.sort ?? 999));
  channels.forEach((c, i) => {
    const tr = document.createElement("tr");
    tr.draggable = true; tr.dataset.i = i;
    tr.innerHTML = `
      <td class="drag-handle">⠿</td>
      <td><code>${esc(c.code)}</code></td>
      <td><input type="text" data-k="name" value="${esc(c.name)}" /></td>
      <td><input type="text" data-k="type" value="${esc(c.type || "")}" style="width:90px" /></td>
      <td><input type="checkbox" data-k="use_yn" ${c.use_yn === "Y" ? "checked" : ""} /></td>
      <td><input type="checkbox" data-k="display_yn" ${c.display_yn === "Y" ? "checked" : ""} /></td>
      <td><input type="text" data-k="sort" value="${c.sort ?? i + 1}" style="width:44px" /></td>
      <td class="row-actions"><button data-del="${i}">삭제</button></td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("input").forEach((inp) =>
    inp.addEventListener("change", () => {
      const tr = inp.closest("tr"); const c = channels[+tr.dataset.i]; const k = inp.dataset.k;
      if (inp.type === "checkbox") c[k] = inp.checked ? "Y" : "N";
      else if (k === "sort") c[k] = parseInt(inp.value, 10) || 0;
      else c[k] = inp.value;
    }));
  tb.querySelectorAll("[data-del]").forEach((btn) =>
    btn.addEventListener("click", () => {
      if (confirm(`${channels[+btn.dataset.del].code} 채널을 목록에서 삭제할까요?`)) {
        channels.splice(+btn.dataset.del, 1); renderChannels();
      }
    }));
  enableDrag(tb);
}

function enableDrag(tb) {
  let dragEl;
  tb.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("dragstart", () => { dragEl = tr; tr.classList.add("dragging"); });
    tr.addEventListener("dragend", () => {
      tr.classList.remove("dragging");
      [...tb.querySelectorAll("tr")].forEach((r, idx) => (channels[+r.dataset.i].sort = idx + 1));
      renderChannels();
    });
    tr.addEventListener("dragover", (e) => {
      e.preventDefault();
      const rows = [...tb.querySelectorAll("tr:not(.dragging)")];
      const after = rows.find((r) => e.clientY <= r.getBoundingClientRect().top + r.offsetHeight / 2);
      if (after) tb.insertBefore(dragEl, after); else tb.appendChild(dragEl);
    });
  });
}

$("ch-add").addEventListener("click", () => {
  const code = $("new-code").value.trim();
  if (!code) return;
  if (channels.some((c) => c.code === code)) return banner("warn", "이미 있는 code 입니다.");
  channels.push({ code, name: code, type: "기타", use_yn: "Y", display_yn: "Y", sort: channels.length + 1 });
  $("new-code").value = ""; renderChannels();
});

$("ch-download").addEventListener("click", () => {
  const out = Object.assign({}, channelsMeta,
    { updated_at: new Date().toISOString(), channels });
  const blob = new Blob([JSON.stringify(out, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob); a.download = "channels.json"; a.click();
  URL.revokeObjectURL(a.href);
  banner("ok", "channels.json 을 내려받았습니다. 저장소 docs/data/channels.json 에 커밋하세요.");
});

/* -------- logs -------- */
async function loadLogs() {
  const d = await (await fetch("data/collection_log.json")).json().catch(() => ({ logs: [] }));
  const logs = (d.logs || []).slice().reverse();
  const badge = (s) => ({
    success: '<span class="delta up">정상</span>', skipped: '<span class="delta flat">중복</span>',
    error: '<span class="delta down">오류</span>',
  }[s] || s);
  const dsrc = (s) => s === "subject" ? '<span class="badge-type">제목 날짜</span>'
    : s === "received-1d" ? '<span class="badge-type" style="color:var(--warn);border-color:#f0d9a8">수신일-1일</span>' : "";
  $("hist-body").innerHTML = logs.map((l) => `<tr>
    <td>${l.collected_at ? new Date(l.collected_at).toLocaleString("ko-KR") : "-"}</td>
    <td>${l.record_date || "-"} ${dsrc(l.date_source)}</td><td>${badge(l.status)}</td>
    <td style="text-align:left;white-space:normal">${esc(l.subject)}</td>
    <td style="text-align:left">${esc(l.sender)}</td>
    <td style="text-align:left;white-space:normal">${esc(l.message)}</td></tr>`).join("")
    || `<tr><td colspan="6" style="text-align:center;color:var(--ink-3)">수집이력 없음</td></tr>`;
  const probs = logs.filter((l) => l.status === "error" || (l.warnings && l.warnings.length));
  $("err-body").innerHTML = probs.length ? probs.map((l) => `<tr>
    <td>${l.collected_at ? new Date(l.collected_at).toLocaleString("ko-KR") : "-"}</td>
    <td>${l.record_date || "-"}</td><td>${badge(l.status)}</td>
    <td style="text-align:left;white-space:normal">${esc(l.message)}${
      (l.warnings || []).map((w) => `<br><span style="color:var(--warn)">⚠ ${esc(w)}</span>`).join("")}</td></tr>`).join("")
    : `<tr><td colspan="4" style="text-align:center;color:var(--ink-3)">오류/경고 없음</td></tr>`;
}

loadChannels();
