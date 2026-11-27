/* 팀장 리더십 리플렉션 v4 — 사용자 화면 21개 (SPEC 3장)
   - 문구는 모두 /api/content(content.json)에서 받는다
   - 별명은 서버로 보내지 않는다(이 브라우저 저장소에만). 직접 쓴 문장 안의 별명은 ○○로 바꿔 보낸다
   - 화면 이동은 앱 안에서 처리하고, 브라우저 뒤로 가기는 앱의 '이전 화면'으로 연결한다 */
'use strict';
(() => {
  const $screen = document.getElementById('screen');
  const $toast = document.getElementById('toast');
  const $themeColor = document.getElementById('theme-color');
  const reduceMotion = () => !!(window.matchMedia && matchMedia('(prefers-reduced-motion: reduce)').matches);

  // ----------------------------------------------------------------------- //
  // 브라우저 저장소 (쓸 수 없으면 메모리로 대신)
  // ----------------------------------------------------------------------- //
  const Store = (() => {
    const PREFIX = 'lr4.';
    const mem = {};
    let ok = true;
    try { const t = PREFIX + '__t'; localStorage.setItem(t, '1'); localStorage.removeItem(t); } catch (e) { ok = false; }
    return {
      available: ok,
      get(k) { try { return ok ? localStorage.getItem(PREFIX + k) : (k in mem ? mem[k] : null); } catch (e) { return k in mem ? mem[k] : null; } },
      set(k, v) { mem[k] = v; try { if (ok) localStorage.setItem(PREFIX + k, v); } catch (e) { /* 무시 */ } },
      del(k) { delete mem[k]; try { if (ok) localStorage.removeItem(PREFIX + k); } catch (e) { /* 무시 */ } },
      keys() {
        const out = new Set(Object.keys(mem));
        try { if (ok) for (let i = 0; i < localStorage.length; i++) { const k = localStorage.key(i); if (k && k.startsWith(PREFIX)) out.add(k.slice(PREFIX.length)); } } catch (e) { /* 무시 */ }
        return [...out];
      },
    };
  })();

  function uuid4() {
    if (window.crypto && crypto.randomUUID) return crypto.randomUUID();
    const b = crypto.getRandomValues(new Uint8Array(16));
    b[6] = (b[6] & 0x0f) | 0x40; b[8] = (b[8] & 0x3f) | 0x80;
    const x = Array.from(b, (v) => v.toString(16).padStart(2, '0')).join('');
    return `${x.slice(0, 8)}-${x.slice(8, 12)}-${x.slice(12, 16)}-${x.slice(16, 20)}-${x.slice(20)}`;
  }
  function randInt(n) { return crypto.getRandomValues(new Uint32Array(1))[0] % n; }
  function shuffle(arr) { const a = arr.slice(); for (let i = a.length - 1; i > 0; i--) { const j = randInt(i + 1); [a[i], a[j]] = [a[j], a[i]]; } return a; }

  // ----------------------------------------------------------------------- //
  // 상태
  // ----------------------------------------------------------------------- //
  let C = null;      // content.json
  let UI = null;     // content.ui
  let ITEMS = {};    // 문항 ID → 문항
  const S = {
    entryKey: null, deviceId: null, participantId: null,
    fromRecommend: false,                       // 이번에 동료 추천 링크(ref=recommend)로 열었는지
    version: '', settings: {},
    me: null,                                   // {leader_name, leader_type, option_order, locked}
    step1: { reviews: {}, keywords: [], done: false },
    check: null,                                // 진행 중(또는 방금 끝낸) 점검
    nick: '',                                   // 이 점검의 별명(브라우저에만)
    ln: null, kw: null, md: null, act: null,    // 화면별 입력 중 상태
    played: { thumbs: new Set(), confetti: new Set() },
  };
  const nickKey = (seq) => `nick.${S.participantId}.${seq}`;
  const actKey = (seq) => `act.${S.participantId}.${seq}`;

  // ----------------------------------------------------------------------- //
  // API: 모든 요청에 입장 키·참여자 ID·기기 ID 를 함께 보낸다
  // ----------------------------------------------------------------------- //
  async function api(method, path, body) {
    const headers = { Accept: 'application/json' };
    if (S.entryKey) headers['X-Entry-Key'] = S.entryKey;
    if (S.participantId) headers['X-Participant-Id'] = S.participantId;
    if (S.deviceId) headers['X-Device-Id'] = S.deviceId;
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    try {
      const res = await fetch(path, { method, headers, cache: 'no-store', body: body === undefined ? undefined : JSON.stringify(body) });
      let data = {};
      try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
      const out = { ok: res.ok, status: res.status, data: data || {} };
      if (sessionGone(out)) out.gone = true;
      return out;
    } catch (e) {
      return { ok: false, status: 0, data: {} };
    }
  }

  // ----------------------------------------------------------------------- //
  // DOM 도우미 (사용자 입력은 항상 텍스트 노드로만 넣는다)
  // ----------------------------------------------------------------------- //
  function h(tag, props, ...kids) {
    const el = document.createElement(tag);
    if (props) {
      for (const [k, v] of Object.entries(props)) {
        if (v === null || v === undefined || v === false) continue;
        if (k === 'class') el.className = v;
        else if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
        else if (k === 'value') el.value = v;
        else el.setAttribute(k, v === true ? '' : String(v));
      }
    }
    return add(el, kids);
  }
  function add(el, kids) {
    for (const k of kids.flat(Infinity)) {
      if (k === null || k === undefined || k === false) continue;
      el.append(k instanceof Node ? k : document.createTextNode(String(k)));
    }
    return el;
  }
  function fmt(s, vars) { return String(s).replace(/\{(\w+)\}/g, (m, k) => (vars && Object.prototype.hasOwnProperty.call(vars, k) ? String(vars[k]) : m)); }
  /** 문구 → 노드. [별명]·[리더명] 은 토큰으로, \n 은 줄바꿈으로, {x} 는 vars 로 */
  function rich(text, vars, names) {
    const frag = document.createDocumentFragment();
    if (text === null || text === undefined) return frag;
    const nick = names && 'nick' in names ? names.nick : S.nick;
    const leader = names && 'leader' in names ? names.leader : (S.me ? S.me.leader_name : '');
    for (const part of String(text).split(/(\[별명\]|\[리더명\])/)) {
      if (part === '[별명]') frag.append(h('span', { class: 'tok-nick' }, nick || '○○'));
      else if (part === '[리더명]') frag.append(h('span', { class: 'tok-leader' }, leader));
      else fmt(part, vars).split('\n').forEach((ln, i) => { if (i) frag.append(h('br')); if (ln) frag.append(ln); });
    }
    return frag;
  }
  const ICONS = {
    back: '<svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 6l-6 6 6 6"/></svg>',
    arrow: '<svg width="64" height="32" viewBox="0 0 64 32" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 16H58"/><path d="M46 6L59 16L46 26"/></svg>',
    regen: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 11a8 8 0 1 0-2.3 5.7"/><path d="M20 5v6h-6"/></svg>',
    plus: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg>',
    check: '<svg width="56" height="56" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5"/></svg>',
    again: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 12a9 9 0 1 0 3-6.7"/><path d="M3 4v5h5"/></svg>',
    download: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 4v11"/><path d="M7 10l5 5 5-5"/><path d="M5 19h14"/></svg>',
    link: '<svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M10 13a5 5 0 0 0 7.07 0l3-3a5 5 0 0 0-7.07-7.07l-1.5 1.5"/><path d="M14 11a5 5 0 0 0-7.07 0l-3 3a5 5 0 0 0 7.07 7.07l1.5-1.5"/></svg>',
  };
  function icon(name) { const t = document.createElement('template'); t.innerHTML = ICONS[name]; return t.content.firstElementChild; }

  let toastTimer = null;
  function toast(text, ms) {
    $toast.textContent = text; $toast.hidden = false;
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => { $toast.hidden = true; }, ms || 2000);
  }
  function messageOnly(text) {
    document.body.className = 'theme-white';
    // 화면 하단 표시: 버전 대신 만든 팀(2026-11-27). 버전은 /api/health 로만 확인
    $screen.replaceChildren(h('p', { class: 'msg-only' }, text), h('p', { class: 'hint', style: 'padding-bottom:12px' }, S.credit || ''));
  }

  // ----------------------------------------------------------------------- //
  // 공통 조각
  // ----------------------------------------------------------------------- //
  function header(title, opt) {
    const o = opt || {};
    if (o.center) return h('header', { class: 'hdr center' }, h('span', { class: 'hdr-title' }, title));
    const left = o.back === false
      ? h('span', { class: 'hdr-side', 'aria-hidden': 'true' })
      : h('button', { type: 'button', class: 'icon-btn', 'aria-label': o.backAria || UI.back, onclick: () => back() }, icon('back'));
    return h('header', { class: 'hdr' }, left, h('span', { class: 'hdr-title' }, title || ''), h('span', { class: 'hdr-side' }, o.side || ''));
  }
  function progress(n, total, aria) {
    return h('div', { class: 'progress', role: 'progressbar', 'aria-label': aria, 'aria-valuemin': 0, 'aria-valuemax': total, 'aria-valuenow': n },
      h('div', { style: `width:${Math.round((n / total) * 100)}%` }));
  }
  /** 하단 주 버튼. 막힌 상태는 aria-disabled(누르면 아무 일도 하지 않음) */
  function cta(label, onClick, opt) {
    const o = opt || {};
    const btn = h('button', { type: 'button', class: 'cta' }, label);
    const hint = o.hint ? h('p', { class: 'hint' }, o.hint) : null;
    btn.addEventListener('click', () => {
      if (btn.getAttribute('aria-disabled') === 'true') { Events.track('blocked_next_tap', { screen: cur }); return; }
      if (btn.getAttribute('aria-busy') === 'true') return;
      busy(btn, () => onClick(btn));
    });
    const setEnabled = (on) => {
      if (on) btn.removeAttribute('aria-disabled'); else btn.setAttribute('aria-disabled', 'true');
      if (hint) hint.hidden = !!on;
    };
    setEnabled(o.enabled !== false);
    return { el: h('div', { class: 'ftr' }, btn, hint), btn, setEnabled };
  }
  /** 버튼 처리(저장 대기) 중에 사용자가 뒤로 가기 등으로 화면을 옮기면, 저장이 끝난 뒤 앞으로 가지 않도록 go() 가 확인한다 */
  let navGuard = null;
  async function busy(btn, fn) {
    if (btn.getAttribute('aria-busy') === 'true') return;
    btn.setAttribute('aria-busy', 'true');
    const mine = { screen: cur };
    navGuard = mine;
    try { await fn(); } finally { btn.removeAttribute('aria-busy'); if (navGuard === mine) navGuard = null; }
  }
  /** 안내·리더명·퀵 리뷰 공통 화살표(120×64) + 아래 44px 칸 */
  function arrowFooter(aria, onClick, opt) {
    const o = opt || {};
    const btn = h('button', { type: 'button', class: 'arrow', 'aria-label': aria }, h('span', { class: 'arrow-move' }, icon('arrow')));
    btn.addEventListener('click', () => {
      if (btn.getAttribute('aria-disabled') === 'true') { Events.track('blocked_next_tap', { screen: cur }); return; }
      if (btn.getAttribute('aria-busy') === 'true') return;
      busy(btn, () => onClick(btn));
    });
    const setEnabled = (on, disabledAria) => {
      if (on) { btn.removeAttribute('aria-disabled'); btn.setAttribute('aria-label', aria); }
      else { btn.setAttribute('aria-disabled', 'true'); btn.setAttribute('aria-label', disabledAria || aria); }
    };
    if (o.enabled === false) setEnabled(false, o.disabledAria);
    const spacer = h('span', { class: 'spacer', 'aria-hidden': o.credit ? null : 'true' }, o.credit && S.credit ? S.credit : null);
    return { el: h('div', { class: 'ftr-arrow' }, btn, spacer), btn, setEnabled };
  }
  function quadrant(highlight) {
    const Q = C.quadrants;
    return h('div', { class: 'quad' },
      h('span', { class: 'axis' }, UI.axis_care),
      h('div', { class: 'q' }, ['re', 'rc', 'mi', 'oa'].map((k) =>
        h('div', { class: k === highlight ? 'on' : null }, h('strong', null, Q[k].label), h('span', null, Q[k].sub)))),
      h('span', { class: 'axis r' }, UI.axis_challenge));
  }
  function flowLine(parts) {
    const out = [];
    parts.forEach((p, i) => {
      if (i) out.push(' ', h('span', { class: 'arr' }, '→'), ' ');
      const m = /^([A-Z])(\S*)(.*)$/.exec(p);
      out.push(m ? [h('b', null, m[1]), m[2] + m[3]] : p);
    });
    return h('span', { class: 'flow' }, out);
  }
  /** 핵심 메시지 상세(content.reviews[].sections) */
  function section(sec) {
    const kids = [];
    if (sec.heading) kids.push(h('h2', { class: 'sec-h' }, sec.heading));
    if (sec.kind === 'box') kids.push(h('p', { class: 'box' }, rich(sec.text)));
    else if (sec.kind === 'text') kids.push(h('p', { class: 'lead', style: 'font-size:14px' }, rich(sec.text)));
    else if (sec.kind === 'quadrant') kids.push(quadrant(sec.highlight));
    else if (sec.kind === 'grid') {
      kids.push(h('div', { class: 'grid-n', style: `grid-template-columns:repeat(${sec.columns || 2},minmax(0,1fr))` },
        sec.items.map((it) => h('div', { class: 'cell' }, h('span', { class: 't' }, it.title), h('span', { class: 's' }, it.sub)))));
      if (sec.note) kids.push(h('p', { class: 'lead', style: 'font-size:14px' }, rich(sec.note)));
    } else if (sec.kind === 'steps') {
      kids.push(h('ol', { class: 'steps' }, sec.items.map((t, i) => h('li', null, h('span', { class: 'num' }, i + 1), t))));
    } else if (sec.kind === 'cards') {
      kids.push(h('div', { style: 'display:flex;flex-direction:column;gap:6px' }, sec.items.map((it) =>
        h('div', { class: 'cell' }, h('span', { class: 't' }, it.title),
          it.flow ? flowLine(it.flow) : null,
          it.sub ? h('span', { class: 's' }, it.sub) : null,
          it.note ? h('span', { class: 'n' }, it.note) : null))));
    }
    return h('section', { class: 'sec' }, kids);
  }
  function phraseText(key) {
    if (!key) return '';
    const [kind, id] = key.split(':');
    if (kind === 'situation') return C.situation_phrases[id] || '';
    if (kind === 'item') return (ITEMS[id] && ITEMS[id].phrase) || '';
    if (kind === 'general') return (C.general_phrases || {})[id] || '';
    return '';
  }
  const situationsByPriority = () => C.member.situations.filter((s) => s.id !== 'none').sort((a, b) => a.priority - b.priority);
  function topSituationLabel(sits) {
    if (!sits || !sits.length) return null;
    if (sits[0] === 'none') return (C.member.situations.find((s) => s.id === 'none') || {}).label;
    const top = situationsByPriority().find((s) => sits.includes(s.id));
    return top ? top.label : null;
  }
  /** 서버 anonymize.to_call_form 과 같은 규칙: '…리더님'은 '님'을 떼고, '리더'로 끝나지 않으면 ' 리더'를 붙인다 */
  const callForm = (name) => {
    let n = name.replace(/\s+/g, ' ').trim();
    if (n.endsWith('리더님')) n = n.slice(0, -1);
    return n.endsWith('리더') ? n : `${n} 리더`;
  };
  const charLen = (s) => [...s].length;   // 서버(Python len)와 같게 글자(코드 포인트) 수로 센다
  /** 리더명이 사람 이름(띄어쓰기 없는 한글 3글자, 흔한 성으로 시작)처럼 보이는지 — 막지 않고 안내만(2026-11-27).
   *  서버(anonymize.looks_like_person_name)와 같은 규칙, 같은 데이터(content.leader_name.custom.name_like) */
  function looksLikeName(text, rule) {
    let base = String(text || '').replace(/\s+/g, ' ').trim();
    if (base.endsWith('리더')) base = base.slice(0, -2).trim();
    if (!rule || !/^[가-힣]{3}$/.test(base)) return false;
    return rule.surnames.includes(base[0]) && !rule.exclude_endings.includes(base[2]);
  }
  function blockedPattern(text) {
    const t = (text || '').normalize('NFKC');
    return /[\w.+-]+\s*@\s*[\w-]+(\.[\w-]+)+/.test(t) || /(\+?\d{1,3}[\s.-]?)?\(?\d{2,4}\)?[\s.-]\d{3,4}[\s.-]\d{4}/.test(t) || /\d{6,}/.test(t);
  }
  /** 직접 쓴 문장 안의 별명 → ○○. 별명이 한 글자면 다른 낱말 속 같은 글자('수' → '수고')까지 바꾸지 않도록
   *  앞이 글자가 아니고, 뒤가 끝·글자 아님·조사/호칭일 때만 바꾼다(애매하면 바꾸는 쪽: 별명 노출 방지가 우선) */
  const NICK_AFTER = '님|씨|께|에게|한테|이|가|은|는|을|를|의|도|와|과|랑|아|야|에';
  function maskNick(text) {
    const n = (S.nick || '').trim();
    if (!n) return text;
    if (charLen(n) >= 2) return text.split(n).join('○○');
    const esc = n.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    const re = new RegExp(`(^|[^\\p{L}\\p{N}])${esc}(?=$|[^\\p{L}\\p{N}]|${NICK_AFTER})`, 'gu');
    return text.replace(re, (m, pre) => `${pre}○○`);
  }
  function genNick() {
    const g = C.member.nickname_generator;
    const pick = (a) => a[randInt(a.length)];
    return fmt(g.pattern, { adj: pick(g.adj), place: pick(g.place), animal: pick(g.animal) }).slice(0, C.member.nickname_maxlength);
  }
  const checkLocked = () => !!(S.check && S.check.status === 'done');

  // ----------------------------------------------------------------------- //
  // 이벤트(SPEC 5.3): 모아서 보내고, 화면을 떠날 때 남은 것을 keepalive 로 보낸다
  // ----------------------------------------------------------------------- //
  const Events = (() => {
    const q = [];
    let timer = null;
    const sessionId = (() => {
      try { let s = sessionStorage.getItem('lr4.sid'); if (!s) { s = uuid4(); sessionStorage.setItem('lr4.sid', s); } return s; } catch (e) { return uuid4(); }
    })();
    const MAX_QUEUE = 300;
    const requeue = (batch) => { q.unshift(...batch.slice(0, Math.max(0, MAX_QUEUE - q.length))); };
    function flush(keepalive) {
      // 입장 키가 있거나 '입장 키 없이 들어오기'가 켜져 있으면 보낸다(2026-11-27 수정: 키 없는 기본 주소에서
      // 이벤트가 하나도 저장되지 않던 문제). 둘 다 아니면 서버가 거절하므로 보내지 않는다
      if (!q.length || !(S.entryKey || (S.settings && S.settings.open_entry))) return;
      // 이벤트가 생긴 때의 참여자 ID 로 보낸다(리더명을 정하기 전 이벤트는 기기 ID 로만 남게)
      const pid = q[0].pid;
      let n = 0;
      while (n < q.length && n < 50 && q[n].pid === pid) n += 1;
      const batch = q.splice(0, n);
      const headers = { 'Content-Type': 'application/json' };
      if (S.entryKey) headers['X-Entry-Key'] = S.entryKey;
      if (pid) headers['X-Participant-Id'] = pid;
      if (S.deviceId) headers['X-Device-Id'] = S.deviceId;
      const events = batch.map((e) => ({ name: e.name, props: e.props, session_id: e.session_id, check_seq: e.check_seq }));
      fetch('/api/events', { method: 'POST', headers, keepalive: !!keepalive, body: JSON.stringify({ events }) })
        .then((res) => { if (res.status >= 500 && !keepalive) requeue(batch); })   // 서버 오류면 나중에 다시
        .catch(() => { if (!keepalive) requeue(batch); });                           // 연결이 끊겼으면 나중에 다시
      if (q.length) flush(keepalive);
    }
    function track(name, props, checkSeq) {
      const seq = checkSeq !== undefined ? checkSeq : (S.check ? S.check.seq : undefined);
      const pr = Object.assign({}, props || {});
      if (name === 'error') pr.browser = browserFamily();
      q.push({ name, props: pr, session_id: sessionId, check_seq: seq === null ? undefined : seq, pid: S.participantId || null });
      if (q.length > MAX_QUEUE) q.shift();
      clearTimeout(timer);
      timer = setTimeout(() => flush(false), 800);
    }
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') flush(true); });
    window.addEventListener('pagehide', () => flush(true));
    return { track, flush, sessionId };
  })();
  /** 브라우저 종류(오류 기록용). User-Agent 원문은 보내지 않는다 */
  function browserFamily() {
    const ua = navigator.userAgent || '';
    const tests = [['kakaotalk', /KAKAOTALK/i], ['naver', /NAVER\(inapp|NAVER\//i], ['line', /\bLine\//i], ['teams', /Teams\//i],
      ['instagram', /Instagram/i], ['facebook', /FBAN|FBAV/i], ['webview', /; wv\)/],
      // iOS 앱 안 브라우저(WKWebView)는 UA 에 'Safari/' 가 없다(Safari·Chrome·Edge·Firefox iOS 에는 있음)
      ['ios_webview', /\b(iPhone|iPad|iPod)\b(?!.*Safari\/)/], ['samsung', /SamsungBrowser/i],
      ['whale', /Whale\//i], ['edge', /Edg(e|A|iOS)?\//i], ['opera', /OPR\//i], ['firefox', /Firefox|FxiOS/i],
      ['chrome', /Chrome|CriOS/i], ['safari', /Safari/i]];
    for (const [name, re] of tests) if (re.test(ua)) return name;
    return 'other';
  }
  /** 메신저·메일 앱 안 브라우저: PDF 내려받기·클립보드·저장소가 막히거나 바깥 브라우저와 기록이 나뉠 수 있다(2026-11-27) */
  const INAPP = new Set(['kakaotalk', 'naver', 'line', 'teams', 'instagram', 'facebook', 'webview', 'ios_webview']);
  const inApp = () => INAPP.has(browserFamily());

  /** 화면별 머문 시간: 보이는 동안만 세고, 입력 없이 기준 시간(기본 3분)이 지나면 그 뒤는 자른다 */
  const Dwell = (() => {
    let v = null;   // 지금 화면의 방문 {screen, visit, enteredAt, acc, segStart, lastInput, visible, seq}
    const cutoffMs = () => (Number(S.settings.dwell_cutoff_sec) || 180) * 1000;
    function account(t) {
      if (!v || !v.visible) return;
      const end = Math.min(t, v.lastInput + cutoffMs());
      if (end > v.segStart) v.acc += end - v.segStart;
      v.segStart = Math.max(v.segStart, t);
    }
    function emit(t, final) {
      Events.track('screen_view', {
        screen: v.screen, visit: v.visit, final,
        entered_at: new Date(v.enteredAt).toISOString(), left_at: new Date(t).toISOString(), visible_ms: Math.round(v.acc),
      }, v.seq !== undefined ? v.seq : (S.check ? S.check.seq : null));   // 구성원 떠올리기는 떠날 때 생긴 점검에 붙인다
    }
    function leave() { if (!v) return; const t = Date.now(); account(t); emit(t, true); v = null; }
    function enter(screen) {
      leave();
      const t = Date.now();
      v = { screen, visit: uuid4(), enteredAt: t, acc: 0, segStart: t, lastInput: t,
        visible: document.visibilityState !== 'hidden', seq: S.check ? S.check.seq : undefined };
    }
    function input() { if (!v) return; const t = Date.now(); account(t); v.lastInput = t; }
    function hide() { if (!v || !v.visible) return; const t = Date.now(); account(t); v.visible = false; emit(t, false); Events.flush(true); }
    function unhide() { if (!v || v.visible) return; const t = Date.now(); v.visible = true; v.segStart = t; v.lastInput = t; }
    for (const ev of ['pointerdown', 'keydown', 'input', 'touchstart', 'wheel', 'scroll']) document.addEventListener(ev, input, { passive: true, capture: true });
    document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'hidden') hide(); else unhide(); });
    window.addEventListener('pagehide', hide);
    return { enter, current: () => (v ? v.screen : null) };
  })();

  function dateStamp(sep) {
    const d = new Date();
    const p = (n) => String(n).padStart(2, '0');
    return [d.getFullYear(), p(d.getMonth() + 1), p(d.getDate())].join(sep === undefined ? '' : sep);
  }

  // ----------------------------------------------------------------------- //
  // 저장
  // ----------------------------------------------------------------------- //
  async function saveStep1(body) {
    if (S.step1.done) return true;
    const r = await api('PUT', '/api/step1', body);
    if (r.ok) { S.step1 = { reviews: r.data.reviews || {}, keywords: r.data.keywords || [], done: !!r.data.done, last_screen: r.data.last_screen }; return true; }
    if (r.status === 409) { S.step1.done = true; return true; }
    saveFailed();
    return false;
  }
  /** 저장이 안 됐을 때: 안내하고, 다시 누르게 되므로 save_retry 로 남긴다 */
  function saveFailed(message) {
    if (gone) return;
    toast(message || UI.save_error, 3000);
    Events.track('error', { kind: 'save_retry' });
  }
  /** 진행 중에 마감·입장 키 변경(403)이나 관리자의 참여자 삭제(404)가 생기면 '연결 확인'을 되풀이하지 않고 처리한다.
   *  문구를 받기 전(시작)과 이어 하기 확인(/api/me) 단계는 boot()·resume() 이 따로 처리한다 */
  let gone = false;
  function sessionGone(r) {
    if (gone) return true;
    if (!C) return false;
    const err = r.data && r.data.error;
    if (r.status === 403 && (err === 'closed' || err === 'entry_denied')) {
      gone = true;
      if (err === 'entry_denied') Store.del('entry_key');
      messageOnly(r.data.message || UI.load_error);
      return true;
    }
    if (r.status === 404 && err === 'participant_not_found' && S.me) {
      gone = true;
      Store.del('participant_id');   // 관리자가 지운 참여자: 처음부터 새로 시작
      location.reload();
      return true;
    }
    return false;
  }
  async function saveCheck(body) {
    if (!S.check || checkLocked()) return true;
    const seq = S.check.seq;
    const r = await api('PATCH', `/api/checks/${seq}`, body);
    if (r.ok) { S.check = Object.assign({}, S.check, r.data, { results: (r.data.results && r.data.results.r1) ? r.data.results : S.check.results }); return true; }
    if (r.gone) return false;
    if (r.status === 409 && r.data.error === 'check_done') {
      // 앞선 요청(완료)은 저장됐는데 응답을 받지 못한 경우: 저장된 결과로 맞추고 넘어간다.
      // (그 사이 보기를 바꿔 다시 눌렀다면 서버에는 앞선 보기가 남아 있고, 완료 화면은 이번 보기를 보여 준다)
      const res = await api('GET', `/api/checks/${seq}/result`);
      S.check = Object.assign({}, S.check, { status: 'done', last_screen: 'done' },
        res.ok ? { results: res.data.results, quadrant: res.data.quadrant } : {},
        body.complete ? { action_type: body.action_type, action_key: body.action_key || null, action_text: body.action_text || null } : {});
      return true;
    }
    if (r.status === 422 && r.data.error === 'bad_action_key') {
      // 답이 바뀌어 고른 보기가 결과에서 빠진 경우: 결과를 다시 정리한다
      S.check.results = null; S.act = null;
      go('analyzing');
      return false;
    }
    if (r.status === 422 && r.data.message && r.data.error === 'bad_action_text') toast(r.data.message, 3000);
    else saveFailed();
    return false;
  }

  // ----------------------------------------------------------------------- //
  // 화면 이동
  // ----------------------------------------------------------------------- //
  const THEME = { notice: 'gradient', intro: 'gradient', check_intro: 'gradient', done: 'gradient', analyzing: 'black' };
  const THEME_COLOR = { gradient: '#96C93E', black: '#000000', white: '#ffffff' };
  const STEP2 = /^(check_\d|analyzing|result_\d|action|done)$/;
  let cur = null;
  let token = 0;
  // 화면이 바뀐 직후의 탭은 버린다: 같은 자리 버튼을 빠르게 두 번 눌러 다음 화면의 답이 눌리거나 결과 페이지를 건너뛰지 않게
  let shownAt = 0;
  const TAP_GUARD_MS = 350;
  $screen.addEventListener('click', (e) => {
    if (Date.now() - shownAt < TAP_GUARD_MS) { e.stopPropagation(); e.preventDefault(); }
  }, true);

  /** 휴대폰에서 주소창 가리기(2026-11-27): 버튼을 누를 때 전체 화면으로 바꾼다(안드로이드 크롬·삼성 인터넷).
   *  전체 화면은 사용자가 누른 순간에만 요청할 수 있다. 뒤로 가기 등으로 풀리면 다음 누를 때 다시 바꾼다.
   *  아이폰은 웹 페이지가 전체 화면을 쓸 수 없어(요청 함수 없음) 건너뛴다. 대신 페이지 자체가 스크롤되게 해
   *  밀어 올리면 브라우저가 주소창·하단 막대를 접는다(app.css 화면 틀). PC·태블릿은 바꾸지 않는다 */
  const Fullscreen = (() => {
    const el = document.documentElement;
    const phone = () => !!(window.matchMedia && matchMedia('(pointer: coarse)').matches)
      && Math.min(window.screen.width, window.screen.height) < 600;
    function request() {
      try {
        if (!el.requestFullscreen || !document.fullscreenEnabled || document.fullscreenElement || !phone()) return;
        const p = el.requestFullscreen({ navigationUI: 'hide' });
        if (p && p.catch) p.catch(() => { /* 앱 안 브라우저 등에서 거절되면 그대로 둔다 */ });
      } catch (e) { /* 무시 */ }
    }
    return { request };
  })();
  document.addEventListener('click', (e) => {
    if (e.target && e.target.closest && e.target.closest('button, a')) Fullscreen.request();
  }, true);

  function show(screen, opt) {
    const o = opt || {};
    if (gone) return;
    if (!o.keep) shownAt = Date.now();
    // 순서가 맞지 않는 화면 요청은 갈 수 있는 화면으로 바꾼다
    if (screen !== 'notice' && screen !== 'leader_name' && !S.me) screen = 'notice';
    if ((screen === 'check_intro' || screen === 'member' || STEP2.test(screen)) && !S.step1.done) screen = 'intro';
    if (STEP2.test(screen) && !S.check) screen = 'member';
    if (/^(result_\d|action|done)$/.test(screen) && !(S.check.results && S.check.results.r1)) screen = 'analyzing';
    token += 1;
    if (!(o.keep && screen === cur)) Dwell.enter(screen);   // 같은 화면 다시 그리기는 같은 방문
    cur = screen;
    const theme = THEME[screen] || 'white';
    document.body.className = 'theme-' + theme;
    $themeColor.setAttribute('content', THEME_COLOR[theme]);
    const nodes = [].concat(RENDER[screen](o, token)).filter(Boolean);
    if (o.banner) {
      const i = nodes.findIndex((n) => n.classList && n.classList.contains('main'));
      nodes.splice(i < 0 ? 1 : i, 0, bannerEl(o.banner));
    }
    // 페이지 자체가 스크롤된다(2026-11-27, 휴대폰 브라우저가 주소창을 접을 수 있게). 같은 화면 다시 그리기는 위치 유지
    const keepScroll = o.keep ? window.scrollY : 0;
    $screen.replaceChildren(...nodes);
    window.scrollTo(0, keepScroll);
    if (!o.initial && !o.keep) {
      const h1 = $screen.querySelector('h1');
      if (h1) { h1.setAttribute('tabindex', '-1'); h1.focus({ preventScroll: true }); }
    }
  }
  const go = (screen, opt) => {
    if (navGuard && navGuard.screen !== cur) return;   // 저장을 기다리는 사이 다른 화면으로 옮겨 감
    show(screen, opt);
    if (navGuard) navGuard.screen = cur;
  };

  function prevOf(s) {
    if (s === 'notice') return null;
    if (s === 'leader_name') return 'notice';
    if (s === 'intro') return S.me && S.me.locked ? null : 'leader_name';
    let m = /^review_(\d)$/.exec(s);
    if (m) return m[1] === '1' ? 'intro' : `review_${m[1] - 1}`;
    if (s === 'keyword') return 'review_5';
    if (s === 'check_intro') return 'keyword';
    if (s === 'member') return 'check_intro';
    m = /^check_(\d)$/.exec(s);
    if (m) return m[1] === '1' ? 'member' : `check_${m[1] - 1}`;
    if (s === 'analyzing' || s === 'result_1') return 'check_5';   // 결과에서 뒤로 가면 결과 정리를 건너뜀
    if (s === 'result_2') return 'result_1';
    if (s === 'result_3') return 'result_2';
    if (s === 'action') return 'result_3';
    if (s === 'done') return 'action';
    return null;
  }
  function back() {
    const p = prevOf(cur);
    if (!p) return false;
    Events.track('back_nav', { from: cur, to: p });
    if (cur === 'result_1' && p === 'check_5') Events.track('result_back_to_checklist');
    show(p, { back: true });
    return true;
  }
  /** 브라우저(안드로이드) 뒤로 가기 → 앱의 이전 화면. 더 갈 곳이 없으면 실제로 나간다 */
  function initHistory() {
    history.replaceState({ lr: 'base' }, '');
    history.pushState({ lr: 'top' }, '');
    window.addEventListener('popstate', () => {
      if (back()) history.pushState({ lr: 'top' }, '');
      else history.back();
    });
  }

  /** 이어 하기 안내: 누구의 기록인지 보여 주고, 같은 기기를 쓰는 다른 리더가 새로 시작할 수 있게 한다 */
  function bannerEl(b) {
    return h('div', { class: 'resume', role: 'status' }, h('span', null, rich(UI.resume_banner)),
      h('span', { class: 'acts' },
        b.restart ? h('button', { type: 'button', onclick: restartCheck }, UI.restart) : null,
        h('button', { type: 'button', onclick: switchLeader }, UI.switch_leader)));
  }
  /** 공용 기기: 이 브라우저에 남은 앞 리더의 기록(참여자 ID·별명·직접 쓴 말)만 지우고 처음부터. 서버 기록은 그대로 */
  function switchLeader() {
    if (!S.me || !window.confirm(fmt(UI.switch_leader_confirm, { leader: S.me.leader_name }))) return;
    Events.track('leader_switch', {}, null);
    Events.flush(true);
    const pid = S.participantId;
    Store.keys().filter((k) => k.startsWith(`nick.${pid}.`) || k.startsWith(`act.${pid}.`) || k === `kworder.${pid}`).forEach((k) => Store.del(k));
    Store.del('participant_id');
    location.reload();
  }
  async function restartCheck() {
    if (!S.check || !window.confirm(UI.restart_confirm)) return;
    const r = await api('DELETE', `/api/checks/${S.check.seq}`);
    if (!r.ok && r.status !== 404) { saveFailed(); return; }
    if (r.gone) return;
    const seq = S.check.seq;
    Store.del(nickKey(seq)); Store.del(actKey(seq));
    // 다시 시작하면 같은 순번을 다시 쓰므로 엄지척·꽃가루를 한 번 더 보여 줄 수 있게 지운다
    S.played.thumbs.delete(seq); S.played.confetti.delete(seq);
    S.check = null; S.nick = ''; S.md = null; S.act = null;
    show('member');
  }

  // ----------------------------------------------------------------------- //
  // 화면 21개
  // ----------------------------------------------------------------------- //
  const RENDER = {};

  // 1 안내 ------------------------------------------------------------------
  RENDER.notice = () => {
    const n = C.notice;
    const dur = S.settings.duration_min ? fmt(n.duration, { n: S.settings.duration_min }) : null;
    return [
      header(UI.brand, { center: true }),
      h('main', { class: 'main center' },
        h('p', { class: 'over' }, n.eyebrow),
        h('h1', { class: 'h1 lg' }, rich(n.title)),
        h('p', { class: 'lead' }, rich(n.body, { cycle_name: S.settings.cycle_name || '' })),
        h('p', { style: 'font-size:13px;line-height:1.6;font-weight:700;white-space:pre-line' }, dur, dur ? h('br') : null, n.resume_note),
        // 앱 안 브라우저로 처음 연 경우만: 시작 전에 바깥 브라우저로 옮기도록(이미 기록이 있으면 옮기면 이어 하기가 끊겨 안내하지 않음)
        inApp() && !S.participantId ? h('p', { class: 'inapp-hint' }, n.inapp_hint) : null),
      // 리더 기록이 있는데 아직 불러오지 않았으면(추천 링크로 안내부터 연 경우) 멈춘 곳으로 이어 간다
      arrowFooter(UI.notice_next_aria, () => (S.participantId && !S.me ? resume() : go('leader_name')), { credit: true }).el,
    ];
  };

  // 2 리더명 정하기 ----------------------------------------------------------
  RENDER.leader_name = () => {
    const L = C.leader_name;
    const labels = Object.fromEntries(L.options.map((o) => [o.type, o.label]));
    if (!S.ln) {
      // 저장된 이름은 ' 리더'가 붙은 형태라, 그대로 넣으면 최대 글자 수를 넘을 수 있다 → 그럴 때만 붙은 ' 리더'를 뗀다
      const typed = (n) => (charLen(n) > L.custom.max && n.endsWith(' 리더') ? n.slice(0, -3) : n);
      S.ln = S.me
        ? { order: S.me.option_order, type: S.me.leader_type, custom: S.me.leader_type === 'custom' ? typed(S.me.leader_name) : '' }
        : { order: shuffle(L.options.map((o) => o.type)), type: null, custom: '' };
    }
    const st = S.ln;
    const rows = {};
    const radio = (type, label) => {
      const b = h('button', { type: 'button', role: 'radio', class: 'radio-row', 'aria-checked': 'false' }, h('span', null, label), h('span', { class: 'dot', 'aria-hidden': 'true' }));
      b.addEventListener('click', () => {
        if (st.type !== type) st.picks = (st.picks || 0) + 1;
        st.type = type; update();
        if (type === 'custom') input.focus();
      });
      rows[type] = b;
      return b;
    };
    const input = h('input', { id: 'lname', class: 'input', type: 'text', maxlength: L.custom.max, value: st.custom, placeholder: L.custom.placeholder, autocomplete: 'off', 'aria-describedby': 'lname-help lname-err' });
    const err = h('p', { id: 'lname-err', class: 'err', hidden: true });
    // 사람 이름처럼 보이면 안내만(막지 않음, 2026-11-27)
    const nameHint = h('p', { id: 'lname-hint', class: 'name-hint', role: 'status', hidden: true }, L.custom.name_like ? L.custom.name_like.hint : '');
    const field = h('div', { class: 'field', style: 'padding-top:4px' },
      h('label', { for: 'lname', class: 'label' }, L.custom.input_label), input,
      h('p', { id: 'lname-help', class: 'cap' }, L.custom.help), err, nameHint);
    const preview = h('p', { style: 'font-size:14px;color:var(--ink-secondary);text-align:center;word-break:keep-all' });
    const arrow = arrowFooter(L.next_aria, async () => {
      if (S.step1.done) return go('intro');
      const body = { leader_type: st.type, custom_name: st.type === 'custom' ? st.custom : null, option_order: st.order };
      let r;
      if (!S.participantId) {
        r = await api('POST', '/api/participants', body);
        if (r.ok) { S.participantId = r.data.participant_id; Store.set('participant_id', S.participantId); }
      } else {
        r = await api('PUT', '/api/participants/me', body);
      }
      if (!r.ok) {
        if (r.status === 422 && r.data.message) { err.textContent = r.data.message; err.hidden = false; input.setAttribute('aria-invalid', 'true'); }
        else saveFailed();
        return;
      }
      // 직접 쓴 리더명 내용은 남기지 않는다
      Events.track('leader_name_pick', { leader_type: st.type, option_order: st.order, changes: Math.max(0, (st.picks || 1) - 1) });
      S.me = { leader_name: r.data.leader_name, leader_type: r.data.leader_type, option_order: r.data.option_order, locked: false };
      go('intro');
    });
    input.addEventListener('input', () => { st.custom = input.value; update(); });
    function update() {
      for (const [type, b] of Object.entries(rows)) b.setAttribute('aria-checked', String(st.type === type));
      field.hidden = st.type !== 'custom';
      const trimmed = st.custom.replace(/\s+/g, ' ').trim();
      const blocked = st.type === 'custom' && blockedPattern(trimmed);
      const len = charLen(trimmed);
      const ready = st.type === 'custom' ? (len >= L.custom.min && len <= L.custom.max && !blocked) : !!st.type;
      nameHint.hidden = !(st.type === 'custom' && !blocked && looksLikeName(trimmed, L.custom.name_like));
      err.hidden = !blocked; err.textContent = blocked ? L.custom.blocked_message : '';
      if (blocked) input.setAttribute('aria-invalid', 'true'); else input.removeAttribute('aria-invalid');
      const name = st.type === 'custom' ? callForm(trimmed) : (labels[st.type] || '');
      preview.replaceChildren(ready ? rich(L.preview, null, { leader: name }) : h('span', { style: 'color:var(--ink-tertiary)' }, L.not_ready_hint));
      arrow.setEnabled(ready, L.next_disabled_aria);
    }
    const nodes = [
      header(UI.brand, { backAria: L.back_aria }),
      h('main', { class: 'main', style: 'padding:16px 20px 8px;gap:20px' },
        h('div', { class: 'head-block', style: 'gap:10px' }, h('h1', { class: 'h1' }, rich(L.title)), h('p', { class: 'lead' }, rich(L.body))),
        h('div', { role: 'radiogroup', 'aria-label': L.custom.input_label, style: 'display:flex;flex-direction:column;gap:8px' },
          st.order.map((t) => radio(t, labels[t])), radio('custom', L.custom.label), field),
        h('div', { style: 'margin-top:auto;min-height:22px;display:flex;justify-content:center' }, preview)),
      arrow.el,
    ];
    update();
    return nodes;
  };

  // 3 퀵 리뷰 시작 -----------------------------------------------------------
  RENDER.intro = () => {
    const I = C.intro;
    return [
      header(I.step, { back: !(S.me && S.me.locked) }),
      h('main', { class: 'main center' },
        h('p', { class: 'lead' }, rich(I.greeting)),
        h('h1', { class: 'h1 lg' }, rich(I.title)),
        h('p', { class: 'lead' }, rich(I.body)),
        h('p', { style: 'font-size:13px;font-weight:700' }, I.note)),
      arrowFooter(UI.intro_next_aria, async () => {
        if (await saveStep1({ last_screen: 'review_1' })) go('review_1');
      }).el,
    ];
  };

  // 4~8 핵심 메시지 1~5 -----------------------------------------------------
  const renderReview = (n) => () => {
    const r = C.reviews[n - 1];
    const next = n < 5 ? `review_${n + 1}` : 'keyword';
    const prev = S.step1.reviews[r.id];
    let sending = false;   // 두 버튼을 거의 동시에 누르면 저장이 두 번(서로 다른 값으로) 가지 않게
    const buttons = C.recall_buttons.order.map((val) => {
      const b = h('button', { type: 'button', class: 'btn-outline', 'aria-pressed': String(prev === val) },
        h('span', { class: 'emo', 'aria-hidden': 'true' }, r.emoji[val]), C.recall_buttons[val]);
      b.addEventListener('click', () => {
        if (sending) return;
        sending = true;
        busy(b, async () => {
          if (!(await saveStep1({ reviews: { [r.id]: val }, last_screen: next }))) return;
          Events.track('review_recall', { review: r.id, value: val });
          go(next);
        }).finally(() => { sending = false; });
      });
      return b;
    });
    return [
      header(C.intro.step, { side: fmt(UI.progress_step1, { n }) }),
      progress(n, 6, UI.progress_aria_step1),
      h('main', { class: 'main pad' },
        h('div', { class: 'head-center' },
          h('p', { class: 'over' }, fmt(UI.review_overline, { n })),
          h('h1', { class: 'h1 md' }, rich(r.message)),
          h('p', { class: 'sub' }, rich(r.sub))),
        (r.sections || []).map(section)),
      h('div', { class: 'ftr' },
        h('div', { class: 'grid2', role: 'group', 'aria-label': UI.recall_group_aria }, buttons),
        h('p', { class: 'hint' }, C.recall_buttons.hint)),
    ];
  };
  for (let i = 1; i <= 5; i++) RENDER[`review_${i}`] = renderReview(i);

  // 9 키워드 ----------------------------------------------------------------
  RENDER.keyword = () => {
    const K = C.keywords;
    const NONE = 'kw_none';
    if (!S.kw) S.kw = (S.step1.keywords || []).slice();
    const locked = S.step1.done;
    // 보기 순서를 리더마다 섞는다(2026-11-27, 처음 고른 키워드 1.11 의 순서 효과). 같은 리더는 다시 열어도 같은 순서
    const ids = K.items.map((k) => k.id);
    const orderKey = `kworder.${S.participantId}`;
    let order = null;
    try { order = JSON.parse(Store.get(orderKey) || 'null'); } catch (e) { order = null; }
    if (!Array.isArray(order) || order.length !== ids.length || !ids.every((i) => order.includes(i))) {
      order = shuffle(ids);
      Store.set(orderKey, JSON.stringify(order));
    }
    const byId = Object.fromEntries(K.items.map((k) => [k.id, k]));
    const all = order.map((id) => ({ id, label: byId[id].label })).concat([{ id: NONE, label: K.none_option }]);
    const count = h('p', { 'aria-live': 'polite', style: 'font-size:13px;font-weight:700' });
    const chips = all.map((k) => {
      const b = h('button', { type: 'button', class: 'chip' }, k.label);
      b.addEventListener('click', () => {
        if (locked) return;
        const picked = S.kw;
        if (picked.includes(k.id)) S.kw = picked.filter((x) => x !== k.id);
        else if (k.id === NONE) S.kw = [NONE];
        else { const rest = picked.filter((x) => x !== NONE); if (rest.length >= 3) return; S.kw = rest.concat([k.id]); }
        update();
      });
      return { k, b };
    });
    const foot = cta(K.cta, async () => {
      if (!locked) {
        if (!(await saveStep1({ keywords: S.kw, last_screen: 'keyword', complete: true }))) return;
        // position: 화면에 보인 자리(1부터, '잘 떠오르지 않아요'는 맨 뒤)
        S.kw.forEach((k, i) => Events.track('keyword_pick', { keyword: k, order: i + 1, position: all.findIndex((x) => x.id === k) + 1 }));
      }
      S.me.locked = true;
      go('check_intro');
    }, { hint: K.blocked_hint });
    function update() {
      const n = S.kw.filter((x) => x !== NONE).length;
      const full = n >= 3;
      count.textContent = fmt(K.count, { n });
      for (const { k, b } of chips) {
        const on = S.kw.includes(k.id);
        b.setAttribute('aria-pressed', String(on));
        if (locked || (!on && full && k.id !== NONE)) b.setAttribute('aria-disabled', 'true'); else b.removeAttribute('aria-disabled');
      }
      foot.setEnabled(S.kw.length > 0);
    }
    update();
    return [
      header(C.intro.step, { side: fmt(UI.progress_step1, { n: 6 }) }),
      progress(6, 6, UI.progress_aria_step1),
      h('main', { class: 'main pad' },
        h('div', { class: 'head-center' },
          h('p', { class: 'over' }, K.overline),
          h('h1', { class: 'h1 md', id: 'kw-title' }, rich(K.title)),
          h('p', { class: 'sub' }, K.sub)),
        h('section', { class: 'sec', 'aria-labelledby': 'kw-title' },
          h('div', { style: 'display:flex;align-items:center;justify-content:space-between' }, h('h2', { class: 'sec-h' }, K.section), count),
          h('div', { class: 'chips' }, chips.map((c) => c.b)))),
      foot.el,
    ];
  };

  // 9-1 점검 시작 안내(2026-11-27 추가) — 퀵 리뷰 시작(intro)과 같은 디자인 ---------------
  RENDER.check_intro = () => {
    const I = C.check_intro;
    return [
      header(I.step),
      h('main', { class: 'main center' },
        h('h1', { class: 'h1 lg' }, rich(I.title)),
        h('p', { style: 'font-size:13px;font-weight:700' }, I.note)),
      arrowFooter(UI.check_intro_next_aria, () => go('member')).el,
    ];
  };

  // 10 구성원 떠올리기 --------------------------------------------------------
  RENDER.member = () => {
    const M = C.member;
    // 끝난 점검에서 뒤로 왔거나(완료 → … → 구성원) 키워드에서 다시 왔으면 새 구성원으로 시작한다
    if (checkLocked()) { S.check = null; S.nick = ''; S.md = null; S.act = null; S.pdf = null; }
    const locked = checkLocked();
    if (!S.md) {
      S.md = S.check
        ? { nick: S.nick, situations: (S.check.situations || []).slice() }
        : { nick: genNick(), situations: [], method: 'auto', regen: 0 };
    }
    const st = S.md;
    const input = h('input', { id: 'nick', class: 'input nick', type: 'text', maxlength: M.nickname_maxlength, value: st.nick, placeholder: M.nickname_placeholder, autocomplete: 'off', disabled: locked });
    input.addEventListener('input', () => { st.nick = input.value; st.method = 'manual'; update(); });
    const regen = h('button', { type: 'button', class: 'regen', 'aria-label': M.regen_aria, disabled: locked }, icon('regen'), M.regen);
    regen.addEventListener('click', () => {
      st.nick = genNick(); input.value = st.nick; st.method = 'regenerated'; st.regen = (st.regen || 0) + 1; update();
    });
    const chips = M.situations.map((s) => {
      const b = h('button', { type: 'button', class: 'chip' }, s.label);
      b.addEventListener('click', () => {
        if (locked) return;
        const on = st.situations.includes(s.id);
        if (s.exclusive) st.situations = on ? [] : [s.id];
        else st.situations = on ? st.situations.filter((x) => x !== s.id) : st.situations.filter((x) => x !== 'none').concat([s.id]);
        update();
      });
      return { s, b };
    });
    const foot = cta(M.cta, async () => {
      if (locked) return go('check_1');
      const nick = st.nick.trim();
      let r;
      if (S.check) r = await api('PATCH', `/api/checks/${S.check.seq}`, { situations: st.situations, last_screen: 'check_1' });
      else {
        r = await api('POST', '/api/checks', { situations: st.situations });
        if (r.status === 409 && r.data.error === 'check_in_progress') {
          // 앞선 요청은 저장됐는데 응답을 받지 못한 경우: 아직 답이 없는 진행 중 점검이면 그것을 이어서 쓴다
          const me = await api('GET', '/api/me');
          const c = me.ok ? me.data.check_in_progress : null;
          if (c && !Object.keys(c.answers || {}).length) r = await api('PATCH', `/api/checks/${c.seq}`, { situations: st.situations, last_screen: 'check_1' });
          else if (c) { location.reload(); return; }   // 다른 창에서 답하던 점검이 있으면 이어 하기로
        }
      }
      if (!r.ok) { saveFailed(); return; }
      const isNew = !S.check;
      S.check = r.data;
      S.nick = nick;
      if (isNew) Events.track('nickname_method', { method: st.method || 'auto', regenerate_count: st.regen || 0 });
      Store.set(nickKey(S.check.seq), nick);
      S.md = null; S.act = null;
      go('check_1');
    }, { hint: M.cta_disabled_hint });
    function update() {
      for (const { s, b } of chips) b.setAttribute('aria-pressed', String(st.situations.includes(s.id)));
      foot.setEnabled(st.nick.trim().length > 0 && st.situations.length > 0);
    }
    update();
    return [
      header(M.step),
      h('main', { class: 'main pad', style: 'padding-bottom:16px' },
        h('div', { class: 'head-block', style: 'gap:12px' }, h('h1', { class: 'h1' }, rich(M.title)), h('p', { class: 'lead' }, rich(M.body))),
        h('div', { class: 'field', style: 'gap:8px' },
          h('label', { for: 'nick', class: 'label' }, M.nickname_label),
          h('div', { style: 'display:flex;gap:8px' }, input, regen),
          h('p', { class: 'cap' }, M.nickname_help)),
        h('div', { style: 'display:flex;flex-direction:column;gap:12px' },
          h('div', { style: 'display:flex;flex-direction:column;gap:4px' },
            h('h2', { style: 'font-size:14px;font-weight:700' }, M.situation_title),
            h('p', { class: 'cap' }, M.situation_help)),
          h('div', { class: 'chips' }, chips.map((c) => c.b)))),
      foot.el,
    ];
  };

  // 11~15 체크 1~5 ----------------------------------------------------------
  const renderCheck = (n) => () => {
    const page = C.checklist_pages[n - 1];
    const locked = checkLocked();
    const answers = S.check.answers || (S.check.answers = {});
    const codes = { behavior: ['done', 'not_done'], signal: ['yes', 'no'] };
    const last = n === 5;
    const foot = cta(last ? UI.check_last_cta : UI.next, async () => {
      const nextScreen = last ? 'result_1' : `check_${n + 1}`;
      const pageAnswers = Object.fromEntries(page.items.map((id) => [id, answers[id]]));
      if (!(await saveCheck({ answers: pageAnswers, last_screen: nextScreen }))) return;
      if (last && !locked) S.check.results = null;   // 답이 바뀌었을 수 있으니 결과를 다시 정리
      go(last && !locked ? 'analyzing' : nextScreen);
    }, { hint: C.answer_options.blocked_hint });
    const cards = page.items.map((id) => {
      const it = ITEMS[id];
      const labels = C.answer_options[it.type];
      const btns = codes[it.type].map((code, i) => {
        const b = h('button', { type: 'button', class: 'ans', disabled: locked }, labels[i]);
        b.addEventListener('click', () => {
          const before = answers[id];
          if (before === code) return;
          if (before !== undefined) {
            if (S.check.results && S.check.results.r1) Events.track('answer_changed_after_result', { item: id, before, after: code });
            else Events.track('answer_changed', { item: id });
          }
          answers[id] = code; update();
        });
        return { code, b };
      });
      return { id, btns, el: h('div', { class: 'qcard' + (last ? ' tight' : '') }, h('p', null, it.text), h('div', { class: 'grid2', role: 'group', 'aria-label': it.text, style: 'gap:6px' }, btns.map((x) => x.b))) };
    });
    function update() {
      for (const c of cards) for (const { code, b } of c.btns) b.setAttribute('aria-pressed', String(answers[c.id] === code));
      foot.setEnabled(page.items.every((id) => !!answers[id]));
    }
    update();
    return [
      header(C.member.step, { side: fmt(UI.progress_check, { n }) }),
      progress(n, 5, UI.progress_aria_check),
      h('main', { class: 'main pad', style: last ? 'padding-top:20px;gap:16px' : 'padding-top:28px;gap:32px' },
        h('div', { class: 'head-block' },
          h('p', { class: 'over' }, page.topic),
          h('h1', { class: 'h1 md' }, rich(page.title)),
          page.sub ? h('p', { class: 'lead' }, rich(page.sub)) : null),
        h('div', { style: `display:flex;flex-direction:column;gap:${last ? 8 : 12}px` }, cards.map((c) => c.el))),
      foot.el,
    ];
  };
  for (let i = 1; i <= 5; i++) RENDER[`check_${i}`] = renderCheck(i);

  // 16 결과 정리 (검정, 5→1 카운트다운 3초) ------------------------------------
  RENDER.analyzing = (o, myToken) => {
    const A = C.analyzing;
    const cd = A.countdown;
    const nums = [];
    for (let v = cd.from, i = 0; v >= cd.to; v--, i++) nums.push(h('span', { class: 'count', style: `animation-delay:${(i * cd.step_seconds).toFixed(2)}s` }, v));
    const mark = h('span', { class: 'check-mark', style: `animation-delay:${cd.total_seconds}s` }, icon('check'));
    const status = h('div', { style: 'display:flex;flex-direction:column;align-items:center;gap:12px' });
    const run = async () => {
      const wait = new Promise((res) => setTimeout(res, cd.total_seconds * 1000 + 350));
      const [r] = await Promise.all([api('GET', `/api/checks/${S.check.seq}/result`), wait]);
      if (myToken !== token) return;   // 그 사이 다른 화면으로 이동함
      if (!r.ok) {
        status.replaceChildren(h('p', { class: 'lead' }, UI.result_error),
          h('button', { type: 'button', class: 'btn-outline', style: 'padding:0 20px;background:transparent;color:#fff;border-color:#fff', onclick: () => show('analyzing') }, UI.retry));
        return;
      }
      S.check.results = r.data.results;
      S.check.quadrant = r.data.quadrant;
      go('result_1', { fromBlack: true });
    };
    run();
    return [
      header(C.member.step, { center: true }),
      h('main', { class: 'main center', 'aria-live': 'polite' },
        h('div', { class: 'count-wrap', 'aria-hidden': 'true' }, nums, mark),
        h('h1', { class: 'h1 lg', style: 'color:#fff' }, rich(A.title)),
        h('p', { class: 'lead' }, rich(A.body)),
        status),
    ];
  };

  // 17 결과 1 경향과 기억 -----------------------------------------------------
  RENDER.result_1 = (o) => {
    const R = C.results.r1;
    const res = S.check.results;
    const q = S.check.quadrant;
    const sit = topSituationLabel(S.check.situations);
    const behaviorLabels = C.answer_options.behavior;
    const recall = res.r1.none
      ? h('p', { class: 'box', style: 'font-size:14px' }, R.recall_none)
      : h('ul', { style: 'list-style:none;padding:0;display:flex;flex-direction:column;gap:6px' }, res.r1.keywords.map((k) => {
        const kw = C.keywords.items.find((x) => x.id === k.id);
        return h('li', { class: 'recall-li' }, h('span', { class: 'k' }, kw ? kw.label : k.id),
          h('span', { class: 'badge' }, fmt(R.recall_status, { answer: k.did ? behaviorLabels[0] : behaviorLabels[1] })));
      }));
    const foot = cta(UI.next, async () => { if (await saveCheck({ last_screen: 'result_2' })) go('result_2'); });
    return [
      header(C.member.step, { side: fmt(UI.progress_result, { n: 1 }) }),
      progress(1, 3, UI.progress_aria_result),
      h('main', { class: 'main pad', style: 'padding-top:16px;gap:16px' },
        h('div', { class: 'head-block' },
          h('p', { class: 'over' }, sit ? `${R.overline} · ${sit}` : R.overline),
          h('h1', { class: 'h1 sm' }, rich(R.title, { quadrant_label: C.quadrants[q].label }))),
        h('section', { class: 'sec' },
          h('p', { class: 'lead', style: 'font-size:14px' }, C.quadrants[q].desc),
          quadrant(q),
          // 행동은 했는데 체크 5의 순간 때문에 '낮음'이 된 축은 이유를 한 줄로(2026-11-27)
          (res.r1.signal_low || []).map((x) => h('p', { class: 'box', style: 'font-size:13px;line-height:1.6;word-break:keep-all' },
            fmt(R.signal_low, { axis: R.axis_names[x.axis], done: x.done, total: x.total, signals: x.signals }))),
          h('p', { class: 'cap' }, R.note)),
        h('section', { class: 'sec', style: 'gap:12px' },
          h('h2', { style: 'font-size:14px;font-weight:700;color:var(--ink-tertiary)' }, R.recall_section),
          h('p', { class: 'cap' }, rich(R.recall_note)),
          recall)),
      foot.el,
      o.fromBlack && !reduceMotion() ? h('div', { class: 'from-black', 'aria-hidden': 'true' }) : null,
    ];
  };

  // 18 결과 2 잘한 점 (👍 애니메이션) ------------------------------------------
  RENDER.result_2 = (o) => {
    const R = C.results.r2;
    const r2 = S.check.results.r2;
    const rows = [];
    const lowAxis = new Set(r2.low_axis || []);
    r2.items.forEach((id, i) => {
      const it = ITEMS[id];
      if (i) rows.push(h('hr'));
      // 낮음 축에서 온 '했어요'는 결과 1 과 어긋나 보이지 않게 표시(2026-11-27)
      const cap = lowAxis.has(id) ? `${fmt(R.low_axis_cap, { axis: C.results.r1.axis_names[it.axis] })} · ${it.tool}` : UI.tool_prefix + it.tool;
      rows.push(h('div', { class: 'row' }, h('span', { class: 't' }, it.text), h('span', { class: 'cap' }, cap), h('span', { class: 'e' }, it.effect)));
    });
    if (!r2.empty && !o.back && !S.played.thumbs.has(S.check.seq) && !checkLocked()) {
      S.played.thumbs.add(S.check.seq);
      setTimeout(thumbsUp, 0);
    }
    const foot = cta(UI.next, async () => { if (await saveCheck({ last_screen: 'result_3' })) go('result_3'); });
    return [
      header(C.member.step, { side: fmt(UI.progress_result, { n: 2 }) }),
      progress(2, 3, UI.progress_aria_result),
      h('main', { class: 'main pad', style: 'padding-top:28px;gap:28px' },
        h('div', { class: 'head-block' },
          h('p', { class: 'over' }, R.overline),
          h('h1', { class: 'h1 md' }, rich(R.title)),
          r2.empty ? null : h('p', { style: 'font-size:18px;line-height:1.5;font-weight:700;word-break:keep-all' }, R.summary[r2.summary])),
        r2.empty
          ? h('p', { class: 'lead' }, rich(R.empty))
          : (rows.length ? h('section', { class: 'sec', style: 'gap:12px' }, h('div', { class: 'strength' }, rows)) : null)),
      foot.el,
    ];
  };

  // 19 결과 3 다음에 해 볼 것 -------------------------------------------------
  RENDER.result_3 = () => {
    const R = C.results.r3;
    const res = S.check.results;
    // 늘 3개(SPEC 4.6): 못 한 행동 → 체크 5에서 고른 순간 → 이어 가기. items 가 없는 예전 결과는 item 1개로 보여 준다
    const ids = res.r3.items || (res.r3.item ? [res.r3.item] : []);
    const keep = new Set(res.r3.keep || []);
    const rows = [];
    ids.forEach((id, i) => {
      const it = ITEMS[id];
      if (i) rows.push(h('hr'));
      const [cap, body] = keep.has(id) ? [R.keep_cap, it.effect]
        : it.type === 'signal' ? [R.signal_cap, it.advice] : [UI.tool_prefix + it.tool, it.advice];
      rows.push(h('div', { class: 'row' }, h('span', { class: 't' }, rich(it.next_label)), h('span', { class: 'cap' }, cap), h('span', { class: 'e' }, body)));
    });
    // 못 한 행동이 없으면 한 줄 안내를 먼저(부족 신호가 있으면 empty_signals)
    const lead = res.r3.empty_signals ? R.empty_signals : (res.r3.empty ? R.empty : null);
    const foot = cta(R.cta, async () => { if (await saveCheck({ last_screen: 'action' })) go('action'); });
    return [
      header(C.member.step, { side: fmt(UI.progress_result, { n: 3 }) }),
      progress(3, 3, UI.progress_aria_result),
      h('main', { class: 'main pad', style: 'padding-top:28px;gap:20px' },
        h('div', { class: 'head-block' }, h('p', { class: 'over' }, R.overline), h('h1', { class: 'h1 md' }, rich(R.title))),
        h('section', { class: 'sec', style: 'gap:12px' },
          lead ? h('p', { class: 'lead' }, rich(lead)) : null,
          rows.length ? h('div', { class: 'strength' }, rows) : null,
          res.phrase ? h('div', { class: 'phrase-card' }, h('span', { class: 'l' }, R.phrase_label), h('span', { class: 'p' }, phraseText(res.phrase))) : null)),
      foot.el,
    ];
  };

  // 20 실천 1개 --------------------------------------------------------------
  RENDER.action = () => {
    const A = C.action;
    const locked = checkLocked();
    const opts = S.check.results.action_options || [];
    if (!S.act || S.act.seq !== S.check.seq) {
      const c = S.check;
      S.act = {
        seq: c.seq,
        sel: c.action_type === 'custom' ? 'custom' : (c.action_key || (opts[0] ? opts[0].key : 'custom')),
        draft: Store.get(actKey(c.seq)) || '',
      };
    }
    const st = S.act;
    // 결과를 다시 정리해 보기가 바뀌었으면(결과를 본 뒤 답을 바꿈) 예전에 고른 보기 대신 첫 보기를 고른다
    if (st.sel !== 'custom' && !opts.some((o) => o.key === st.sel)) st.sel = opts[0] ? opts[0].key : 'custom';
    const subOf = (o) => {
      if (o.type === 'recommended') return A.recommended_label;
      const [kind, id] = o.key.split(':');
      if (kind === 'situation') return C.situation_labels_for_action[id];
      // 상황을 많이 골라 결과 3 의 말·공통 문장으로 채운 보기(2026-11-27)
      if (kind === 'item' && ITEMS[id]) return fmt(A.item_option_label, { label: ITEMS[id].next_label.replace(/\n/g, ' ') });
      return A.general_option_label;
    };
    const choose = (key) => {
      if (locked || key === st.sel) return;
      if (st.sel === 'custom') Events.track('action_custom_abandoned');
      st.sel = key; show('action', { keep: true }); if (key === 'custom') { const t = document.getElementById('act-custom'); if (t) t.focus(); } };
    const cards = opts.map((o) => h('button', { type: 'button', role: 'radio', class: 'act', 'aria-checked': String(st.sel === o.key), onclick: () => choose(o.key) },
      h('span', { class: 'dot', 'aria-hidden': 'true' }),
      h('span', { style: 'display:flex;flex-direction:column;gap:6px' }, h('span', { class: 'sub' }, subOf(o)), h('span', { class: 'txt' }, phraseText(o.key)))));
    const isCustom = st.sel === 'custom';
    const counter = h('span', { style: 'flex-shrink:0' }, `${st.draft.length}/${A.custom_maxlength}`);
    let textarea = null;
    if (isCustom) {
      textarea = h('textarea', { id: 'act-custom', class: 'textarea', maxlength: A.custom_maxlength, rows: 3, placeholder: A.custom_placeholder.replace('[별명]', () => S.nick || '○○'), disabled: locked });
      textarea.value = st.draft;
      textarea.addEventListener('input', () => { st.draft = textarea.value; counter.textContent = `${st.draft.length}/${A.custom_maxlength}`; foot.setEnabled(!!st.draft.trim()); });
    }
    const customBox = h('div', { class: 'act-custom' + (isCustom ? ' on' : '') },
      h('button', { type: 'button', role: 'radio', class: 'head', 'aria-checked': String(isCustom), onclick: () => choose('custom') },
        h('span', { class: 'dot' + (isCustom ? ' on' : ''), 'aria-hidden': 'true', style: isCustom ? '' : 'border:2px solid var(--border)' }),
        h('span', { class: 'lbl' }, icon('plus'), A.custom)),
      isCustom ? [h('label', { for: 'act-custom', class: 'sr-only' }, A.custom_aria), textarea,
        h('div', { class: 'meta' }, h('span', { class: 'keep' }, A.custom_note), counter)] : null);
    const foot = cta(A.cta, async () => {
      if (locked) return go('done');
      const opt = opts.find((x) => x.key === st.sel);
      if (st.sel !== 'custom' && !opt) return show('action', { keep: true });
      const body = st.sel === 'custom'
        ? { action_type: 'custom', action_text: maskNick(st.draft.trim()), complete: true }
        : { action_type: opt.type, action_key: opt.key, complete: true };
      if (!(await saveCheck(body))) return;
      Events.track('action_pick', { kind: st.sel === 'custom' ? 'custom' : opt.type });
      if (st.sel === 'custom') {
        Store.set(actKey(S.check.seq), st.draft.trim());
        Events.track('action_custom_text', { text: body.action_text });   // ○○로 바꾼 문장(서버에서 한 번 더 지움)
      }
      go('done');
    }, { hint: A.blocked_hint, enabled: !isCustom || !!st.draft.trim() });
    return [
      header(C.member.step, { backAria: UI.back }),
      h('main', { class: 'main pad', style: 'padding-top:28px' },
        h('div', { class: 'head-block', style: 'gap:12px' }, h('h1', { class: 'h1 md' }, rich(A.title)), h('p', { class: 'lead' }, rich(A.body))),
        h('div', { role: 'radiogroup', 'aria-label': A.group_aria, style: 'display:flex;flex-direction:column;gap:12px' }, cards, customBox)),
      foot.el,
    ];
  };

  // 21 완료 (꽃가루) ----------------------------------------------------------
  RENDER.done = (o) => {
    const D = C.done;
    const [first, ...rest] = D.title.split('\n');
    const phrase = donePhrase();
    const btn = (a, iconName, onClick) => h('button', { type: 'button', class: 'done-btn', 'aria-label': a.aria.replace('[별명]', () => S.nick || '○○'), onclick: onClick },
      icon(iconName), h('span', null, a.label));
    const acts = Object.fromEntries(D.actions.map((a) => [a.id, a]));
    if (!o.back && !S.played.confetti.has(S.check.seq)) { S.played.confetti.add(S.check.seq); setTimeout(confetti, 0); }
    prebuildPdf();
    return [
      header('', {}),
      h('main', { class: 'main center' },
        h('p', { class: 'over' }, D.eyebrow),
        h('h1', { class: 'h1 lg' },
          h('span', { style: 'display:block;margin-bottom:6px;font-size:22px;line-height:1.4;letter-spacing:-0.02em' }, rich(first)),
          rich(rest.join('\n'))),
        h('p', { class: 'lead' }, rich(D.phrase_label)),
        h('p', { class: 'done-card' }, phrase),
        h('p', { style: 'margin-top:4px;font-size:13px;line-height:1.5;font-weight:700;word-break:keep-all' }, rich(D.keep_note),
          // 앱 안 브라우저는 PDF 저장이 조용히 실패하기도 한다 → 캡처로 남기도록(2026-11-27)
          inApp() ? [h('br'), D.inapp_note] : null),
        h('div', { class: 'done-actions', role: 'group', 'aria-label': D.group_aria },
          btn(acts.again, 'again', () => {
            Events.track('done_button_click', { button: 'again' });
            Events.track('another_member_started');
            S.check = null; S.nick = ''; S.md = null; S.act = null; S.pdf = null;
            go('member');
          }),
          btn(acts.pdf, 'download', (e) => busy(e.currentTarget, downloadResult)),
          btn(acts.recommend, 'link', (e) => busy(e.currentTarget, recommend)))),
    ];
  };

  // ----------------------------------------------------------------------- //
  // 완료 화면 3칸 (SPEC 7장)
  // ----------------------------------------------------------------------- //
  function donePhrase() {
    if (!S.check) return '';
    return S.check.action_type === 'custom'
      ? (Store.get(actKey(S.check.seq)) || S.check.action_text || '')
      : phraseText(S.check.action_key);
  }
  /** 누르자마자 받을 수 있게 완료 화면에 들어오면 PDF 를 미리 만들어 둔다 */
  function prebuildPdf() {
    if (!window.ResultPdf || !S.check || (S.pdf && S.pdf.seq === S.check.seq)) return;
    const data = {
      content: C, nick: S.nick, leader: S.me.leader_name, quadrant: S.check.quadrant, results: S.check.results,
      phrase: donePhrase(), situationLabel: topSituationLabel(S.check.situations),
      cycleName: S.settings.cycle_name, date: dateStamp('.'), version: S.version, credit: S.credit,
    };
    const promise = new Promise((res) => setTimeout(res, 300)).then(() => window.ResultPdf.build(data));
    promise.catch(() => {});
    S.pdf = { seq: S.check.seq, promise };
  }
  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const a = h('a', { href: url, download: filename, style: 'display:none' });
    document.body.append(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 30000);
  }
  /** PDF 다운받기(2026-11-26 결정: 공유 창 대신 늘 파일로 내려받기). 누를 때마다 클릭으로 센다 */
  async function downloadResult() {
    const D = C.done;
    Events.track('done_button_click', { button: 'pdf' });
    prebuildPdf();
    let blob;
    try { blob = await S.pdf.promise; } catch (e) { Events.track('error', { kind: 'pdf' }); toast(D.pdf_error, 3000); return; }
    downloadBlob(blob, fmt(D.pdf.filename, { date: dateStamp() }));
    Events.track('result_pdf_saved');
  }
  async function copyText(t) {
    try { if (navigator.clipboard && window.isSecureContext) { await navigator.clipboard.writeText(t); return true; } } catch (e) { /* 아래 방법으로 */ }
    try {
      const ta = h('textarea', { readonly: true, style: 'position:fixed;top:-1000px;left:0;opacity:0' });
      ta.value = t; document.body.append(ta); ta.select(); ta.setSelectionRange(0, t.length);
      const ok = document.execCommand('copy');
      ta.remove();
      return ok;
    } catch (e) { return false; }
  }
  /** 동료에게 추천하기(2026-11-26 결정): 공유 창 없이 첫 화면(안내) 링크만 복사한다. PC·휴대폰 모두 같은 동작.
   *  링크 = 운영 주소 + 입장 키 + ref=recommend. 리더명·별명·결과는 담지 않는다.
   *  클립보드 쓰기는 누른 직후(첫 await 전)에 불려야 iOS 에서도 된다 → 앞에 await 를 두지 않는다 */
  async function recommend() {
    const D = C.done;
    Events.track('done_button_click', { button: 'recommend' });
    // 입장 키 없이 들어오게 열어 두었으면 기본 주소만(2026-11-27). 아니면 이 브라우저의 입장 키를 붙인다
    const key = !S.settings.open_entry && S.entryKey ? `k=${encodeURIComponent(S.entryKey)}&` : '';
    const url = `${location.origin}/?${key}ref=recommend`;
    if (await copyText(url)) { toast(D.recommend_copied, 2500); Events.track('recommend_copied'); }
    else window.prompt(D.copy_error, url);   // 복사가 막힌 브라우저: 링크를 보여 주고 직접 복사
  }

  // ----------------------------------------------------------------------- //
  // 효과: 결과 2 엄지척, 완료 꽃가루 (한 번만, 터치 막지 않음, 동작 줄이기면 없음)
  // ----------------------------------------------------------------------- //
  function fxLayer(kids, ms) {
    if (reduceMotion()) return;
    const layer = h('div', { class: 'fx', 'aria-hidden': 'true' }, kids);
    document.body.append(layer);
    setTimeout(() => layer.remove(), ms);
  }
  function thumbsUp() {
    const sizes = [18, 22, 26, 30, 34, 38, 44];
    const kids = [];
    for (let i = 0; i < 14; i++) {
      const dur = 1.8 + Math.random() * 0.7;
      const delay = Math.random() * 0.65;
      kids.push(h('span', { class: 'emoji', style: `left:${(5 + Math.random() * 82).toFixed(1)}%;font-size:${sizes[randInt(sizes.length)]}px;animation:emoji-rise-${i % 2 ? 'b' : 'a'} ${dur.toFixed(2)}s cubic-bezier(.25,.6,.4,1) ${delay.toFixed(2)}s 1 forwards` }, '👍'));
    }
    fxLayer(kids, 3500);
  }
  function confetti() {
    const colors = ['#FF5A5F', '#FFC93C', '#00A651', '#2EC4B6', '#3D8BFD', '#9B5DE5', '#FF6FB5', '#FF8C42', '#00703C', '#000000'];
    const kids = [];
    for (let i = 0; i < 42; i++) {
      const round = i % 3 === 0;
      const dur = 1.6 + Math.random() * 1.0;
      const delay = Math.random() * 0.5;
      kids.push(h('span', { class: 'conf', style: `left:${(Math.random() * 98).toFixed(1)}%;width:${round ? 8 : 9}px;height:${round ? 8 : 5}px;border-radius:${round ? '50%' : '2px'};background:${colors[i % colors.length]};animation:confetti-fall-${i % 2 ? 'b' : 'a'} ${dur.toFixed(2)}s cubic-bezier(.3,.5,.6,1) ${delay.toFixed(2)}s 1 forwards` }));
    }
    fxLayer(kids, 3500);
  }

  // ----------------------------------------------------------------------- //
  // 시작: 입장 키 확인 → 문구 → 이어 하기
  // ----------------------------------------------------------------------- //
  function readEntryParams() {
    const url = new URL(location.href);
    const k = url.searchParams.get('k');
    if (k) Store.set('entry_key', k);
    if (url.searchParams.get('ref') === 'recommend') { Store.set('ref', 'recommend'); S.fromRecommend = true; }
    if (k || url.searchParams.has('ref')) {
      url.searchParams.delete('k'); url.searchParams.delete('ref');
      history.replaceState(null, '', url.pathname + url.search + url.hash);
    }
    S.entryKey = Store.get('entry_key');
    S.deviceId = Store.get('device_id') || (() => { const id = uuid4(); Store.set('device_id', id); return id; })();
    S.participantId = Store.get('participant_id');
  }

  async function resume() {
    if (!S.participantId) return show('notice', { initial: true });
    const r = await api('GET', '/api/me');
    if (r.status === 404) { Store.del('participant_id'); S.participantId = null; return show('notice', { initial: true }); }
    if (!r.ok) return messageOnly(r.data.message || UI.load_error);
    const me = r.data;
    S.me = { leader_name: me.leader_name, leader_type: me.leader_type, option_order: me.option_order, locked: me.leader_name_locked };
    S.step1 = me.step1 ? { reviews: me.step1.reviews || {}, keywords: me.step1.keywords || [], done: me.step1.done } : { reviews: {}, keywords: [], done: false };
    if (!S.step1.done) {
      const s = (me.step1 && me.step1.last_screen) || 'intro';
      Events.track('resume', { screen: s }, null);
      // 이어 하기 안내는 늘 보여 준다: 같은 기기를 쓰는 다른 리더가 남의 기록으로 이어 가지 않게(2026-11-27)
      return show(s, { initial: true, banner: { restart: false } });
    }
    const c = me.check_in_progress;
    if (!c) {
      Events.track(me.checks_done > 0 ? 'revisit_after_done' : 'resume', me.checks_done > 0 ? {} : { screen: 'member' }, null);
      return show('member', { initial: true, banner: { restart: false } });
    }
    S.check = c;
    S.nick = Store.get(nickKey(c.seq)) || '';
    let s = c.last_screen || 'member';
    if (s === 'analyzing') s = 'result_1';
    if (/^(result_\d|action)$/.test(s) && !(c.results && c.results.r1)) s = 'analyzing';
    Events.track('resume', { screen: s });
    return show(s, { initial: true, banner: { restart: true } });
  }

  async function boot() {
    readEntryParams();
    const r = await api('GET', '/api/content');
    S.version = r.data.version || '';
    S.credit = r.data.credit || '';
    if (r.status === 0) return messageOnly('연결하지 못했어요. 잠시 후 다시 열어 주세요.'); // 문구를 받기 전이라 예외적으로 코드에 둠
    // 입장 키가 틀린 경우만 저장된 키를 지운다(사내 프록시 등이 막은 403 은 본문이 달라 '연결하지 못했어요'로)
    if (r.status === 403 && r.data.error === 'entry_denied') { Store.del('entry_key'); return messageOnly(r.data.message); }
    if (r.data.closed) return messageOnly(r.data.message);
    if (!r.ok) return messageOnly('연결하지 못했어요. 잠시 후 다시 열어 주세요.');
    C = r.data.content;
    UI = C.ui;
    ITEMS = Object.fromEntries(C.items.map((i) => [i.id, i]));
    S.settings = r.data.settings || {};
    initHistory();
    if (!Store.available) { toast(UI.storage_unavailable, 5000); Events.track('error', { kind: 'storage_unavailable' }, null); }
    // 앱 안 브라우저로 연 세션(관리자 4.5 기기별의 '앱 안 브라우저' 기기 수). 브라우저 종류만, UA 원문은 보내지 않는다
    if (inApp()) Events.track('inapp_browser', { browser: browserFamily() }, null);
    // 추천 링크로 들어온 첫 방문(기기 ID). 이미 참여한 리더가 자기 링크를 열어 본 것은 세지 않는다
    if (Store.get('ref') === 'recommend' && !Store.get('ref_logged') && !S.participantId) {
      Events.track('visit_from_recommend', {}, null);
      Store.set('ref_logged', '1');
    }
    Store.del('ref');
    // 추천 링크는 늘 첫 화면(안내)부터. 이 브라우저에 이미 리더 기록이 있으면 안내의 화살표에서 이어 하기로 간다
    if (S.fromRecommend && S.participantId) return show('notice', { initial: true });
    await resume();
  }

  window.LR = { S, Store, api, show, Events };   // 개발 중 콘솔 확인용
  boot();
})();
