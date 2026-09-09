"use strict";
const $ = (id) => document.getElementById(id);
const esc = (s) => (s ?? "").toString().replace(/[&<>"]/g, (m) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[m]));
let channels = [];
let channelsMeta = {};
let BUNDLE = null;
let usersDoc = null;

if (!window.AUTH.requireLogin()) { throw new Error("redirect to login"); }

let bt;
function banner(kind, msg, sticky) {
  const b = $("banner");
  b.className = "banner show " + kind; b.textContent = msg;
  clearTimeout(bt); if (!sticky) bt = setTimeout(() => (b.className = "banner"), 6000);
}

const SERVER = window.APP_MODE === "server";
const me = window.AUTH.currentUser();
if (me) $("who").textContent = `${me.name}${me.role === "admin" ? " · 관리자" : ""}`;
$("btn-logout").addEventListener("click", () => window.AUTH.logout());

/* server 모드: GitHub 연결 패널 숨김, 데이터 업로드 패널 표시 */
if (SERVER) {
  const p = $("gh-panel"); if (p) p.hidden = true;
  const pill = $("gh-pill"); if (pill) pill.textContent = "서버 저장 — 변경 즉시 반영";
  const ip = $("import-panel"); if (ip) ip.hidden = (me && me.role !== "admin");
}

async function readJsonFile(input) {
  const f = input.files && input.files[0];
  if (!f) return null;
  return JSON.parse(await f.text());
}
if ($("imp-run")) $("imp-run").addEventListener("click", async () => {
  const btn = $("imp-run"); btn.disabled = true; btn.textContent = "업로드 중…";
  try {
    const payload = {};
    const inflow = await readJsonFile($("imp-inflow")); if (inflow) payload.inflow = inflow;
    const channels = await readJsonFile($("imp-channels")); if (channels) payload.channels = channels;
    const log = await readJsonFile($("imp-log")); if (log) payload.log = log;
    if (!Object.keys(payload).length) return banner("warn", "파일을 선택하세요.");
    const j = await apiPost("api/admin/import", payload);
    BUNDLE = null;
    banner("ok", "업로드 완료: " + (j.wrote || []).join(", "));
    loadChannels();
  } catch (e) { banner("err", "업로드 실패: " + e.message, true); }
  finally { btn.disabled = false; btn.textContent = "업로드"; }
});

async function apiPost(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
  return j;
}

/* -------- GitHub 연결 -------- */
async function refreshGh() {
  if (SERVER) return false;
  const st = $("gh-status"), pill = $("gh-pill");
  if (!window.GH.hasToken()) {
    st.textContent = "미연결"; pill.textContent = "GitHub 미연결 — 저장 시 다운로드만"; return false;
  }
  st.textContent = "확인 중…";
  const v = await window.GH.verify();
  if (v.ok && v.canWrite) {
    st.textContent = `연결됨 · ${v.full}`; pill.textContent = "GitHub 연결됨 — 화면에서 저장 시 즉시 반영"; return true;
  }
  st.textContent = v.ok ? "쓰기 권한 없음 — 토큰 권한(Contents: write) 확인" : "토큰 오류: " + v.reason;
  pill.textContent = "GitHub 연결 실패";
  return false;
}
$("gh-connect").addEventListener("click", async () => {
  const t = $("gh-token").value.trim();
  if (!t) return banner("warn", "토큰을 입력하세요.");
  window.GH.setToken(t); $("gh-token").value = "";
  const ok = await refreshGh();
  banner(ok ? "ok" : "err", ok ? "GitHub 연결됨." : "연결 실패 — 상태 메시지 확인", !ok);
});
$("gh-disconnect").addEventListener("click", async () => {
  window.GH.clearToken(); await refreshGh(); banner("ok", "연결을 해제했습니다.");
});
refreshGh();

/* GitHub 커밋 or 다운로드 공통 */
async function saveFile(path, text, message, dlName) {
  if (window.GH.hasToken()) {
    await window.GH.putFile(path, text, message);
    return "gh";
  }
  download(dlName, text);
  return "download";
}

/* -------- tabs -------- */
const TABS = ["channels", "users", "history", "errors"];
document.querySelectorAll(".admin-tabs button").forEach((b) =>
  b.addEventListener("click", () => {
    document.querySelectorAll(".admin-tabs button").forEach((x) => x.classList.toggle("on", x === b));
    TABS.forEach((t) => ($("tab-" + t).hidden = t !== b.dataset.tab));
    if (b.dataset.tab === "history" || b.dataset.tab === "errors") loadLogs();
    if (b.dataset.tab === "users") loadUsers();
  }));

/* -------- 데이터 로드 -------- */
async function bundle() {
  if (!BUNDLE) BUNDLE = await window.AUTH.loadBundle();
  return BUNDLE;
}

/* -------- channels -------- */
async function loadChannels() {
  const d = (await bundle()).channels || { channels: [] };
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
      if (confirm(`${channels[+btn.dataset.del].code} 채널을 삭제할까요?`)) {
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

function channelsJson() {
  return JSON.stringify(Object.assign({}, channelsMeta,
    { updated_at: new Date().toISOString(), channels }), null, 2);
}
$("ch-download").addEventListener("click", () => {
  download("channels.json", channelsJson());
  banner("ok", "channels.json 저장. data/live/channels.json 교체 후 publish.bat 실행.");
});
$("ch-save").addEventListener("click", async () => {
  const btn = $("ch-save"); btn.disabled = true; btn.textContent = "저장 중…";
  try {
    if (SERVER) {
      await apiPost("api/channels", { channels });
      BUNDLE = null;
      banner("ok", "저장 완료. 대시보드에 즉시 반영됩니다.");
    } else if (window.GH.hasToken()) {
      const b = await bundle();
      const newBundle = { inflow: b.inflow, log: b.log, channels: JSON.parse(channelsJson()) };
      await window.GH.putFile("docs/data/bundle.enc", await window.AUTH.encryptBundle(newBundle),
        "chore: 채널 설정 변경 (관리자 화면)");
      BUNDLE = newBundle;
      banner("ok", "저장 완료. 1~2분 후 사이트에 반영됩니다.");
    } else {
      download("channels.json", channelsJson());
      banner("warn", "GitHub 미연결 — channels.json 만 내려받았습니다. data/live/ 교체 후 publish.bat.");
    }
  } catch (e) { banner("err", "저장 실패: " + e.message, true); }
  finally { btn.disabled = false; btn.textContent = "저장 및 적용"; }
});

/* -------- users -------- */
async function loadUsers() {
  if (!me || me.role !== "admin") {
    $("u-body").innerHTML = `<tr><td colspan="4" style="text-align:center;color:var(--ink-3)">관리자만 사용할 수 있습니다.</td></tr>`;
    return;
  }
  if (SERVER) {
    if ($("u-save")) $("u-save").hidden = true;
    if ($("u-download")) $("u-download").hidden = true;
    const j = await fetch("api/admin/users").then((r) => r.json());
    usersDoc = { users: {} };
    (j.users || []).forEach((u) => (usersDoc.users[u.id] = { name: u.name, role: u.role }));
  } else if (!usersDoc) {
    usersDoc = await fetch("users.json?_=" + Date.now()).then((r) => r.json());
  }
  renderUsers();
}

function renderUsers() {
  const tb = $("u-body"); tb.innerHTML = "";
  const users = usersDoc.users || {};
  Object.keys(users).forEach((uid) => {
    const u = users[uid];
    const tr = document.createElement("tr");
    tr.innerHTML = `<td><code>${esc(uid)}</code></td><td>${esc(u.name || "")}</td>
      <td>${u.role === "admin" ? "관리자" : "조회"}</td>
      <td class="row-actions">${uid === me.id ? "<span style='color:var(--ink-3)'>(본인)</span>"
        : `<button data-del="${esc(uid)}">제거</button>` + (SERVER ? ` <button data-pw="${esc(uid)}">비번변경</button>` : "")}</td>`;
    tb.appendChild(tr);
  });
  tb.querySelectorAll("[data-del]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const uid = btn.dataset.del;
      if (!confirm(`'${uid}' 사용자를 제거할까요?`)) return;
      if (SERVER) {
        try { await apiPost("api/admin/users", { action: "remove", id: uid }); await loadUsers(); banner("ok", `'${uid}' 제거됨.`); }
        catch (e) { banner("err", "제거 실패: " + e.message, true); }
      } else { delete usersDoc.users[uid]; renderUsers(); }
    }));
  tb.querySelectorAll("[data-pw]").forEach((btn) =>
    btn.addEventListener("click", async () => {
      const uid = btn.dataset.pw;
      const pw = prompt(`'${uid}' 의 새 비밀번호 (6자 이상)`);
      if (!pw) return;
      if (pw.length < 6) return banner("warn", "6자 이상.");
      try { await apiPost("api/admin/users", { action: "passwd", id: uid, password: pw }); banner("ok", "변경됨."); }
      catch (e) { banner("err", "변경 실패: " + e.message, true); }
    }));
}

$("u-add").addEventListener("click", async () => {
  const id = $("u-id").value.trim(), name = $("u-name").value.trim(), pw = $("u-pw").value;
  if (!/^[A-Za-z0-9_.-]{2,}$/.test(id)) return banner("warn", "아이디는 영문/숫자 2자 이상.");
  if (!name) return banner("warn", "이름을 입력하세요.");
  if (pw.length < 6) return banner("warn", "임시 비밀번호는 6자 이상.");
  if (usersDoc.users[id]) return banner("warn", "이미 있는 아이디입니다.");
  const admin = $("u-admin").checked;
  try {
    if (SERVER) {
      await apiPost("api/admin/users", { action: "add", id, name, password: pw, admin });
      $("u-id").value = $("u-name").value = $("u-pw").value = ""; $("u-admin").checked = false;
      await loadUsers();
      banner("ok", `'${id}' 추가됨. 바로 로그인 가능합니다.`);
    } else {
      const wrapped = await window.AUTH.wrapForNewUser(pw, usersDoc.iterations);
      usersDoc.users[id] = { name, role: admin ? "admin" : "viewer", ...wrapped };
      $("u-id").value = $("u-name").value = $("u-pw").value = ""; $("u-admin").checked = false;
      renderUsers();
      banner("ok", `'${id}' 추가됨. 아래 "저장 및 적용" 을 눌러 반영하세요.`);
    }
  } catch (e) { banner("err", "추가 실패: " + e.message, true); }
});

if ($("u-download")) $("u-download").addEventListener("click", () => {
  download("users.json", JSON.stringify(usersDoc, null, 2));
  banner("ok", "users.json 저장. docs/users.json 에 커밋하면 반영됩니다.");
});
if ($("u-save")) $("u-save").addEventListener("click", async () => {
  const btn = $("u-save"); btn.disabled = true; btn.textContent = "저장 중…";
  try {
    const text = JSON.stringify(usersDoc, null, 2);
    if (window.GH.hasToken()) {
      await window.GH.putFile("docs/users.json", text, "chore: 사용자 변경 (관리자 화면)");
      banner("ok", "저장 완료. 1~2분 후 반영됩니다. (제거한 사용자 완전 차단은 rekey 필요)");
    } else {
      download("users.json", text);
      banner("warn", "GitHub 미연결 — users.json 만 내려받았습니다. 저장소에 커밋하세요.");
    }
  } catch (e) { banner("err", "저장 실패: " + e.message, true); }
  finally { btn.disabled = false; btn.textContent = "저장 및 적용"; }
});

if (SERVER && me && me.role === "admin" && $("collect-now")) {
  $("collect-now").hidden = false;
  $("collect-now").addEventListener("click", async () => {
    const btn = $("collect-now"); btn.disabled = true; btn.textContent = "수집 중…";
    try {
      const j = await apiPost("api/collect", { days: 5 });
      BUNDLE = null;
      banner(j.exit_code === 0 ? "ok" : "warn", "수집 완료 (종료코드 " + j.exit_code + ")");
      loadLogs();
    } catch (e) { banner("err", "수집 실패: " + e.message, true); }
    finally { btn.disabled = false; btn.textContent = "지금 수집"; }
  });
}

/* -------- logs -------- */
async function loadLogs() {
  BUNDLE = SERVER ? null : BUNDLE;
  const d = (await bundle()).log || { logs: [] };
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

/* -------- util -------- */
function download(name, text) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type: "application/json" }));
  a.download = name; a.click(); URL.revokeObjectURL(a.href);
}

if (me && me.role === "admin") $("tabbtn-users").hidden = false;
loadChannels().catch((e) => banner("err", "데이터 로드 실패: " + e.message, true));
