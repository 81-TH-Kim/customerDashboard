/* 관리자 화면에서 GitHub 저장소에 직접 커밋 → GitHub Pages 자동 반영.
   토큰(Fine-grained PAT, Contents: Read and write)은 이 브라우저 localStorage 에만 저장.
   절대 저장소/파일에 커밋되지 않는다. */
(function () {
  "use strict";

  const OWNER = "81-TH-Kim";
  const REPO = "customerDashboard";
  const BRANCH = "main";
  const API = "https://api.github.com";
  const TS = "dash.gh_pat";

  const b64encodeUtf8 = (str) => btoa(unescape(encodeURIComponent(str)));
  const b64decodeUtf8 = (b64) => decodeURIComponent(escape(atob(b64.replace(/\n/g, ""))));

  function token() { try { return localStorage.getItem(TS) || ""; } catch (e) { return ""; } }
  function setToken(t) { try { localStorage.setItem(TS, (t || "").trim()); } catch (e) {} }
  function clearToken() { try { localStorage.removeItem(TS); } catch (e) {} }
  function hasToken() { return !!token(); }

  async function api(path, opts) {
    const r = await fetch(API + path, Object.assign({
      headers: {
        Authorization: "Bearer " + token(),
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
      },
    }, opts || {}));
    const body = await r.json().catch(() => ({}));
    if (!r.ok) {
      throw new Error(`GitHub ${r.status}: ${body.message || r.statusText}`);
    }
    return body;
  }

  async function verify() {
    if (!hasToken()) return { ok: false, reason: "토큰 없음" };
    try {
      const repo = await api(`/repos/${OWNER}/${REPO}`);
      const canWrite = !!(repo.permissions && repo.permissions.push);
      return { ok: true, full: repo.full_name, canWrite };
    } catch (e) {
      return { ok: false, reason: e.message };
    }
  }

  async function getFile(path) {
    const j = await api(`/repos/${OWNER}/${REPO}/contents/${path}?ref=${BRANCH}`);
    return { text: b64decodeUtf8(j.content || ""), sha: j.sha };
  }

  /* contentString(UTF-8 문자열) 을 path 에 커밋. sha 없으면 조회 시도. */
  async function putFile(path, contentString, message) {
    let sha;
    try { sha = (await getFile(path)).sha; } catch (e) { sha = undefined; }
    const payload = {
      message: message || `chore: update ${path}`,
      content: b64encodeUtf8(contentString),
      branch: BRANCH,
    };
    if (sha) payload.sha = sha;
    return api(`/repos/${OWNER}/${REPO}/contents/${path}`, {
      method: "PUT",
      headers: {
        Authorization: "Bearer " + token(),
        Accept: "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(payload),
    });
  }

  window.GH = { OWNER, REPO, BRANCH, token, setToken, clearToken, hasToken, verify, getFile, putFile };
})();
