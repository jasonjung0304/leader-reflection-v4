/* 결과 PDF — 브라우저에서만 만든다(SPEC 2장·7장). 서버에 결과를 보내지 않는다.
   외부 라이브러리 없이: 캔버스에 그리기(기기 한글 글꼴 사용) → JPEG → A4 PDF(이미지 페이지).
   내용: 리더명, 별명, 결과 1~3, 꼭 해야 하는 말. 판단 근거(축별 실행 수)는 넣지 않는다. */
'use strict';
window.ResultPdf = (() => {
  const PW = 595.28;           // A4 (pt)
  const PH = 841.89;
  const K = 2.5;               // 캔버스 배율(pt → px)
  const M = 44;                // 여백
  // 한 쪽에 들어가지 않으면 전체를 조금 줄여(ZOOM) 다시 그린다. 아래 값은 줄인 좌표계 기준
  let ZOOM = 1;
  let VW = PW;                 // 가상 쪽 폭
  let VH = PH;                 // 가상 쪽 높이
  let CW = VW - M * 2;         // 본문 폭
  let BOTTOM = VH - M - 4;     // 본문 끝(바닥글은 아래 여백 안)
  function setZoom(z) { ZOOM = z; VW = PW / z; VH = PH / z; CW = VW - M * 2; BOTTOM = VH - M - 4; }
  const FONT = '"Noto Sans KR","Malgun Gothic","Apple SD Gothic Neo","Noto Sans CJK KR",system-ui,sans-serif';
  const COL = {
    ink: '#000000', ink2: '#444444', ink3: '#676767', ink4: '#8f8f8f',
    border: '#d2d2d2', subtle: '#f7f7f7', nickBg: '#E3F3EA', nickInk: '#00703C', leaderBg: '#f0f0f0',
  };

  // ---------------------------------------------------------------------- //
  // 문구 → 글자 묶음(runs). [별명]·[리더명] 은 토큰, \n 은 줄바꿈, {x} 는 값
  // ---------------------------------------------------------------------- //
  function fmt(s, vars) { return String(s).replace(/\{(\w+)\}/g, (m, k) => (vars && k in vars ? String(vars[k]) : m)); }
  function toRuns(text, ctx, vars) {
    const out = [];
    for (const part of String(text || '').split(/(\[별명\]|\[리더명\])/)) {
      if (part === '[별명]') out.push({ text: ctx.nick || '○○', kind: 'nick' });
      else if (part === '[리더명]') out.push({ text: ctx.leader, kind: 'leader' });
      else fmt(part, vars).split('\n').forEach((t, i) => { if (i) out.push({ br: true }); if (t) out.push({ text: t }); });
    }
    return out;
  }

  // ---------------------------------------------------------------------- //
  // 문서(페이지 = 캔버스)
  // ---------------------------------------------------------------------- //
  class Doc {
    constructor() { this.pages = []; this.newPage(); }
    newPage() {
      const c = document.createElement('canvas');
      c.width = Math.round(PW * K); c.height = Math.round(PH * K);
      const g = c.getContext('2d');
      g.scale(K * ZOOM, K * ZOOM);
      g.fillStyle = '#ffffff'; g.fillRect(0, 0, VW, VH);
      g.textBaseline = 'alphabetic';
      this.g = g; this.pages.push(c); this.y = M;
    }
    font(size, weight) { this.g.font = `${weight || 400} ${size}px ${FONT}`; }
    ensure(h) { if (this.y + h > BOTTOM && this.y > M) this.newPage(); }
    rect(x, y, w, h, r, fill, stroke) {
      const g = this.g;
      g.beginPath();
      g.moveTo(x + r, y); g.arcTo(x + w, y, x + w, y + h, r); g.arcTo(x + w, y + h, x, y + h, r);
      g.arcTo(x, y + h, x, y, r); g.arcTo(x, y, x + w, y, r); g.closePath();
      if (fill) { g.fillStyle = fill; g.fill(); }
      if (stroke) { g.strokeStyle = stroke; g.lineWidth = 1; g.stroke(); }
    }
  }

  const PILL = 4; // 토큰 좌우 여백
  /** 한국어 낱말(띄어쓰기) 단위로 줄을 나눈다. 토큰은 한 덩어리 */
  function wrap(doc, runs, size, weight, maxW) {
    const g = doc.g;
    const atoms = [];
    for (const r of runs) {
      if (r.br) { atoms.push({ br: true }); continue; }
      if (r.kind) { atoms.push({ text: r.text, kind: r.kind }); continue; }
      for (const w of r.text.match(/\S+\s*|\s+/g) || []) atoms.push({ text: w });
    }
    const width = (a) => {
      doc.font(size, a.kind ? 700 : weight);
      return g.measureText(a.kind ? a.text.trimEnd() : a.text).width + (a.kind ? PILL * 2 + 2 : 0);
    };
    const lines = [];
    let line = []; let w = 0;
    const flush = () => { lines.push(line); line = []; w = 0; };
    for (const a of atoms) {
      if (a.br) { flush(); continue; }
      let aw = width(a);
      if (w + aw > maxW && line.length) { flush(); if (!a.kind) { a.text = a.text.replace(/^\s+/, ''); aw = width(a); } }
      if (aw > maxW && !a.kind) {            // 너무 긴 낱말은 글자 단위로 자른다
        let buf = '';
        for (const ch of a.text) {
          const t = buf + ch;
          doc.font(size, weight);
          if (g.measureText(t).width > maxW - w && buf) { line.push({ text: buf, w: g.measureText(buf).width }); flush(); buf = ch; }
          else buf = t;
        }
        if (buf) { const part = { text: buf }; part.w = width(part); line.push(part); w += part.w; }
        continue;
      }
      a.w = aw; line.push(a); w += aw;
    }
    if (line.length) flush();
    return lines.map((ln) => { while (ln.length && !ln[ln.length - 1].kind && !ln[ln.length - 1].text.trim()) ln.pop(); return ln; });
  }

  /** 문단. 줄마다 쪽 넘김을 확인한다 */
  function para(doc, runs, o) {
    const size = o.size || 11; const weight = o.weight || 400; const lh = (o.lh || 1.6) * size;
    const x = o.x === undefined ? M : o.x; const maxW = o.maxW || CW;
    const lines = wrap(doc, runs, size, weight, maxW);
    for (const ln of lines) {
      doc.ensure(lh);
      const base = doc.y + size * 1.02 + (lh - size * 1.3) / 2;
      let cx = x;
      if (o.align === 'center') cx = x + (maxW - ln.reduce((s, a) => s + (a.w || 0), 0)) / 2;
      for (const a of ln) {
        const g = doc.g;
        if (a.kind) {
          doc.rect(cx, base - size * 1.02, a.w - 2, size * 1.42, 4, a.kind === 'nick' ? COL.nickBg : COL.leaderBg);
          doc.font(size, 700); g.fillStyle = a.kind === 'nick' ? COL.nickInk : COL.ink;
          g.fillText(a.text, cx + PILL, base);
        } else {
          doc.font(size, weight); g.fillStyle = o.color || COL.ink;
          g.fillText(a.text, cx, base);
        }
        cx += a.w;
      }
      doc.y += lh;
    }
    doc.y += o.after === undefined ? 0 : o.after;
    return lines.length * lh;
  }
  const text = (t) => [{ text: String(t || '') }];

  function heading(doc, label) {
    doc.ensure(56);
    doc.y += 10;
    doc.g.fillStyle = COL.border; doc.g.fillRect(M, doc.y, CW, 0.75);
    doc.y += 12;
    para(doc, text(label), { size: 9.5, weight: 700, color: COL.ink3, after: 6 });
  }

  function quadrant(doc, C, highlight) {
    const Q = C.quadrants;
    const gap = 5; const cw = (CW - gap) / 2; const ch = 34;
    doc.ensure(ch * 2 + gap + 34);
    para(doc, text(C.ui.axis_care), { size: 8.5, color: COL.ink3, after: 2 });
    ['re', 'rc', 'mi', 'oa'].forEach((k, i) => {
      const x = M + (i % 2) * (cw + gap); const y = doc.y + Math.floor(i / 2) * (ch + gap);
      const on = k === highlight;
      doc.rect(x, y, cw, ch, 6, on ? COL.ink : COL.subtle);
      doc.font(10, 700); doc.g.fillStyle = on ? '#ffffff' : COL.ink; doc.g.fillText(Q[k].label, x + 10, y + 15);
      doc.font(8.5, 400); doc.g.fillStyle = on ? COL.border : COL.ink3; doc.g.fillText(Q[k].sub, x + 10, y + 27);
    });
    doc.y += ch * 2 + gap + 2;
    doc.font(8.5, 400);
    const w = doc.g.measureText(C.ui.axis_challenge).width;
    doc.g.fillStyle = COL.ink3; doc.g.fillText(C.ui.axis_challenge, M + CW - w, doc.y + 10);
    doc.y += 16;
  }

  function recallRow(doc, label, badge) {
    const h = 24;
    doc.ensure(h + 5);
    doc.rect(M, doc.y, CW, h, 6, COL.subtle);
    doc.font(10, 700); doc.g.fillStyle = COL.ink; doc.g.fillText(label, M + 10, doc.y + 16);
    doc.font(8.5, 400);
    const bw = doc.g.measureText(badge).width + 16;
    doc.rect(M + CW - bw - 8, doc.y + 4, bw, 16, 8, '#ffffff', COL.border);
    doc.g.fillStyle = COL.ink2; doc.g.fillText(badge, M + CW - bw, doc.y + 15.5);
    doc.y += h + 5;
  }

  function phraseBox(doc, label, phrase) {
    const pad = 14;
    const lines = wrap(doc, text(phrase), 11.5, 400, CW - pad * 2);
    const h = pad * 2 + 14 + lines.length * 11.5 * 1.6;
    doc.ensure(h);
    doc.rect(M, doc.y, CW, h, 12, null, COL.border);
    const top = doc.y;
    doc.y += pad;
    para(doc, label, { size: 9, color: COL.ink3, x: M + pad, maxW: CW - pad * 2, after: 4 });
    para(doc, text(phrase), { size: 11.5, x: M + pad, maxW: CW - pad * 2 });
    doc.y = top + h;
  }

  // ---------------------------------------------------------------------- //
  // 결과 그리기
  // ---------------------------------------------------------------------- //
  function draw(d, zoom) {
    setZoom(zoom);
    const C = d.content; const R = C.results; const res = d.results;
    const items = Object.fromEntries(C.items.map((i) => [i.id, i]));
    const ctx = { nick: d.nick, leader: d.leader };
    const doc = new Doc();
    const P = C.done.pdf;

    para(doc, text(fmt(P.eyebrow, { cycle_name: d.cycleName || '' })), { size: 9.5, weight: 700, color: COL.ink3, after: 10 });

    // 결과 1 경향과 기억
    para(doc, toRuns(R.r1.overline + (d.situationLabel ? ` · ${d.situationLabel}` : ''), ctx), { size: 9.5, weight: 700, color: COL.ink3, after: 4 });
    para(doc, toRuns(R.r1.title, ctx, { quadrant_label: C.quadrants[d.quadrant].label }), { size: 18, weight: 700, lh: 1.45, after: 8 });
    para(doc, text(C.quadrants[d.quadrant].desc), { size: 10.5, color: COL.ink2, after: 8 });
    quadrant(doc, C, d.quadrant);
    for (const x of res.r1.signal_low || []) {   // 체크 5의 순간 때문에 '낮음'이 된 축(2026-11-27)
      para(doc, text(fmt(R.r1.signal_low, { axis: R.r1.axis_names[x.axis], done: x.done, total: x.total, signals: x.signals })),
        { size: 9.5, color: COL.ink2, lh: 1.5, after: 4 });
    }
    para(doc, text(R.r1.note), { size: 8.5, color: COL.ink3, after: 8 });
    para(doc, text(R.r1.recall_section), { size: 10, weight: 700, color: COL.ink3, after: 6 });
    if (res.r1.none) para(doc, text(R.r1.recall_none), { size: 10.5, color: COL.ink2 });
    else {
      const labels = C.answer_options.behavior;
      for (const k of res.r1.keywords) {
        const kw = C.keywords.items.find((x) => x.id === k.id);
        recallRow(doc, kw ? kw.label : k.id, fmt(R.r1.recall_status, { answer: k.did ? labels[0] : labels[1] }));
      }
    }

    // 결과 2 잘한 점
    heading(doc, R.r2.overline);
    if (res.r2.empty) para(doc, toRuns(R.r2.empty, ctx), { size: 10.5, color: COL.ink2 });
    else {
      para(doc, text(R.r2.summary[res.r2.summary]), { size: 13, weight: 700, after: 8 });
      const low2 = new Set(res.r2.low_axis || []);
      for (const id of res.r2.items) {
        const it = items[id];
        const cap = low2.has(id) ? `${fmt(R.r2.low_axis_cap, { axis: R.r1.axis_names[it.axis] })} · ${it.tool}` : C.ui.tool_prefix + it.tool;
        doc.ensure(52);
        para(doc, text(it.text), { size: 11, weight: 700, after: 1 });
        para(doc, text(cap), { size: 8.5, color: COL.ink3, after: 1 });
        para(doc, text(it.effect), { size: 10, color: COL.ink2, lh: 1.5, after: 6 });
      }
    }

    // 결과 3 다음에 해 볼 것
    heading(doc, R.r3.overline);
    const ids3 = res.r3.items || (res.r3.item ? [res.r3.item] : []);   // 늘 3개(SPEC 4.6)
    const keep3 = new Set(res.r3.keep || []);
    const lead3 = res.r3.empty_signals ? R.r3.empty_signals : (res.r3.empty ? R.r3.empty : null);
    if (lead3) para(doc, toRuns(lead3, ctx), { size: 10.5, color: COL.ink2, after: 6 });
    for (const id of ids3) {
      const it = items[id];
      const [cap, body] = keep3.has(id) ? [R.r3.keep_cap, it.effect]
        : it.type === 'signal' ? [R.r3.signal_cap, it.advice] : [C.ui.tool_prefix + it.tool, it.advice];
      doc.ensure(52);
      para(doc, toRuns(it.next_label.replace(/\n/g, ' '), ctx), { size: 11, weight: 700, after: 1 });
      para(doc, text(cap), { size: 8.5, color: COL.ink3, after: 1 });
      para(doc, text(body), { size: 10, color: COL.ink2, lh: 1.5, after: 6 });
    }

    // 꼭 해야 하는 말 (실천에서 고른 문장)
    if (d.phrase) {
      doc.y += 10;
      phraseBox(doc, toRuns(C.done.phrase_label.replace('\n', ' '), ctx), d.phrase);
    }

    // 바닥글(쪽마다)
    const footer = fmt(P.footer, { date: d.date, version: d.version || '' });
    doc.pages.forEach((c, i) => {
      const g = c.getContext('2d');
      g.font = `400 8px ${FONT}`; g.fillStyle = COL.ink4;
      g.fillText(doc.pages.length > 1 ? `${footer} · ${i + 1}/${doc.pages.length}` : footer, M, VH - M + 16);
    });
    return doc.pages;
  }

  // ---------------------------------------------------------------------- //
  // 캔버스 → JPEG → PDF (쪽마다 이미지 1장)
  // ---------------------------------------------------------------------- //
  function toJpeg(canvas) {
    return new Promise((resolve, reject) => {
      canvas.toBlob(async (b) => {
        if (!b) { reject(new Error('jpeg')); return; }
        resolve({ bytes: new Uint8Array(await b.arrayBuffer()), w: canvas.width, h: canvas.height });
      }, 'image/jpeg', 0.9);
    });
  }
  function utf16hex(s) {
    let out = 'FEFF';
    for (let i = 0; i < s.length; i++) out += s.charCodeAt(i).toString(16).toUpperCase().padStart(4, '0');
    return out;
  }
  function writePdf(jpegs, title) {
    const enc = new TextEncoder();
    const chunks = []; const offsets = []; let len = 0;
    const push = (d) => { const b = typeof d === 'string' ? enc.encode(d) : d; chunks.push(b); len += b.length; };
    const obj = (n, parts) => { offsets[n] = len; push(`${n} 0 obj\n`); [].concat(parts).forEach(push); push('\nendobj\n'); };
    push('%PDF-1.4\n');
    push(new Uint8Array([0x25, 0xe2, 0xe3, 0xcf, 0xd3, 0x0a]));
    const ids = jpegs.map((_, i) => 4 + i * 3);
    obj(1, '<< /Type /Catalog /Pages 2 0 R >>');
    obj(2, `<< /Type /Pages /Kids [${ids.map((id) => `${id} 0 R`).join(' ')}] /Count ${jpegs.length} >>`);
    obj(3, `<< /Title <${utf16hex(title)}> /Producer (leader-reflection) >>`);
    jpegs.forEach((j, i) => {
      const p = ids[i]; const c = p + 1; const im = p + 2;
      obj(p, `<< /Type /Page /Parent 2 0 R /MediaBox [0 0 ${PW} ${PH}] /Resources << /XObject << /Im0 ${im} 0 R >> >> /Contents ${c} 0 R >>`);
      const cs = `q ${PW} 0 0 ${PH} 0 0 cm /Im0 Do Q`;
      obj(c, [`<< /Length ${cs.length} >>\nstream\n`, cs, '\nendstream']);
      obj(im, [`<< /Type /XObject /Subtype /Image /Width ${j.w} /Height ${j.h} /ColorSpace /DeviceRGB /BitsPerComponent 8 /Filter /DCTDecode /Length ${j.bytes.length} >>\nstream\n`, j.bytes, '\nendstream']);
    });
    const size = 4 + jpegs.length * 3;
    const xref = len;
    push(`xref\n0 ${size}\n0000000000 65535 f \n`);
    for (let i = 1; i < size; i++) push(`${String(offsets[i]).padStart(10, '0')} 00000 n \n`);
    push(`trailer\n<< /Size ${size} /Root 1 0 R /Info 3 0 R >>\nstartxref\n${xref}\n%%EOF\n`);
    return new Blob(chunks, { type: 'application/pdf' });
  }

  /** data: {content, nick, leader, quadrant, results, phrase, situationLabel, cycleName, date, version} → Blob */
  async function build(data) {
    let pages = null;
    // 한 쪽에 들어가는 가장 큰 배율. 그래도 넘치면 2쪽 이상
    // (2026-11-27) 0.8·0.75 추가: 가장 긴 조합(리더명·별명 최장, 결과 3개씩, 직접 쓴 100자)도 1쪽에. 0.75 에서 본문 약 7.5pt
    for (const z of [1, 0.93, 0.86, 0.8, 0.75]) {
      pages = draw(data, z);
      if (pages.length === 1) break;
    }
    const jpegs = [];
    for (const c of pages) jpegs.push(await toJpeg(c));
    return writePdf(jpegs, data.content.done.pdf.title);
  }

  return { build };
})();
