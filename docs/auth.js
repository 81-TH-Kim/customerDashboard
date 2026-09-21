/* 정적 사이트 로그인/암호화 공통 모듈.
   서버가 없으므로 데이터(bundle.enc)는 AES-256-GCM 으로 암호화되어 있고,
   로그인 시 비밀번호로 contentKey 를 unwrap 해 세션(sessionStorage)에 보관한다.
   scripts/manage_users.py 와 포맷 호환 (PBKDF2-SHA256 / AES-GCM / base64). */
(function () {
  "use strict";

  const SERVER = (typeof window !== "undefined" && window.APP_MODE === "server");
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

  /* ---- 로그인 ---- */
  async function login(userId, password) {
    if (SERVER) {
      const r = await fetch("api/login", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ id: userId, password }),
      });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || !j.ok) throw new Error(j.error || "로그인에 실패했습니다.");
      try {
        sessionStorage.setItem(KEY_SS, "server");
        sessionStorage.setItem(USER_SS, JSON.stringify(j.user));
      } catch (e) {}
      return j.user;
    }
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
    if (SERVER && window.__ME) return window.__ME;
    try { return JSON.parse(sessionStorage.getItem(USER_SS) || "null"); } catch (e) { return null; }
  }
  function isLoggedIn() {
    if (SERVER) return !!window.__ME;
    try { return !!sessionStorage.getItem(KEY_SS); } catch (e) { return false; }
  }
  function logout() {
    if (SERVER) {
      fetch("api/logout", { method: "POST" }).finally(() => location.replace("login.html"));
      return;
    }
    try { sessionStorage.removeItem(KEY_SS); sessionStorage.removeItem(USER_SS); } catch (e) {}
    location.replace("login.html");
  }
  /* 보호 페이지에서 호출. server 모드는 서버가 이미 게이트했으므로 통과. */
  function requireLogin() {
    if (SERVER) return !!window.__ME || (location.replace("login.html"), false);
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

  /* 데이터 번들 로드 → {inflow, channels, log} */
  async function loadBundle(force) {
    // 매 로드마다 캐시버스팅: GitHub Pages CDN(max-age=600)·브라우저 캐시로
    // 방금 발행한 데이터가 최대 10분 넘게 옛날 값으로 보이는 걸 방지한다.
    if (SERVER) {
      const r = await fetch("api/bundle?_=" + Date.now());
      if (r.status === 401) { location.replace("login.html"); throw new Error("세션 만료"); }
      if (!r.ok) throw new Error("데이터를 불러오지 못했습니다.");
      return r.json();
    }
    const key = await sessionContentKey();
    const blob = await fetch("data/bundle.enc?_=" + Date.now(), { cache: "no-store" }).then((r) => {
      if (!r.ok) throw new Error("데이터 파일(bundle.enc)을 찾을 수 없습니다.");
      return r.json();
    });
    const pt = await aesDecrypt(key, blob.iv, blob.ct);
    return JSON.parse(dec.decode(pt));
  }

  /* 관리자: 현재 세션 키로 번들 재암호화 → bundle.enc 에 넣을 문자열 반환 */
  async function encryptBundle(obj) {
    const key = await sessionContentKey();
    const iv = crypto.getRandomValues(new Uint8Array(12));
    const ct = await crypto.subtle.encrypt({ name: "AES-GCM", iv }, key, enc.encode(JSON.stringify(obj)));
    return JSON.stringify({ iv: b64e(iv), ct: b64e(ct) });
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
    loadBundle, wrapForNewUser, encryptBundle,
  };
})();
