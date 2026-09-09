/* 정적 사이트 로그인/암호화 공통 모듈.
   서버가 없으므로 데이터(bundle.enc)는 AES-256-GCM 으로 암호화되어 있고,
   로그인 시 비밀번호로 contentKey 를 unwrap 해 세션(sessionStorage)에 보관한다.
   scripts/manage_users.py 와 포맷 호환 (PBKDF2-SHA256 / AES-GCM / base64). */
(function () {
  "use strict";

  const KEY_SS = "dash.key";     // b64(contentKey 32B)
  const USER_SS = "dash.user";   // {id,name,role}
  const enc = new TextEncoder();
  const dec = new TextDecoder();
  const b64d = (s) => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
  const b64e = (buf) => btoa(String.fromCharCode(...new Uint8Array(buf)));

  async function deriveKek(password, salt, iterations) {
    const material = await crypto.subtle.importKey("raw", enc.encode(password), "PBKDF2", false, ["deriveKey"]);
    return crypto.subtle.deriveKey(
      { name: "PBKDF2", salt, iterations, hash: "SHA-256" },
      material, { name: "AES-GCM", length: 256 }, false, ["decrypt", "encrypt"],
    );
  }
  async function aesDecrypt(key, ivB64, ctB64) {
    const pt = await crypto.subtle.decrypt({ name: "AES-GCM", iv: b64d(ivB64) }, key, b64d(ctB64));
    return new Uint8Array(pt);
  }
  async function aesEncrypt(key, bytes) {
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, bytes);
    return { iv: b64e(iv), ct: b64e(ct) };
  }
  async function importContentKey(rawBytes) {
    return crypto.subtle.importKey("raw", rawBytes, { name: "AES-GCM", length: 256 }, false, ["decrypt", "encrypt"]);
  }

  /* ---- 로그인: users.json + 비밀번호 → contentKey ---- */
  async function login(userId, password) {
    const users = await fetch("users.json?_=" + Date.now()).then((r) => r.json());
    const u = (users.users || {})[userId];
    if (!u) throw new Error("아이디 또는 비밀번호가 올바르지 않습니다.");
    const iters = users.iterations || 600000;
    let contentKey;
    try {
      const kek = await deriveKek(password, b64d(u.salt), iters);
      contentKey = await aesDecrypt(kek, u.iv, u.wrapped);      // 32B raw
    } catch (e) {
      throw new Error("아이디 또는 비밀번호가 올바르지 않습니다.");
    }
    // verify (선택)
    if (users.verify) {
      try {
        const ck = await importContentKey(contentKey);
        const chk = dec.decode(await aesDecrypt(ck, users.verify.iv, users.verify.ct));
        if (chk.indexOf("customerDashboard") !== 0) throw 0;
      } catch (e) { throw new Error("데이터 키 검증 실패 — 관리자에게 문의하세요."); }
    }
    try {
      sessionStorage.setItem(KEY_SS, b64e(contentKey));
      sessionStorage.setItem(USER_SS, JSON.stringify({ id: userId, name: u.name || userId, role: u.role || "viewer" }));
    } catch (e) { /* private mode */ }
    return { id: userId, name: u.name, role: u.role || "viewer" };
  }

  function currentUser() {
    try { return JSON.parse(sessionStorage.getItem(USER_SS) || "null"); } catch (e) { return null; }
  }
  function isLoggedIn() {
    try { return !!sessionStorage.getItem(KEY_SS); } catch (e) { return false; }
  }
  function logout() {
    try { sessionStorage.removeItem(KEY_SS); sessionStorage.removeItem(USER_SS); } catch (e) {}
    location.replace("login.html");
  }
  /* 보호 페이지 첫 줄에서 호출. 미로그인 → login.html 이동, false 반환 */
  function requireLogin() {
    if (isLoggedIn()) return true;
    const here = location.pathname.split("/").pop() + location.search;
    location.replace("login.html?next=" + encodeURIComponent(here || "index.html"));
    return false;
  }

  async function sessionContentKey() {
    const b = sessionStorage.getItem(KEY_SS);
    if (!b) throw new Error("로그인이 필요합니다.");
    return importContentKey(b64d(b));
  }

  /* 암호화 번들 로드 → {inflow, channels, log} */
  async function loadBundle(force) {
    const key = await sessionContentKey();
    const blob = await fetch("data/bundle.enc" + (force ? "?_=" + Date.now() : "")).then((r) => {
      if (!r.ok) throw new Error("데이터 파일(bundle.enc)을 찾을 수 없습니다.");
      return r.json();
    });
    const pt = await aesDecrypt(key, blob.iv, blob.ct);
    return JSON.parse(dec.decode(pt));
  }

  /* 관리자: 현재 세션 contentKey 를 새 사용자 비번으로 wrap (users.json 항목 생성) */
  async function wrapForNewUser(password, iterations) {
    const raw = b64d(sessionStorage.getItem(KEY_SS));
    const salt = crypto.getRandomValues(new Uint8Array(16));
    const kek = await deriveKek(password, salt, iterations || 600000);
    const w = await aesEncrypt(kek, raw);
    return { salt: b64e(salt), iv: w.iv, wrapped: w.ct };
  }

  window.AUTH = {
    login, logout, requireLogin, isLoggedIn, currentUser,
    loadBundle, wrapForNewUser,
  };
})();
