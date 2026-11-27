/* 관리자 화면 — 로그인, 대시보드·문항 분석·참여자·사용 흐름, 설정, CSV (SPEC 8장)
   데이터는 로그인 쿠키로 /api/admin/* 에서만 받는다. 문구는 /api/admin/content (admin.json + content.json) */
'use strict';
(() => {
  const $root = document.getElementById('root');
  const $tip = document.getElementById('tip');
  const $toast = document.getElementById('toast');
  let A = null;      // admin.json
  let C = null;      // content.json
  let ME = null;     // {role, cycle_id, cycle_name, cycles, target_count}
  const F = { cycle: null, type: '', from: '', to: '' };
  const L = { stage: '', q: '', sort: 'last_access', desc: true, page: 1, size: 20 };
  let tab = 'dashboard';
  let renderToken = 0;

  // 비밀 관리자 링크(2026-11-27): /admin?a=키 로 열면 이 브라우저에 키를 두고 주소창에서는 지운다.
  // 로그인 문구·로그인 요청에 X-Admin-Link 로 함께 보낸다(키를 정해 두지 않았으면 서버가 확인하지 않음)
  const LINK_STORE = 'lr4.admin_link';
  const store = {
    get(k) { try { return localStorage.getItem(k); } catch (e) { return null; } },
    set(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* 무시 */ } },
    del(k) { try { localStorage.removeItem(k); } catch (e) { /* 무시 */ } },
  };
  (() => {
    const u = new URL(location.href);
    const a = u.searchParams.get('a');
    if (a) { store.set(LINK_STORE, a); u.searchParams.delete('a'); history.replaceState(null, '', u.pathname + u.search + u.hash); }
  })();
  const linkHeader = () => { const k = store.get(LINK_STORE); return k ? { 'X-Admin-Link': k } : {}; };

  // ----------------------------------------------------------------------- //
  // 도우미
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
    for (const c of kids.flat(Infinity)) {
      if (c === null || c === undefined || c === false) continue;
      el.append(c instanceof Node ? c : document.createTextNode(String(c)));
    }
    return el;
  }
  const fmt = (s, v) => String(s).replace(/\{(\w+)\}/g, (m, k) => (v && k in v ? String(v[k]) : m));
  const U = () => A.app.units;
  const D = () => A.app.dash;
  const n = (v, unit) => (v === null || v === undefined ? D() : `${typeof v === 'number' ? (Math.round(v * 10) / 10).toLocaleString('ko-KR') : v}${unit || ''}`);
  const pctT = (v) => n(v, U().pct);
  function toast(text) { $toast.textContent = text; $toast.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { $toast.hidden = true; }, 2500); }

  async function api(method, path, body) {
    const headers = Object.assign({ Accept: 'application/json' }, linkHeader());
    if (method !== 'GET') headers['X-Admin-Request'] = '1';
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    try {
      const res = await fetch(path, { method, headers, credentials: 'same-origin', cache: 'no-store', body: body === undefined ? undefined : JSON.stringify(body) });
      let data = {};
      try { data = await res.json(); } catch (e) { /* 본문 없음 */ }
      if (res.status === 401 && path !== '/api/admin/login') { showLogin(); }
      return { ok: res.ok, status: res.status, data: data || {} };
    } catch (e) {
      return { ok: false, status: 0, data: {} };
    }
  }
  function query(extra) {
    const p = new URLSearchParams();
    if (F.cycle) p.set('cycle', F.cycle);
    if (F.type) p.set('type', F.type);
    if (F.from) p.set('date_from', F.from);
    if (F.to) p.set('date_to', F.to);
    for (const [k, v] of Object.entries(extra || {})) if (v !== '' && v !== null && v !== undefined) p.set(k, v);
    const s = p.toString();
    return s ? `?${s}` : '';
  }

  // 툴팁(data-tip)
  document.addEventListener('mouseover', (e) => {
    const t = e.target.closest && e.target.closest('[data-tip]');
    if (!t) { $tip.hidden = true; return; }
    $tip.textContent = t.getAttribute('data-tip'); $tip.hidden = false;
  });
  document.addEventListener('mousemove', (e) => {
    if ($tip.hidden) return;
    $tip.style.left = `${Math.min(e.clientX + 14, window.innerWidth - 330)}px`;
    $tip.style.top = `${e.clientY + 14}px`;
  });

  // 이름표
  const typeLabel = (t) => (t === 'custom' ? A.filters.custom : (C.leader_name.options.find((o) => o.type === t) || {}).label || t);
  const itemText = (id) => (C.items.find((i) => i.id === id) || {}).text || id;
  const kwLabel = (id) => (C.keywords.items.find((k) => k.id === id) || {}).label || id;
  const sitLabel = (id) => (C.member.situations.find((s) => s.id === id) || {}).label || id;
  const screenLabel = (s) => A.screens[s] || s || D();
  const TYPES = ['rc', 're', 'oa', 'mi', 'custom'];
  const QUADS = ['rc', 're', 'oa', 'mi'];

  // ----------------------------------------------------------------------- //
  // 차트 조각
  // ----------------------------------------------------------------------- //
  function shade(v, max) {
    if (v === null || v === undefined || !max) return { bg: '#f7f7f7', fg: '#676767' };
    const r = v / max;
    const a = r <= 0 ? 0.04 : r < 0.2 ? 0.12 : r < 0.4 ? 0.28 : r < 0.6 ? 0.45 : r < 0.8 ? 0.65 : 0.85;  // 5단계 + 0
    return { bg: `rgba(0,112,60,${a})`, fg: a >= 0.45 ? '#ffffff' : '#000000' };
  }
  /** 가로 막대. rows: {label, value, text, tip, extra} */
  function bars(rows, o) {
    const opt = o || {};
    const max = opt.max || Math.max(1, ...rows.map((r) => r.value || 0));
    return h('div', { class: 'bars', role: 'img', style: `--lw:${opt.lw || '200px'}` }, rows.map((r) =>
      h('div', { class: 'bar' + (opt.extra ? ' wide' : ''), 'data-tip': r.tip || `${r.label}: ${r.text}` },
        h('span', { class: 'lab' }, r.label),
        h('span', { class: 'track' }, r.value ? h('span', { class: 'fill', style: `width:${Math.min(100, (100 * r.value) / max)}%;background:${opt.color || 'var(--c1)'}` }) : null),
        h('span', { class: 'val num' }, r.text),
        opt.extra ? h('span', { class: 'val2 num' }, r.extra || '') : null)));
  }
  /** 두 계열 막대(비교) */
  function bars2(rows, o) {
    const opt = o || {};
    const max = opt.max || Math.max(1, ...rows.flatMap((r) => [r.a || 0, r.b || 0]));
    return h('div', { class: 'bars', role: 'img', style: `--lw:${opt.lw || '200px'}` }, rows.map((r) =>
      h('div', { class: 'bar wide', 'data-tip': r.tip },
        h('span', { class: 'lab' }, r.label),
        h('span', { class: 'track two' },
          h('span', null, r.a ? h('span', { class: 'fill', style: `width:${(100 * r.a) / max}%;background:var(--c1)` }) : null),
          h('span', null, r.b ? h('span', { class: 'fill', style: `width:${(100 * r.b) / max}%;background:var(--cmp)` }) : null)),
        h('span', { class: 'val num' }, r.textA),
        h('span', { class: 'val2 num' }, r.textB))));
  }
  function legend(items) { return h('div', { class: 'legend' }, items.map(([c, t]) => h('span', null, h('i', { style: `background:${c}` }), t))); }
  function table(head, rows, numCols) {
    const nc = new Set(numCols || []);
    return h('table', { class: 't' },
      h('thead', null, h('tr', null, head.map((x, i) => h('th', { class: nc.has(i) ? 'n' : null }, x)))),
      h('tbody', null, rows.length ? rows.map((r) => h('tr', null, r.map((x, i) => h('td', { class: nc.has(i) ? 'n' : null }, x)))) :
        h('tr', null, h('td', { colspan: head.length, class: 'empty' }, A.app.empty))));
  }
  /** 항목 번호(탭 번호.순서 — 1 대시보드, 2 문항 분석, 3 참여자, 4 사용 흐름) */
  const TAB_NO = { dashboard: 1, items: 2, leaders: 3, flow: 4 };
  function numberer(tabNo) { let i = 0; return () => `${tabNo}.${++i}`; }
  const noBadge = (no) => (no ? h('span', { class: 'no-badge' }, no) : null);
  /** 이 데이터로 알 수 있는 것(admin.json insights). 처음에는 펼친 상태, 항목마다 접을 수 있음 */
  function insightEl(text) {
    return text ? h('details', { class: 'insight', open: true }, h('summary', null, A.insights.label), h('p', null, text)) : null;
  }
  /** 카드 + '표로 보기' 전환 */
  function card(o) {
    const body = h('div', null, o.chart());
    let asTable = false;
    const toggle = o.table ? h('button', { type: 'button', class: 'link' }, A.app.table) : null;
    if (toggle) toggle.addEventListener('click', () => { asTable = !asTable; body.replaceChildren(asTable ? o.table() : o.chart()); toggle.textContent = asTable ? A.app.chart : A.app.table; });
    return h('section', { class: 'card', style: o.span ? 'grid-column: 1 / -1' : null },
      h('div', { class: 'card-h' }, h('h3', null, noBadge(o.no), o.title), h('div', { class: 'r' }, o.sub ? h('span', { class: 'small muted' }, o.sub) : null, toggle)),
      o.legend || null, body, o.note ? h('p', { class: 'note' }, o.note) : null, insightEl(o.insight));
  }
  /** 핵심 지표 줄에도 번호·제목·인사이트를 붙인다 */
  function kpiBlock(o, list) {
    return h('div', { class: 'kpi-block' }, h('h3', { class: 'kpi-h' }, noBadge(o.no), o.title), kpis(list),
      o.warn ? h('p', { class: 'warn' }, o.warn) : null, insightEl(o.insight));
  }
  function secHead(s) {
    return h('div', { class: 'sec-head' },
      h('div', { style: 'display:flex;flex-direction:column;gap:6px' }, h('span', { class: 'no' }, s.no), h('h2', null, s.title)),
      h('div', { class: 'r' }, h('span', { class: 'badge' }, s.badge), h('span', { class: 'small muted' }, s.sub)));
  }
  function kpis(list) {
    return h('div', { class: 'kpis', style: `grid-template-columns:repeat(${list.length},minmax(0,1fr))` }, list.map(([label, value, sub]) =>
      h('div', { class: 'kpi' }, h('span', { class: 'l' }, label), h('span', { class: 'v num' }, value), h('span', { class: 'small muted' }, sub))));
  }
  function lineChart(points, series) {
    if (!points.length) return h('p', { class: 'empty' }, A.app.empty);
    const W = 1000; const H = 220; const L0 = 44; const B = 24;
    const max = Math.max(1, ...points.flatMap((p) => series.map((s) => p[s.key])));
    const x = (i) => L0 + (points.length === 1 ? (W - L0) / 2 : (i * (W - L0 - 10)) / (points.length - 1));
    const y = (v) => 8 + (H - B - 8) * (1 - v / max);
    const ns = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`); svg.setAttribute('role', 'img');
    const el = (tag, attrs, text) => { const e = document.createElementNS(ns, tag); for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v); if (text !== undefined) e.textContent = text; svg.append(e); return e; };
    for (let i = 0; i <= 4; i++) {
      const v = Math.round((max * i) / 4);
      el('line', { x1: L0, x2: W, y1: y(v), y2: y(v), stroke: '#e6e6e6', 'stroke-width': 1 });
      el('text', { x: L0 - 8, y: y(v) + 4, 'text-anchor': 'end', 'font-size': 11, fill: '#8f8f8f' }, v);
    }
    const step = Math.max(1, Math.ceil(points.length / 8));
    points.forEach((p, i) => { if (i % step === 0 || i === points.length - 1) el('text', { x: x(i), y: H - 6, 'text-anchor': 'middle', 'font-size': 11, fill: '#8f8f8f' }, p.date.slice(5)); });
    for (const s of series) {
      el('polyline', { points: points.map((p, i) => `${x(i)},${y(p[s.key])}`).join(' '), fill: 'none', stroke: s.color, 'stroke-width': 2 });
      points.forEach((p, i) => { const c = el('circle', { cx: x(i), cy: y(p[s.key]), r: 4, fill: s.color }); c.setAttribute('data-tip', `${p.date} · ${s.label}: ${p[s.key]}${U().people}`); });
    }
    return h('div', { class: 'line' }, svg);
  }

  // ----------------------------------------------------------------------- //
  // 틀
  // ----------------------------------------------------------------------- //
  /** 비밀 관리자 링크로 열지 않은 경우: 로그인 화면 대신 '없는 페이지'. 문구를 받을 수 없는 상태라 예외적으로 코드에 둔다 */
  function notFound() {
    document.title = '404';
    $root.replaceChildren(h('div', { class: 'login' }, h('div', null, h('h1', null, '404'), h('p', { class: 'muted' }, '페이지를 찾을 수 없어요.'))));
  }
  async function showLogin() {
    const res = await fetch('/api/admin/login-text', { cache: 'no-store', headers: linkHeader() }).catch(() => null);
    if (res && res.status === 404) { store.del(LINK_STORE); notFound(); return; }
    const r = res ? await res.json().catch(() => null) : null;
    const T = (r && r.login) || {};
    const input = h('input', { type: 'password', id: 'pw', autocomplete: 'current-password', required: true });
    const err = h('p', { class: 'err', role: 'alert' });
    const form = h('form', null,
      h('h1', null, (r && r.app.title) || ''), h('p', { class: 'muted' }, T.body || ''),
      h('label', { for: 'pw', style: 'font-weight:700' }, T.password || ''), input, err,
      h('button', { type: 'submit', class: 'btn primary', style: 'justify-content:center;height:48px' }, T.submit || 'OK'));
    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      err.textContent = '';
      const res = await api('POST', '/api/admin/login', { password: input.value });
      if (res.ok) { input.value = ''; boot(); return; }
      if (res.status === 423) err.textContent = fmt(T.locked, { time: new Date(res.data.message).toLocaleTimeString('ko-KR', { hour: '2-digit', minute: '2-digit' }) });
      else if (res.status === 503) err.textContent = T.not_configured;
      else err.textContent = T.wrong;
      input.select();
    });
    $root.replaceChildren(h('div', { class: 'login' }, form));
    input.focus();
  }

  function shell() {
    const isFull = ME.role === 'full';
    const exportMenu = h('div', { class: 'menu', hidden: true },
      ['leaders', 'checks', 'texts', 'screens'].map((k) => h('a', { href: `/api/admin/export/${k}.csv${query()}`, download: '' }, A.export_menu[k])),
      h('p', { class: 'note' }, A.export_menu.note));
    const exportBtn = isFull ? h('button', { type: 'button', class: 'btn primary', 'aria-haspopup': 'true', onclick: () => { exportMenu.hidden = !exportMenu.hidden; } }, A.app.export) : null;
    document.addEventListener('click', (e) => { if (exportBtn && !exportBtn.contains(e.target) && !exportMenu.contains(e.target)) exportMenu.hidden = true; });
    const cycleSel = h('select', { class: 'btn', 'aria-label': 'cycle', onchange: (e) => { F.cycle = e.target.value === ME.cycle_id ? null : e.target.value; L.page = 1; render(); } },
      ME.cycles.map((c) => h('option', { value: c, selected: (F.cycle || ME.cycle_id) === c }, c === ME.cycle_id ? `${ME.cycle_name} (${c})` : c)));
    const tabs = ['dashboard', 'items', 'leaders', 'flow'].concat(isFull ? ['settings'] : []);
    const nav = h('nav', { class: 'nav', 'aria-label': A.app.badge }, tabs.map((t) => h('button', { type: 'button', 'aria-current': t === tab ? 'page' : null, onclick: () => { location.hash = t; } }, TAB_NO[t] ? `${TAB_NO[t]}. ${A.tabs[t]}` : A.tabs[t])));
    const typeSel = h('select', { id: 'f-type' }, h('option', { value: '' }, A.filters.all), TYPES.map((t) => h('option', { value: t, selected: F.type === t }, typeLabel(t))));
    const from = h('input', { type: 'date', id: 'f-from', value: F.from, 'aria-label': A.filters.from });
    const to = h('input', { type: 'date', id: 'f-to', value: F.to, 'aria-label': A.filters.to });
    const filters = tab === 'settings' ? null : h('div', { class: 'filters', role: 'group' },
      h('label', { for: 'f-type' }, A.filters.type, typeSel),
      h('label', null, A.filters.period, from, '–', to),
      h('button', { type: 'button', class: 'btn sm', onclick: () => { F.type = typeSel.value; F.from = from.value; F.to = to.value; L.page = 1; render(); } }, A.filters.apply),
      h('button', { type: 'button', class: 'link', onclick: () => { F.type = ''; F.from = ''; F.to = ''; L.page = 1; render(); } }, A.filters.reset),
      h('span', { class: 'note' }, A.app.privacy_note));
    return [
      h('header', { class: 'top' },
        h('div', { class: 'brand' }, h('b', null, A.app.title), h('span', { class: 'pill' }, A.app.badge), h('span', { class: 'pill' }, isFull ? A.app.role_full : A.app.role_readonly)),
        h('div', { class: 'top-r' }, cycleSel, exportBtn, exportMenu,
          h('button', { type: 'button', class: 'link', onclick: async () => { await api('POST', '/api/admin/logout'); showLogin(); } }, A.app.logout))),
      nav, filters,
    ];
  }

  async function render() {
    const my = ++renderToken;
    const main = h('main', null, h('p', { class: 'muted' }, A.app.loading));
    $root.replaceChildren(...shell().filter(Boolean), main);
    let content;
    try {
      if (tab === 'dashboard') content = await dashboard();
      else if (tab === 'items') content = await items();
      else if (tab === 'leaders') content = await leaders();
      else if (tab === 'flow') content = await flow();
      else content = await settings();
    } catch (e) {
      content = [h('p', { class: 'err' }, A.app.load_error), h('button', { type: 'button', class: 'btn sm', onclick: render }, A.app.retry)];
    }
    if (my !== renderToken) return;
    main.replaceChildren(...[].concat(content).filter(Boolean), h('p', { class: 'foot' }, A.footer));
  }
  async function get(path) {
    const r = await api('GET', path);
    if (!r.ok) throw new Error(String(r.status));
    return r.data;
  }

  // ----------------------------------------------------------------------- //
  // 대시보드
  // ----------------------------------------------------------------------- //
  async function dashboard() {
    const d = await get(`/api/admin/dashboard${query()}`);
    const T = A.dashboard; const p = d.participation; const r = d.recall; const ap = d.application;
    const num = numberer(TAB_NO.dashboard); const IN = A.insights.dashboard; const KT = A.insights.kpi_titles;
    const ppl = (v) => n(v, U().people);
    const stageMax = Math.max(p.target || 0, ...p.stages.map((s) => s.count));
    const out = [];
    // 등록이 대상 인원을 넘으면 중복(3.1)·대상 밖 유입(4.6)을 확인하도록(2026-11-27)
    const overWarn = p.over_target ? fmt(T.over_target, { n: p.registered, target: p.target }) : null;
    out.push(secHead(T.s1), kpiBlock({ no: num(), title: KT.dashboard_kpi, insight: IN.kpi, warn: overWarn }, [
      [T.kpi.target[0], ppl(p.target), T.kpi.target[1]],
      [`${T.kpi.registered[0]} (${A.app.estimated})`, ppl(p.registered), fmt(T.kpi.registered[1], { pct: n(p.registered_pct) })],
      [T.kpi.step1_done[0], ppl(p.step1_done), fmt(T.kpi.step1_done[1], { pct: n(p.step1_done_pct) })],
      [T.kpi.check_done[0], ppl(p.check_done), fmt(T.kpi.check_done[1], { pct: n(p.check_done_pct) })],
      [T.kpi.members_median[0], ppl(p.members_median), T.kpi.members_median[1]],
      [T.kpi.duration[0], n(p.duration_median_min, U().min), T.kpi.duration[1]],
    ]));
    out.push(card({ no: num(), insight: IN.trend, title: T.trend.title, sub: T.trend.sub, span: true,
      legend: legend([['var(--c1)', T.trend.registered], ['var(--c2)', T.trend.done]]),
      chart: () => lineChart(p.trend, [{ key: 'registered', color: '#00703C', label: T.trend.registered }, { key: 'done', color: '#2f6fd6', label: T.trend.done }]),
      table: () => table([T.trend.date, T.trend.registered, T.trend.done], p.trend.map((x) => [x.date, x.registered, x.done]), [1, 2]) }));
    const stageRows = p.stages.map((s) => ({ label: T.stages.labels[s.key], value: s.count, text: ppl(s.count), tip: `${T.stages.labels[s.key]}: ${ppl(s.count)} · ${pctT(p.target ? Math.round((1000 * s.count) / p.target) / 10 : null)}` }));
    const typeRows = p.leader_types.map((t) => ({ label: typeLabel(t.type), value: t.count, text: ppl(t.count) }));
    const distRows = p.members_dist.map((x) => ({ label: T.members_dist.labels[x.bucket], value: x.count, text: ppl(x.count) }));
    const compRows = p.type_completion.map((x) => ({ label: typeLabel(x.type), value: x.pct || 0, text: pctT(x.pct) }));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.stages, title: T.stages.title, sub: fmt(T.stages.sub, { target: p.target }), chart: () => bars(stageRows, { max: stageMax, lw: '170px' }), table: () => table(['', U().people], stageRows.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.types, title: T.types.title, sub: T.types.sub, note: T.types.note, chart: () => bars(typeRows), table: () => table(['', U().people], typeRows.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.members_dist, title: T.members_dist.title, sub: T.members_dist.sub, note: T.members_dist.note, chart: () => bars(distRows), table: () => table(['', U().people], distRows.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.type_completion, title: T.type_completion.title, sub: T.type_completion.sub, chart: () => bars(compRows, { max: 100 }), table: () => table(['', U().pct], compRows.map((x) => [x.label, x.text]), [1]) })));

    // 2 기억
    out.push(secHead(T.s2), kpiBlock({ no: num(), title: KT.dashboard_kpi2, insight: IN.kpi2 }, [
      [T.kpi2.avg_remember[0], pctT(r.avg_remember_pct), T.kpi2.avg_remember[1]],
      [T.kpi2.all_remember[0], pctT(r.all_remember_pct), T.kpi2.all_remember[1]],
      [T.kpi2.no_keyword[0], pctT(r.no_keyword_pct), T.kpi2.no_keyword[1]],
    ]));
    const revRows = r.by_review.map((x) => ({ label: A.review_short[x.review] || x.review, value: x.pct || 0, text: pctT(x.pct) }));
    const kwRows = r.by_keyword.map((x) => ({ label: kwLabel(x.keyword), value: x.pct || 0, text: pctT(x.pct) }));
    const dw = T.review_dwell;
    const dwellRows = r.review_dwell.map((x) => ({ label: A.review_short[x.review] || x.review, a: x.remembered_s, b: x.not_remembered_s,
      textA: n(x.remembered_s, U().sec), textB: n(x.not_remembered_s, U().sec), tip: `${dw.remembered} ${n(x.remembered_s, U().sec)} · ${dw.not_remembered} ${n(x.not_remembered_s, U().sec)} · ${dw.diff} ${n(x.diff_s, U().sec)} (${x.leaders}${U().people})` }));
    const cntRows = r.keyword_count.map((x) => ({ label: fmt(T.keyword_way.count_label, { n: x.count }), value: x.pct || 0, text: pctT(x.pct) }));
    const firstRows = r.first_keyword.map((x) => ({ label: kwLabel(x.keyword), value: x.pct || 0, text: pctT(x.pct) }));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.by_review, title: T.by_review.title, sub: T.by_review.sub, chart: () => bars(revRows, { max: 100 }), table: () => table(['', U().pct], revRows.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.by_keyword, title: T.by_keyword.title, sub: T.by_keyword.sub, chart: () => bars(kwRows, { max: 100, lw: '160px' }), table: () => table(['', U().pct], kwRows.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.review_dwell, title: dw.title, sub: dw.sub, legend: legend([['var(--c1)', dw.remembered], ['var(--cmp)', dw.not_remembered]]),
        chart: () => bars2(dwellRows),
        table: () => table(['', dw.remembered, dw.not_remembered, dw.diff], r.review_dwell.map((x) => [A.review_short[x.review], n(x.remembered_s, U().sec), n(x.not_remembered_s, U().sec), n(x.diff_s, U().sec)]), [1, 2, 3]) }),
      card({ no: num(), insight: IN.keyword_way, title: T.keyword_way.title, chart: () => h('div', { style: 'display:flex;flex-direction:column;gap:14px' },
        h('p', { class: 'group-h' }, T.keyword_way.count), bars(cntRows, { max: 100, lw: '160px' }),
        h('p', { class: 'group-h' }, T.keyword_way.first), bars(firstRows, { max: 100, lw: '160px' })),
      table: () => table(['', U().pct], cntRows.concat(firstRows).map((x) => [x.label, x.text]), [1]) })));

    // 3 적용
    out.push(secHead(T.s3), kpiBlock({ no: num(), title: KT.dashboard_kpi3, insight: IN.kpi3 }, [
      [T.kpi3.behavior[0], pctT(ap.behavior_rate_pct), T.kpi3.behavior[1]],
      [T.kpi3.care_high[0], pctT(ap.care_high_pct), T.kpi3.care_high[1]],
      [T.kpi3.challenge_high[0], pctT(ap.challenge_high_pct), T.kpi3.challenge_high[1]],
      [T.kpi3.sent[0], pctT(ap.sent_pct), T.kpi3.sent[1]],
    ]));
    const tb = T.top_bottom;
    const topRows = ap.top_items.map((x) => ({ label: itemText(x.item), value: x.pct || 0, text: pctT(x.pct) }));
    const botRows = ap.bottom_items.map((x) => ({ label: itemText(x.item), value: x.pct || 0, text: pctT(x.pct) }));
    const qmax = Math.max(1, ...ap.quadrants.map((x) => x.pct || 0));
    const qv = Object.fromEntries(ap.quadrants.map((x) => [x.quadrant, x.pct]));
    const quadChart = () => h('div', { class: 'quad' }, h('span', { class: 'axis' }, T.quadrants.care),
      h('div', { class: 'q' }, ['re', 'rc', 'mi', 'oa'].map((q) => { const s = shade(qv[q], qmax); return h('div', { style: `background:${s.bg};color:${s.fg}`, 'data-tip': `${C.quadrants[q].label}: ${pctT(qv[q])}` }, h('span', null, h('b', null, C.quadrants[q].label), ' ', h('span', { class: 'small' }, C.quadrants[q].sub)), h('b', { class: 'num' }, pctT(qv[q]))); })),
      h('span', { class: 'axis r' }, T.quadrants.challenge));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.top_bottom, title: tb.title, sub: tb.sub, chart: () => h('div', { style: 'display:flex;flex-direction:column;gap:12px' }, h('p', { class: 'group-h' }, tb.top), bars(topRows, { max: 100, lw: '260px' }), h('p', { class: 'group-h' }, tb.bottom), bars(botRows, { max: 100, lw: '260px', color: 'var(--cmp)' })),
        table: () => table(['', U().pct], topRows.concat(botRows).map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.quadrants, title: T.quadrants.title, sub: T.quadrants.sub, chart: quadChart, table: () => table(['', U().pct], QUADS.map((q) => [C.quadrants[q].label, pctT(qv[q])]), [1]),
        // 한 칸 쏠림 경고(2026-11-27): 설정의 '판정 기준값 미리보기'로 안내
        note: ap.quadrant_skew && ap.quadrant_skew.skewed ? fmt(T.quadrants.skew, { label: C.quadrants[ap.quadrant_skew.quadrant].label, pct: pctT(ap.quadrant_skew.pct) }) : null })));
    const X = T.cross;
    const crossTable = () => {
      const max = Math.max(1, ...ap.cross.flatMap((r2) => QUADS.map((q) => r2.cells[q] || 0)));
      return h('table', { class: 't cross' },
        h('thead', null, h('tr', null, h('th', null, X.corner), QUADS.map((q) => h('th', { style: 'text-align:center' }, C.quadrants[q].label, h('br'), h('span', { class: 'small muted' }, C.quadrants[q].sub))), h('th', { class: 'n' }, X.checks))),
        h('tbody', null, ap.cross.map((row) => h('tr', null, h('th', { style: 'font-weight:700;color:#000' }, typeLabel(row.type)),
          QUADS.map((q) => { const s = shade(row.cells[q], max); return h('td', { class: 'cell num' + (row.type === q ? ' diag' : ''), style: `background:${s.bg};color:${s.fg}`, 'data-tip': `${typeLabel(row.type)} → ${C.quadrants[q].label}: ${pctT(row.cells[q])} (${row.checks}${U().count})` }, pctT(row.cells[q])); }),
          h('td', { class: 'n' }, n(row.checks, U().count))))));
    };
    out.push(card({ no: num(), insight: IN.cross, title: X.title, sub: X.sub, note: X.note, chart: crossTable, span: true }));

    // 2→3 기억한 리더와 실천한 리더: 핵심 메시지별 기억난 리더 비율과 실천한 리더 비율(주 지표)
    const RP = T.recall_practice;
    const rpPct = (v, cnt) => (v === null ? (cnt ? RP.small : D()) : pctT(v));
    const withPct = (cnt, v) => (v === null ? n(cnt, U().people) : `${n(cnt, U().people)} (${pctT(v)})`);
    const rpRows = d.recall_practice.map((x) => ({
      label: A.review_short[x.review], a: x.remembered_pct, b: x.practiced_pct,
      textA: rpPct(x.remembered_pct, x.leaders), textB: rpPct(x.practiced_pct, x.leaders),
      tip: `${RP.remembered} ${x.remembered}/${x.leaders}${U().people} · ${RP.practiced} ${x.practiced}/${x.leaders}${U().people} · ${RP.cols[3]} ${x.remembered_not_practiced}${U().people}`,
    }));
    const rpCard = card({ no: num(), insight: IN.recall_practice, title: RP.title, sub: RP.sub, note: RP.note, span: true,
      legend: legend([['var(--c1)', RP.remembered], ['var(--cmp)', RP.practiced]]),
      chart: () => bars2(rpRows, { max: 100, lw: '220px' }),
      table: () => table([''].concat(RP.cols), d.recall_practice.map((x) => [A.review_short[x.review],
        withPct(x.remembered, x.remembered_pct), withPct(x.practiced, x.practiced_pct),
        n(x.both, U().people), n(x.remembered_not_practiced, U().people),
        rpPct(x.practiced_of_remembered_pct, x.remembered)]), [1, 2, 3, 4, 5]) });

    // 2→3 보조: 고른 키워드와 실천
    const TR = T.transfer;
    const trRows = d.transfer.map((x) => ({ label: kwLabel(x.keyword), a: x.picked_pct, b: x.not_picked_pct, textA: pctT(x.picked_pct), textB: n(x.diff_pp, U().pp),
      tip: `${TR.picked} ${pctT(x.picked_pct)} (${x.picked_n}${U().people}) · ${TR.not_picked} ${pctT(x.not_picked_pct)} (${x.not_picked_n}${U().people}) · ${TR.diff} ${n(x.diff_pp, U().pp)}` }));
    // 같은 체크 문항을 함께 쓰는 키워드(2026-11-27): content.json 의 연결에서 계산해 차이를 따로 해석하지 않도록 알린다
    const sharedBy = {};
    C.keywords.items.forEach((k) => k.linked_items.forEach((i) => { (sharedBy[i] = sharedBy[i] || []).push(k.label); }));
    const shared = Object.entries(sharedBy).filter(([, ks]) => ks.length > 1)
      .map(([i, ks]) => `${ks.join('·')}(‘${itemText(i)}’)`);
    const trNote = shared.length ? `${TR.note} ${fmt(TR.shared, { pairs: shared.join(', ') })}` : TR.note;
    out.push(secHead(T.s23), rpCard, card({ no: num(), insight: IN.transfer, title: TR.title, sub: TR.sub, note: trNote, span: true,
      legend: legend([['var(--c1)', TR.picked], ['var(--cmp)', TR.not_picked]]),
      chart: () => bars2(trRows, { max: 100, lw: '180px' }),
      table: () => table(['', TR.picked, TR.not_picked, TR.diff], d.transfer.map((x) => [kwLabel(x.keyword), pctT(x.picked_pct), pctT(x.not_picked_pct), n(x.diff_pp, U().pp)]), [1, 2, 3]) }));
    out.push(h('div', { class: 'box-note' }, h('b', null, T.level4.title), h('br'), T.level4.body), h('p', { class: 'note' }, T.notice));
    return out;
  }

  // ----------------------------------------------------------------------- //
  // 문항 분석
  // ----------------------------------------------------------------------- //
  async function items() {
    const d = await get(`/api/admin/items${query()}`);
    const T = A.items;
    const num = numberer(TAB_NO.items); const IN = A.insights.items;
    const out = [];
    const behaviorChart = () => h('div', { style: 'display:flex;flex-direction:column;gap:10px' }, ['care', 'challenge', null].map((axis) => [
      h('p', { class: 'group-h' }, T.behavior.axis[axis || 'none']),
      bars(d.behavior.filter((x) => x.axis === axis).map((x) => ({ label: itemText(x.item), value: x.rate || 0, text: pctT(x.rate), tip: `${itemText(x.item)}: ${pctT(x.rate)} · ${T.behavior.leaders} ${x.leaders}${U().people}` })), { max: 100, lw: '340px' })]));
    out.push(card({ no: num(), insight: IN.behavior, title: T.behavior.title, sub: T.behavior.sub, note: T.behavior.note, span: true, chart: behaviorChart,
      table: () => table(['', U().pct, T.behavior.leaders], d.behavior.map((x) => [itemText(x.item), pctT(x.rate), n(x.leaders, U().people)]), [1, 2]) }));
    const ah = [['care', T.axis_high.care], ['challenge', T.axis_high.challenge]].map(([k, l]) => ({ label: l, value: d.axis_high[k] || 0, text: pctT(d.axis_high[k]) }));
    const sg = d.signals.map((x) => ({ label: A.signal_short[x.item] || itemText(x.item), value: x.rate || 0, text: pctT(x.rate), tip: `${itemText(x.item)}: ${pctT(x.rate)} · ${x.leaders}${U().people}` }));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.axis_high, title: T.axis_high.title, chart: () => bars(ah, { max: 100 }), table: () => table(['', U().pct], ah.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.signals, title: T.signals.title, sub: T.signals.sub, chart: () => bars(sg, { max: 100, lw: '220px', color: 'var(--cmp)' }), table: () => table(['', U().pct], sg.map((x) => [x.label, x.text]), [1]) })));
    const Q = T.quality; const qd = d.quality;
    const dirRows = Object.entries(Q.dir).map(([k, l]) => ({ label: l, value: qd.directions[k] || 0, text: pctT(qd.directions[k]) }));
    const chRows = d.answer_changed_top.map((x) => ({ label: itemText(x.item), value: x.pct || 0, text: pctT(x.pct) }));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.quality, title: Q.title, sub: Q.sub, note: Q.note, chart: () => h('div', { style: 'display:flex;flex-direction:column;gap:14px' },
        kpis([[Q.changed[0], pctT(qd.changed_after_result_pct), Q.changed[1]], [Q.items[0], n(qd.changed_items_median, U().items), Q.items[1]]]),
        h('p', { class: 'group-h' }, Q.directions), bars(dirRows, { max: 100, lw: '240px' })),
      table: () => table(['', U().pct], dirRows.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.changed, title: T.changed.title, sub: T.changed.sub, note: T.changed.note, chart: () => bars(chRows, { max: 100, lw: '300px' }), table: () => table(['', U().pct], chRows.map((x) => [x.label, x.text]), [1]) })));
    const AC = T.actions; const ac = d.actions;
    const acRows = Object.entries(AC.labels).map(([k, l]) => ({ label: l, value: ac[k] || 0, text: pctT(ac[k]) }));
    const afRows = Object.entries(AC.after_labels).map(([k, l]) => ({ label: l, value: d.after_done[k] || 0, text: pctT(d.after_done[k]) }));
    const sitRows = d.situations.map((x) => ({ label: sitLabel(x.situation), value: x.pct || 0, text: pctT(x.pct) }));
    const scRows = d.situation_count.map((x) => ({ label: T.situations.count_labels[x.bucket], value: x.pct || 0, text: pctT(x.pct) }));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.actions, title: AC.title, sub: `${AC.sub} · ${n(ac.checks, U().count)}`, chart: () => h('div', { style: 'display:flex;flex-direction:column;gap:12px' }, bars(acRows, { max: 100, lw: '260px' }), h('p', { class: 'group-h' }, AC.after), bars(afRows, { max: 100, lw: '260px' })),
        table: () => table(['', U().pct], acRows.concat(afRows).map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.situations, title: T.situations.title, sub: T.situations.sub, chart: () => h('div', { style: 'display:flex;flex-direction:column;gap:12px' }, bars(sitRows, { max: 100, lw: '220px' }), h('p', { class: 'group-h' }, T.situations.count), bars(scRows, { max: 100, lw: '220px' })),
        table: () => table(['', U().pct], sitRows.concat(scRows).map((x) => [x.label, x.text]), [1]) })));
    const TX = T.texts;
    // 전체 권한은 문장마다 '삭제'(구성원 실명 등 개인을 알아볼 수 있는 말이 보일 때). 같은 문장은 모두 지워진다
    const delText = async (t) => {
      if (!window.confirm(TX.delete_confirm)) return;
      const r = await api('POST', '/api/admin/texts/delete', { text: t, cycle: F.cycle });
      if (r.ok) { toast(TX.deleted); render(); } else toast(r.status === 404 ? TX.delete_missing : A.settings.errors.generic);
    };
    const textLi = (t) => h('li', { class: 'text-li' }, h('span', null, `“${t}”`),
      ME.role === 'full' ? h('button', { type: 'button', class: 'link danger', 'aria-label': TX.delete_aria, onclick: () => delText(t) }, TX.delete) : null);
    out.push(card({ no: num(), insight: IN.texts, title: TX.title, sub: TX.sub, span: true, note: ME.role === 'full' ? TX.delete_note : null, chart: () => h('div', { style: 'display:flex;flex-direction:column;gap:8px' },
      d.custom_texts.length ? h('ul', { style: 'margin:0;padding-left:18px;display:flex;flex-direction:column;gap:6px' }, d.custom_texts.map(textLi)) : h('p', { class: 'empty' }, A.app.empty),
      h('p', { class: 'small muted' }, fmt(TX.total, { n: d.custom_text_count }), d.can_export ? [' · ', h('a', { href: `/api/admin/export/texts.csv${query()}`, download: '' }, TX.export)] : null)) }));
    out.push(h('p', { class: 'note' }, T.notice));
    return out;
  }

  // ----------------------------------------------------------------------- //
  // 참여자
  // ----------------------------------------------------------------------- //
  async function leaders() {
    const d = await get(`/api/admin/leaders${query({ stage: L.stage, q: L.q, sort: L.sort, desc: L.desc ? 'true' : 'false', page: L.page, size: L.size })}`);
    const T = A.leaders;
    const chip = (key, label) => h('button', { type: 'button', class: 'chipf', 'aria-pressed': String(L.stage === key), onclick: () => { L.stage = key; L.page = 1; render(); } }, label);
    const search = h('input', { type: 'search', class: 'search', placeholder: T.search, value: L.q, 'aria-label': T.search });
    search.addEventListener('keydown', (e) => { if (e.key === 'Enter') { L.q = search.value.trim(); L.page = 1; render(); } });
    search.addEventListener('search', () => { L.q = search.value.trim(); L.page = 1; render(); });
    const sortable = { leader_name: 1, started: 1, stage: 1, last_access: 1 };
    const head = Object.entries(T.cols).map(([k, l]) => (sortable[k]
      ? h('th', { 'aria-sort': L.sort === k ? (L.desc ? 'descending' : 'ascending') : null }, h('button', { type: 'button', onclick: () => { if (L.sort === k) L.desc = !L.desc; else { L.sort = k; L.desc = k !== 'leader_name'; } L.page = 1; render(); } }, l, L.sort === k ? (L.desc ? ' ↓' : ' ↑') : ''))
      : h('th', null, l)));
    const rows = d.rows.map((r) => h('tr', null,
      h('td', null, h('b', null, r.leader_name), ' ', h('span', { class: 'serial' }, `#${r.serial}`),
        // 직접 지은 리더명이 같은 참여자가 여럿: 다른 기기로 다시 시작한 중복일 수 있음(2026-11-27)
        r.same_name ? [' ', h('span', { class: 'dup-chip', 'data-tip': T.same_name_tip }, fmt(T.same_name, { n: r.same_name }))] : null,
        // 사람 이름처럼 보이는 리더명(2026-11-27)
        r.name_suspect ? [' ', h('span', { class: 'dup-chip', 'data-tip': T.name_suspect_tip }, T.name_suspect)] : null),
      h('td', { class: 'num' }, r.started || D()),
      h('td', null, T.stages[r.stage]),
      h('td', null, screenLabel(r.last_screen)),
      h('td', null, r.sent === null ? D() : (r.sent ? T.sent.yes : T.sent.no)),
      h('td', { class: 'n' }, n(r.members, U().people)),
      h('td', null, r.trends.length ? r.trends.map((t) => h('span', { class: `tchip ${t.quadrant}`, 'data-tip': fmt(T.trend_title, { time: t.completed_at }) }, A.quad_short[t.quadrant] || D())) : D()),
      h('td', { class: 'num' }, r.last_access || D())));
    // 페이지 번호: 현재 포함 연속 5개 + 첫·마지막, 사이는 …
    const pages = [];
    const start = Math.max(1, Math.min(d.page - 2, d.pages - 4));
    const end = Math.min(d.pages, start + 4);
    if (start > 1) pages.push(1);
    if (start > 2) pages.push('…');
    for (let i = start; i <= end; i++) pages.push(i);
    if (end < d.pages - 1) pages.push('…');
    if (end < d.pages) pages.push(d.pages);
    const go = (p) => { L.page = p; render(); };
    const from = d.total ? (d.page - 1) * d.size + 1 : 0;
    const to = Math.min(d.total, d.page * d.size);
    return [
      h('section', { class: 'card' },
        h('div', { class: 'card-h' }, h('h3', null, noBadge(`${TAB_NO.leaders}.1`), T.title), h('span', { class: 'small muted' }, T.sub)),
        h('div', { class: 'toolbar' },
          chip('', fmt(T.stage_filter.all, { n: d.counts.all })), chip('step1', fmt(T.stage_filter.step1, { n: d.counts.step1 })),
          chip('checking', fmt(T.stage_filter.checking, { n: d.counts.checking })), chip('done', fmt(T.stage_filter.done, { n: d.counts.done })),
          search),
        h('table', { class: 't' }, h('thead', null, h('tr', null, head)), h('tbody', null, rows.length ? rows : h('tr', null, h('td', { colspan: 8, class: 'empty' }, A.app.empty)))),
        h('div', { class: 'pager' },
          h('span', { class: 'muted' }, fmt(T.range, { total: d.total, from, to })),
          h('div', { class: 'pages' },
            h('button', { type: 'button', 'aria-label': T.prev, disabled: d.page <= 1, onclick: () => go(d.page - 1) }, '‹'),
            pages.map((p) => (p === '…' ? h('span', { class: 'muted' }, '…') : h('button', { type: 'button', 'aria-current': p === d.page ? 'page' : null, onclick: () => go(p) }, p))),
            h('button', { type: 'button', 'aria-label': T.next, disabled: d.page >= d.pages, onclick: () => go(d.page + 1) }, '›')),
          h('label', { class: 'muted' }, T.page_size, ' ', h('select', { class: 'chipf', onchange: (e) => { L.size = Number(e.target.value); L.page = 1; render(); } },
            [20, 50, 100].map((s) => h('option', { value: s, selected: L.size === s }, fmt(T.page_size_unit, { n: s })))))),
        h('p', { class: 'note' }, T.note), insightEl(A.insights.leaders.table)),
    ];
  }

  // ----------------------------------------------------------------------- //
  // 사용 흐름
  // ----------------------------------------------------------------------- //
  async function flow() {
    const d = await get(`/api/admin/flow${query()}`);
    const T = A.flow; const s = d.summary;
    const num = numberer(TAB_NO.flow); const IN = A.insights.flow;
    const out = [kpiBlock({ no: num(), title: A.insights.kpi_titles.flow_kpi, insight: IN.kpi }, [
      [T.kpi.step1[0], n(s.step1_min, U().min), T.kpi.step1[1]],
      [T.kpi.first[0], n(s.first_check_min, U().min), T.kpi.first[1]],
      [T.kpi.later[0], n(s.later_check_min, U().min), T.kpi.later[1]],
      [T.kpi.resume[0], pctT(s.resume_pct), T.kpi.resume[1]],
      [T.kpi.back[0], n(s.back_per_leader, U().times), T.kpi.back[1]],
    ])];
    const scrNo = (i, sc) => `${i + 1}. ${screenLabel(sc)}`;   // 화면 순서 번호(항목 번호 num 과 다름)
    const scrRows = d.screens.map((x, i) => ({ label: scrNo(i, x.screen), value: x.reached, text: n(x.reached, U().people), extra: fmt(T.screens.stopped, { n: x.stopped }) }));
    const dwRows = d.dwell.map((x, i) => (x.excluded
      ? { label: scrNo(i, x.screen), value: 0, text: D(), tip: T.dwell.excluded, extra: '' }
      : { label: scrNo(i, x.screen), value: x.median_s || 0, text: n(x.median_s, U().sec), tip: `${screenLabel(x.screen)} · ${T.dwell.median} ${n(x.median_s, U().sec)} · ${T.dwell.p75} ${n(x.p75_s, U().sec)} · ${T.dwell.leaders} ${x.leaders}${U().people}` }));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.screens, title: T.screens.title, sub: T.screens.sub, chart: () => bars(scrRows, { lw: '190px', extra: true }),
        table: () => table(['', T.screens.reached, T.screens.stopped_col], d.screens.map((x, i) => [scrNo(i, x.screen), x.reached, x.stopped]), [1, 2]) }),
      card({ no: num(), insight: IN.dwell, title: T.dwell.title, sub: T.dwell.sub, note: T.dwell.note, chart: () => bars(dwRows, { lw: '190px' }),
        table: () => table(['', T.dwell.median, T.dwell.p75, T.dwell.leaders], d.dwell.map((x, i) => (x.excluded ? [scrNo(i, x.screen), T.dwell.excluded, '', ''] : [scrNo(i, x.screen), n(x.median_s, U().sec), n(x.p75_s, U().sec), x.leaders])), [1, 2, 3]) })));
    const backRows = d.back_top.map((x) => ({ label: screenLabel(x.screen), value: x.per_leader || 0, text: n(x.per_leader, U().times) }));
    const dv = T.devices;
    // 앱 안 브라우저로 연 기기(2026-11-27): PDF 받기·복사·이어 하기가 막힐 수 있는 환경이 얼마나 되는지
    const ia = d.devices.inapp || { devices: 0, by_browser: [] };
    const inappLine = h('p', { class: 'note' }, fmt(dv.inapp, { n: ia.devices, pct: pctT(ia.pct) }),
      ia.by_browser.length ? ` (${ia.by_browser.map((x) => `${dv.browsers[x.browser] || x.browser} ${x.devices}`).join(' · ')})` : '');
    const devTable = () => h('div', null, table(['', dv.share, dv.completion, fmt(dv.duration, { d: '' }).trim()], ['mobile', 'pc'].map((k) => [dv[k], pctT(d.devices[k].share), pctT(d.devices[k].completion), n(d.devices[k].duration_median_min, U().min)]), [1, 2, 3]), inappLine);
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.back, title: T.back.title, sub: T.back.sub, chart: () => bars(backRows), table: () => table(['', U().times], backRows.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.devices, title: dv.title, sub: dv.sub, chart: devTable })));
    const DA = T.done_actions;
    const daRows = Object.entries(DA.labels).map(([k, l]) => ({ label: l, value: d.done_actions[k] || 0, text: pctT(d.done_actions[k]) }));
    const EN = T.entry;
    const enRows = Object.entries(EN.labels).map(([k, l]) => ({ label: l, value: d.entry[k] || 0, text: pctT(d.entry[k]) }));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.done_actions, title: DA.title, sub: DA.sub, note: fmt(DA.note, { n: d.done_actions.recommend_visits }), chart: () => bars(daRows, { max: 100, lw: '260px' }), table: () => table(['', U().pct], daRows.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.entry, title: EN.title, sub: `${EN.sub} · ${n(d.entry.notice_devices, U().count)}`, note: EN.note, chart: () => h('div', { style: 'display:flex;flex-direction:column;gap:12px' },
        bars(enRows, { max: 100, lw: '220px' }), h('p', { class: 'group-h' }, EN.denied),
        table([EN.denied_missing, EN.denied_wrong], [[n(d.entry.denied_missing, U().count), n(d.entry.denied_wrong, U().count)]], [0, 1])),
      table: () => table(['', U().pct], enRows.map((x) => [x.label, x.text]).concat([[EN.denied_missing, n(d.entry.denied_missing, U().count)], [EN.denied_wrong, n(d.entry.denied_wrong, U().count)]]), [1]) })));
    // 완료 화면 버튼 클릭 수(누를 때마다 1회)
    const DC = T.done_clicks;
    const clickRows = d.done_actions.clicks.map((x) => ({
      label: DC.buttons[x.button], value: x.clicks, text: n(x.clicks, U().times),
      extra: `${n(x.leaders, U().people)} · ${pctT(x.leaders_pct)}`,
      tip: `${DC.buttons[x.button]}: ${n(x.clicks, U().times)} · ${n(x.leaders, U().people)}`,
    }));
    out.push(card({ no: num(), insight: IN.done_clicks, title: DC.title, sub: fmt(DC.sub, { n: d.done_actions.at_done }), note: DC.note,
      chart: () => bars(clickRows, { extra: true, lw: '200px' }),
      table: () => table([''].concat(DC.cols), d.done_actions.clicks.map((x) => [DC.buttons[x.button], n(x.clicks, U().times), n(x.leaders, U().people), pctT(x.leaders_pct)]), [1, 2, 3]) }));
    const RE = T.reaction;
    const reRows =Object.entries(RE.labels).map(([k, l]) => ({ label: l, value: d.reaction[k] || 0, text: pctT(d.reaction[k]) }));
    const HM = T.heatmap;
    const hmax = Math.max(1, ...d.heatmap.flat());
    const heat = () => h('div', { class: 'heat', role: 'img' }, h('span', null), HM.hours.map((x) => h('span', { class: 'h' }, x)),
      HM.days.map((day, di) => [h('span', { class: 'd' }, day), d.heatmap[di].map((v, bi) => { const c = shade(v, hmax); return h('span', { class: 'c num', style: `background:${c.bg};color:${c.fg}`, 'data-tip': `${day} ${HM.hours[bi]}: ${v}${U().people}` }, v || ''); })]));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.reaction, title: RE.title, sub: RE.sub, note: RE.note, chart: () => bars(reRows, { max: 100, lw: '220px' }), table: () => table(['', U().pct], reRows.map((x) => [x.label, x.text]), [1]) }),
      card({ no: num(), insight: IN.heatmap, title: HM.title, sub: HM.sub, note: HM.note, chart: heat, table: () => table([''].concat(HM.hours), HM.days.map((day, di) => [day].concat(d.heatmap[di])), [1, 2, 3, 4, 5, 6]) })));
    const NK = T.nickname;
    const nkRows = Object.entries(NK.labels).map(([k, l]) => ({ label: l, value: d.nickname[k] || 0, text: pctT(d.nickname[k]) }));
    const BL = T.blocked;
    const blRows = d.blocked.map((x) => ({ label: screenLabel(x.screen), value: x.per_leader || 0, text: n(x.per_leader, U().times) }));
    out.push(h('div', { class: 'grid2' },
      card({ no: num(), insight: IN.nickname, title: NK.title, sub: NK.sub, note: NK.note, chart: () => h('div', { style: 'display:flex;flex-direction:column;gap:12px' }, bars(nkRows, { max: 100 }), kpis([[NK.regen[0], n(d.nickname.regen_median, U().times), NK.regen[1]]])),
        table: () => table(['', U().pct], nkRows.map((x) => [x.label, x.text]).concat([[NK.regen[0], n(d.nickname.regen_median, U().times)]]), [1]) }),
      card({ no: num(), insight: IN.blocked, title: BL.title, sub: BL.sub, note: BL.note, chart: () => bars(blRows), table: () => table(['', U().times], blRows.map((x) => [x.label, x.text]), [1]) })));
    const ER = T.errors;
    const erRows = Object.entries(ER.labels).map(([k, l]) => ({ label: l, value: d.errors.counts[k] || 0, text: n(d.errors.counts[k], U().count) }));
    out.push(card({ no: num(), insight: IN.errors, title: ER.title, sub: ER.sub, span: true, chart: () => bars(erRows, { lw: '300px', color: 'var(--cmp)' }),
      table: () => table([ER.cols.kind, ER.cols.device, ER.cols.browser, ER.cols.count], d.errors.table.map((x) => [ER.labels[x.kind] || x.kind, x.device || D(), x.browser || D(), x.count]), [3]) }));
    out.push(h('p', { class: 'note' }, T.notice));
    return out;
  }

  // ----------------------------------------------------------------------- //
  // 설정 (전체 권한만)
  // ----------------------------------------------------------------------- //
  async function settings() {
    const T = A.settings;
    if (ME.role !== 'full') return [h('p', { class: 'muted' }, T.readonly)];
    const s = await get('/api/admin/settings');
    const field = (key, type, attrs) => {
      const input = h('input', Object.assign({ id: `s-${key}`, type, value: s[key] === null || s[key] === undefined ? '' : (key === 'deadline' ? String(s[key]).slice(0, 10) : s[key]) }, attrs || {}));
      return { key, input, row: [h('label', { class: 'lab', for: `s-${key}` }, T.fields[key][0]), h('div', { class: 'field' }, input, h('span', { class: 'note' }, T.fields[key][1]))] };
    };
    const fs = [field('cycle_name', 'text', { maxlength: 60 }), field('cycle_id', 'text', { maxlength: 40 }), field('deadline', 'date'),
      field('target_count', 'number', { min: 1, max: 10000 }), field('duration_min', 'number', { min: 1, max: 120 }),
      field('dwell_cutoff_sec', 'number', { min: 30, max: 3600 }), field('high_threshold', 'number', { min: 0.25, max: 1, step: 0.05 })];
    const err = h('p', { class: 'err' });
    const save = h('button', { type: 'button', class: 'btn primary' }, T.save);
    save.addEventListener('click', async () => {
      const body = { clear: [] };
      for (const f of fs) {
        const v = f.input.value.trim();
        if (v === '') { if (f.key === 'deadline' || f.key === 'duration_min') body.clear.push(f.key); continue; }
        body[f.key] = f.input.type === 'number' ? Number(v) : v;
      }
      if (body.cycle_id === s.cycle_id) delete body.cycle_id;
      const r = await api('PUT', '/api/admin/settings', body);
      if (!r.ok) { err.textContent = T.errors[r.data.error] || T.errors.generic; return; }
      err.textContent = ''; toast(T.saved);
      const me = await api('GET', '/api/admin/me'); if (me.ok) ME = me.data;
      render();
    });
    // 입장 키
    const E = T.entry;
    // 입장 키 없이 들어오기를 켜 두면 안내 링크는 기본 주소(2026-11-27)
    const link = s.open_entry ? `${location.origin}/` : (s.entry_key ? `${location.origin}/?k=${encodeURIComponent(s.entry_key)}` : null);
    const openBox = h('input', { type: 'checkbox', id: 's-open', checked: !!s.open_entry, style: 'width:18px;height:18px;padding:0;margin:0;flex:none' });
    const openErr = h('p', { class: 'err' });
    const saveOpen = h('button', { type: 'button', class: 'btn sm' }, T.save);
    saveOpen.addEventListener('click', async () => {
      if (openBox.checked === !!s.open_entry) return;
      if (!window.confirm(openBox.checked ? E.open_confirm_on : E.open_confirm_off)) { openBox.checked = !!s.open_entry; return; }
      const r = await api('PUT', '/api/admin/settings', { open_entry: openBox.checked });
      if (!r.ok) { openErr.textContent = T.errors[r.data.error] || T.errors.generic; return; }
      toast(T.saved); render();
    });
    const newKey = h('input', { type: 'text', id: 's-key', maxlength: 64, autocomplete: 'off', style: 'width:320px' });
    const keyErr = h('p', { class: 'err' });
    const genKey = () => { const b = crypto.getRandomValues(new Uint8Array(12)); newKey.value = btoa(String.fromCharCode(...b)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, ''); };
    const saveKey = h('button', { type: 'button', class: 'btn sm' }, T.save);
    saveKey.addEventListener('click', async () => {
      if (!newKey.value.trim() || !window.confirm(E.confirm)) return;
      const r = await api('PUT', '/api/admin/settings', { entry_key: newKey.value.trim() });
      if (!r.ok) { keyErr.textContent = T.errors[r.data.error] || T.errors.generic; return; }
      toast(T.saved); render();
    });
    // 비밀 관리자 링크(2026-11-27)
    const LK = T.admin_link;
    const adminLink = s.admin_link_key ? `${location.origin}/admin?a=${encodeURIComponent(s.admin_link_key)}` : null;
    const linkMsg = h('p', { class: 'note' });
    const linkAction = async (action) => {
      if (!window.confirm(action === 'rotate' ? LK.confirm_rotate : LK.confirm_off)) return;
      const r = await api('POST', '/api/admin/link', { action });
      if (!r.ok) { linkMsg.textContent = T.errors.generic; return; }
      if (r.data.admin_link_key) store.set(LINK_STORE, r.data.admin_link_key); else store.del(LINK_STORE);
      toast(T.saved); render();
    };
    const linkCard = h('section', { class: 'card' }, h('div', { class: 'card-h' }, h('h3', null, LK.title)), h('p', { class: 'note' }, LK.help),
      h('div', { class: 'form' },
        h('span', { class: 'lab' }, LK.current), h('div', { class: 'field' }, adminLink ? h('span', { class: 'code' }, adminLink) : h('span', null, LK.none),
          h('span', { class: 'note' }, LK.source[s.admin_link_source])),
        h('span', { class: 'lab' }, ''), h('div', { class: 'field' }, h('div', { style: 'display:flex;gap:8px;align-items:center' },
          h('button', { type: 'button', class: 'btn sm', onclick: () => linkAction('rotate') }, adminLink ? LK.rotate : LK.create),
          s.admin_link_source === 'settings' ? h('button', { type: 'button', class: 'btn sm', onclick: () => linkAction('off') }, LK.off) : null), linkMsg)));
    // 관리자 암호 바꾸기(2026-11-27): 지금 전체 권한 암호를 한 번 더 확인. 새 암호는 서버에 해시로만 저장
    const PW = T.admin_password;
    const pwInput = (id, label, auto) => {
      const i = h('input', { type: 'password', id, autocomplete: auto, maxlength: 200, style: 'width:280px' });
      return { i, row: [h('label', { class: 'lab', for: id }, label), h('div', { class: 'field' }, i)] };
    };
    const pwCurrent = pwInput('pw-current', PW.current, 'current-password');
    const pwRole = h('select', { id: 'pw-role', class: 'btn', style: 'width:280px' },
      h('option', { value: 'full' }, PW.roles.full), h('option', { value: 'readonly' }, PW.roles.readonly));
    const pwNew = pwInput('pw-new', PW.new, 'new-password');
    const pwNew2 = pwInput('pw-new2', PW.new2, 'new-password');
    const pwMsg = h('p', { class: 'err', role: 'alert' });
    const pwSave = h('button', { type: 'button', class: 'btn sm', style: 'align-self:flex-start' }, PW.submit);
    pwSave.addEventListener('click', async () => {
      pwMsg.textContent = '';
      if (pwNew.i.value !== pwNew2.i.value) { pwMsg.textContent = PW.errors.mismatch; return; }
      if (!window.confirm(fmt(PW.confirm, { role: PW.roles[pwRole.value] }))) return;
      const r = await api('POST', '/api/admin/password', { current: pwCurrent.i.value, role: pwRole.value, new: pwNew.i.value });
      if (!r.ok) {
        pwMsg.textContent = r.status === 423 ? PW.errors.locked : (PW.errors[r.data.error] || T.errors.generic);
        return;
      }
      [pwCurrent.i, pwNew.i, pwNew2.i].forEach((x) => { x.value = ''; });
      toast(fmt(PW.done, { role: PW.roles[r.data.role] })); render();
    });
    const srcText = (role) => `${PW.roles[role]}: ${PW.source[(s.admin_password_source || {})[role] || 'env']}`;
    const pwCard = h('section', { class: 'card' }, h('div', { class: 'card-h' }, h('h3', null, PW.title), h('span', { class: 'small muted' }, `${srcText('full')} · ${srcText('readonly')}`)),
      h('p', { class: 'note' }, PW.help),
      h('div', { class: 'form' }, pwCurrent.row, h('label', { class: 'lab', for: 'pw-role' }, PW.role), h('div', { class: 'field' }, pwRole), pwNew.row, pwNew2.row,
        h('span', { class: 'lab' }, ''), h('div', { class: 'field' }, pwSave, pwMsg)));
    // 참여자 삭제
    const P = T.delete_participant;
    const pName = h('input', { type: 'text', id: 'd-name', maxlength: 60, style: 'width:240px' });
    const pSerial = h('input', { type: 'text', id: 'd-serial', maxlength: 4, inputmode: 'numeric', style: 'width:100px' });
    const pMsg = h('p', { class: 'note' });
    const pBtn = h('button', { type: 'button', class: 'btn sm danger' }, P.submit);
    pBtn.addEventListener('click', async () => {
      const name = pName.value.trim(); const serial = pSerial.value.trim().replace(/^#/, '');
      if (!name || !/^\d{4}$/.test(serial) || !window.confirm(fmt(P.confirm, { name, serial }))) return;
      const r = await api('POST', '/api/admin/participants/delete', { leader_name: name, serial });
      pMsg.textContent = r.ok ? P.done : (r.status === 404 ? P.not_found : T.errors.generic);
      if (r.ok) { pName.value = ''; pSerial.value = ''; }
    });
    // 데이터 삭제
    const DD = T.delete_data;
    const dConfirm = h('input', { type: 'text', id: 'd-confirm', maxlength: 60, style: 'width:240px', autocomplete: 'off' });
    const dMsg = h('p', { class: 'note' });
    const dBtn = h('button', { type: 'button', class: 'btn sm danger' }, DD.submit);
    dBtn.addEventListener('click', async () => {
      if (dConfirm.value.trim() !== s.cycle_id) { dMsg.textContent = DD.mismatch; return; }
      if (!window.confirm(fmt(DD.confirm, { cycle_id: s.cycle_id }))) return;
      const r = await api('POST', '/api/admin/data/delete', { confirm: dConfirm.value.trim() });
      dMsg.textContent = r.ok ? fmt(DD.done, { n: r.data.deleted_participants }) : (r.data.error === 'confirm_mismatch' ? DD.mismatch : T.errors.generic);
    });
    // 판정 기준값 미리보기(2026-11-27): 지금까지의 응답을 기준값별로 다시 판정한 4분면 분포
    const TP = T.threshold_preview;
    const pv = await get(`/api/admin/threshold-preview${F.cycle ? `?cycle=${encodeURIComponent(F.cycle)}` : ''}`);
    const pvTable = pv.leaders
      ? table([TP.cols.threshold].concat(QUADS.map((q) => C.quadrants[q].label), [TP.cols.leaders]),
        pv.rows.map((r) => [h('span', null, Number.isInteger(r.threshold) ? r.threshold.toFixed(1) : String(r.threshold), r.current ? h('b', null, ` ${TP.current}`) : null, r.skewed ? h('span', { class: 'note' }, ` ${TP.skewed}`) : null)]
          .concat(QUADS.map((q) => pctT(r.cells[q])), [r.leaders])), [1, 2, 3, 4, 5])
      : h('p', { class: 'empty' }, A.app.empty);
    return [
      h('section', { class: 'card' }, h('div', { class: 'card-h' }, h('h3', null, T.title)),
        h('div', { class: 'form' }, fs.map((f) => f.row)), h('div', null, save, err)),
      h('section', { class: 'card' }, h('div', { class: 'card-h' }, h('h3', null, TP.title), h('span', { class: 'small muted' }, fmt(TP.sub, { n: pv.leaders }))),
        pvTable, h('p', { class: 'note' }, fmt(TP.note, { pct: pv.skew_pct }))),
      h('section', { class: 'card' }, h('div', { class: 'card-h' }, h('h3', null, E.title)),
        h('div', { class: 'form' },
          h('label', { class: 'lab', for: 's-open' }, E.open_label), h('div', { class: 'field' },
            h('div', { style: 'display:flex;gap:8px;align-items:center' }, openBox, h('span', null, s.open_entry ? E.open_on : E.open_off), saveOpen),
            h('span', { class: 'note' }, `${E.open_help} (${E.open_source[s.open_entry_source]})`), openErr),
          h('span', { class: 'lab' }, E.current), h('div', { class: 'field' }, s.entry_key ? h('span', { class: 'code' }, s.entry_key) : null, h('span', { class: 'note' }, E.source[s.entry_key_source])),
          h('span', { class: 'lab' }, E.link), h('div', { class: 'field' }, link ? h('span', { class: 'code' }, link) : D()),
          h('label', { class: 'lab', for: 's-key' }, E.new), h('div', { class: 'field' }, h('div', { style: 'display:flex;gap:8px;align-items:center' }, newKey, h('button', { type: 'button', class: 'btn sm', onclick: genKey }, E.generate), saveKey), h('span', { class: 'note' }, E.help), keyErr))),
      linkCard, pwCard,
      h('section', { class: 'card' }, h('div', { class: 'card-h' }, h('h3', null, P.title)), h('p', { class: 'note' }, P.help),
        h('div', { style: 'display:flex;gap:8px;align-items:center' }, h('label', { for: 'd-name' }, P.name), pName, h('label', { for: 'd-serial' }, P.serial), pSerial, pBtn), pMsg),
      h('section', { class: 'card' }, h('div', { class: 'card-h' }, h('h3', null, DD.title)), h('p', { class: 'note' }, fmt(DD.help, { cycle_id: s.cycle_id })),
        h('div', { style: 'display:flex;gap:8px;align-items:center' }, h('label', { for: 'd-confirm' }, DD.confirm_label), dConfirm, dBtn), dMsg),
    ];
  }

  // ----------------------------------------------------------------------- //
  // 시작
  // ----------------------------------------------------------------------- //
  function readHash() {
    const t = location.hash.replace('#', '');
    tab = ['dashboard', 'items', 'leaders', 'flow', 'settings'].includes(t) ? t : 'dashboard';
    if (tab === 'settings' && ME && ME.role !== 'full') tab = 'dashboard';
  }
  window.addEventListener('hashchange', () => { if (!ME) return; readHash(); render(); });

  async function boot() {
    const me = await api('GET', '/api/admin/me');
    if (!me.ok) { if (me.status !== 401) showLogin(); return; }
    ME = me.data;
    const t = await api('GET', '/api/admin/content');
    if (!t.ok) return;
    A = t.data.admin; C = t.data.content;
    readHash();
    render();
  }
  boot();
})();
