// ==UserScript==
// @name         네이버 지도 검색결과 저장
// @namespace    https://github.com/jdevjinwoo-design/modules
// @version      1.0.0
// @description  네이버 지도 검색결과(빨간 마커 + 작은 빨간 점 전체)를 CSV로 저장하거나 구글 스프레드시트에 추가
// @match        https://map.naver.com/*
// @match        https://pcmap.place.naver.com/*
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_registerMenuCommand
// @connect      script.google.com
// @connect      script.googleusercontent.com
// @run-at       document-idle
// ==/UserScript==

/*
 * 동작 방식
 *  - map.naver.com (지도 화면): "검색결과 저장" 버튼을 띄운다.
 *  - pcmap.place.naver.com (왼쪽 검색결과 iframe): 버튼이 요청하면
 *    현재 검색 조건(query, bounds ...)으로 /…/list 페이지를 start=1, 71, 141 … 순서로 다시 받아서
 *    HTML 안의 window.__APOLLO_STATE__ JSON을 파싱해 전체 결과를 모은다.
 *    (지도에 찍힌 큰 빨간 마커와 작은 빨간 점은 모두 이 검색결과 목록에서 나온 것)
 *  - 두 프레임은 origin이 달라서 postMessage로 주고받는다.
 */

(function () {
  'use strict';

  const MAP_ORIGIN = 'https://map.naver.com';
  const PLACE_ORIGIN = 'https://pcmap.place.naver.com';
  const PAGE_SIZE = 70;       // 네이버가 한 번에 주는 개수 (display=70)
  const MAX_PAGES = 10;       // 안전장치: 최대 700건
  const PAGE_DELAY_MS = 800;  // 페이지 사이 간격 (너무 빠르면 캡차가 뜰 수 있음)

  // 저장할 컬럼: [row 키, 헤더 이름]
  const COLUMNS = [
    ['rank', '순번'],
    ['id', '플레이스ID'],
    ['name', '상호명'],
    ['category', '카테고리'],
    ['roadAddress', '도로명주소'],
    ['jibunAddress', '지번주소'],
    ['region', '지역'],
    ['phone', '전화번호'],
    ['x', '경도'],
    ['y', '위도'],
    ['status', '영업상태'],
    ['hours', '영업시간안내'],
    ['visitorReviewCount', '방문자리뷰수'],
    ['visitorReviewScore', '방문자평점'],
    ['blogCafeReviewCount', '블로그카페리뷰수'],
    ['isDelivery', '배달'],
    ['hasBooking', '예약'],
    ['placeUrl', '플레이스URL'],
    ['imageUrl', '대표이미지'],
    ['query', '검색어'],
    ['collectedAt', '수집시각'],
  ];

  if (location.origin === PLACE_ORIGIN) {
    runInPlaceFrame();
  } else if (location.origin === MAP_ORIGIN && window.top === window.self) {
    runInMapFrame();
  }

  // ───────────────────────────── 검색결과 iframe 쪽 ─────────────────────────────

  function runInPlaceFrame() {
    window.addEventListener('message', async (ev) => {
      if (ev.origin !== MAP_ORIGIN) return;
      const msg = ev.data;
      if (!msg || msg.type !== 'NMS_COLLECT') return;

      const reply = (data) => ev.source.postMessage({ ...data, reqId: msg.reqId }, ev.origin);
      try {
        if (!/\/list\/?$/.test(location.pathname)) {
          throw new Error('검색결과 목록 화면이 아닙니다. 먼저 검색해 주세요.');
        }
        const result = await collectAll((p) => reply({ type: 'NMS_PROGRESS', ...p }));
        reply({ type: 'NMS_RESULT', ...result });
      } catch (e) {
        reply({ type: 'NMS_ERROR', message: String((e && e.message) || e) });
      }
    });
  }

  // URL에 같은 키가 두 번 들어오는 경우가 있는데, 서버는 마지막 값을 쓴다.
  function currentParams() {
    const src = new URLSearchParams(location.search);
    const out = new URLSearchParams();
    for (const key of new Set(src.keys())) {
      const vals = src.getAll(key);
      out.set(key, vals[vals.length - 1]);
    }
    return out;
  }

  async function collectAll(onProgress) {
    const base = currentParams();
    const query = base.get('query') || '';
    const collectedAt = new Date().toISOString();
    const rows = [];
    const seen = new Set();
    let total = null;
    let pagingUnsupported = false;

    for (let page = 0; page < MAX_PAGES; page++) {
      const start = page * PAGE_SIZE + 1;
      const params = new URLSearchParams(base);
      params.set('display', String(PAGE_SIZE));
      params.set('start', String(start));
      params.set('ts', String(Date.now()));

      const res = await fetch(`${location.origin}${location.pathname}?${params}`, { credentials: 'include' });
      if (!res.ok) throw new Error(`요청 실패: HTTP ${res.status}`);
      const parsed = parseList(await res.text());
      if (!parsed) {
        throw new Error('응답에서 검색결과를 찾지 못했습니다. (캡차가 떴거나 페이지 구조가 바뀌었을 수 있음)');
      }
      total = parsed.total;

      // 서버가 start 파라미터를 무시하고 1페이지를 또 주면 중단
      if (page > 0 && parsed.start !== undefined && parsed.start !== start) {
        pagingUnsupported = true;
        break;
      }

      let added = 0;
      for (const item of parsed.items) {
        if (!item.id || seen.has(item.id)) continue;
        seen.add(item.id);
        rows.push(toRow(item, rows.length + 1, query, collectedAt));
        added++;
      }
      onProgress({ count: rows.length, total });

      if (added === 0) {
        if (page > 0) pagingUnsupported = rows.length < total;
        break;
      }
      if (rows.length >= total || parsed.items.length < PAGE_SIZE) break;
      await sleep(PAGE_DELAY_MS);
    }

    return { rows, total, query, bounds: base.get('bounds'), pagingUnsupported };
  }

  function parseList(html) {
    const m = html.match(/window\.__APOLLO_STATE__\s*=\s*(\{[\s\S]*?\});\s*window\.__PLACE_STATE__/);
    if (!m) return null;
    const state = JSON.parse(m[1]);
    const root = state.ROOT_QUERY || {};

    // placeList(...) / restaurantList(...) 등 업종에 따라 키 이름이 다를 수 있음
    const key = Object.keys(root).find((k) => /^[A-Za-z]+List\(/.test(k) && root[k] && root[k].businesses);
    if (!key) return null;

    let start;
    try {
      start = JSON.parse(key.slice(key.indexOf('(') + 1, -1)).input.start;
    } catch (_) { /* 시작 위치를 못 읽으면 중복 검사로만 판단 */ }

    const biz = root[key].businesses;
    const items = (biz.items || [])
      .map((r) => (r && r.__ref ? state[r.__ref] : r))
      .filter(Boolean);
    return { total: Number(biz.total) || items.length, start, items };
  }

  function toRow(it, rank, query, collectedAt) {
    const hours = it.newBusinessHours || {};
    const num = (v) => (v == null ? '' : String(v).replace(/,/g, ''));
    return {
      rank,
      id: it.id,
      name: it.name || '',
      category: it.category || '',
      roadAddress: it.fullAddress || it.roadAddress || '',
      jibunAddress: it.address || '',
      region: it.commonAddress || '',
      phone: it.virtualPhone || it.phone || '',
      x: it.x || '',
      y: it.y || '',
      status: hours.status || '',
      hours: hours.description || '',
      visitorReviewCount: num(it.visitorReviewCount),
      visitorReviewScore: it.visitorReviewScore || '',
      blogCafeReviewCount: num(it.blogCafeReviewCount),
      isDelivery: it.isDelivery ? 'Y' : '',
      hasBooking: it.hasBooking ? 'Y' : '',
      placeUrl: `https://map.naver.com/p/entry/place/${it.id}`,
      imageUrl: it.imageUrl || '',
      query,
      collectedAt,
    };
  }

  // ───────────────────────────── 지도(최상위) 쪽 ─────────────────────────────

  function runInMapFrame() {
    GM_registerMenuCommand('스프레드시트 연결 설정', configureSheet);

    const style = document.createElement('style');
    style.textContent = `
      #nms-box{position:fixed;top:14px;left:50%;transform:translateX(-50%);z-index:2147483000;
        font:13px/1.4 -apple-system,BlinkMacSystemFont,"Apple SD Gothic Neo","Malgun Gothic",sans-serif;
        display:flex;flex-direction:column;align-items:center;gap:6px}
      #nms-box button{border:0;border-radius:18px;padding:8px 16px;cursor:pointer;font:inherit;font-weight:600;
        box-shadow:0 2px 6px rgba(0,0,0,.25)}
      #nms-main{background:#03c75a;color:#fff}
      #nms-main:disabled{background:#9aa;cursor:wait}
      #nms-menu{display:flex;gap:6px}
      #nms-menu[hidden]{display:none}
      #nms-menu button{background:#fff;color:#222}
      #nms-menu button:hover{background:#f0f0f0}
      #nms-status{background:rgba(0,0,0,.75);color:#fff;border-radius:10px;padding:6px 12px;max-width:420px;
        white-space:pre-line;text-align:center}
      #nms-status:empty{display:none}
    `;
    document.head.appendChild(style);

    const box = document.createElement('div');
    box.id = 'nms-box';
    box.innerHTML = `
      <button id="nms-main" type="button">검색결과 저장</button>
      <div id="nms-menu" hidden>
        <button type="button" data-act="csv">CSV 다운로드</button>
        <button type="button" data-act="sheet">스프레드시트에 추가</button>
        <button type="button" data-act="both">둘 다</button>
      </div>
      <div id="nms-status"></div>`;
    document.body.appendChild(box);

    const mainBtn = box.querySelector('#nms-main');
    const menu = box.querySelector('#nms-menu');
    const status = box.querySelector('#nms-status');
    let statusTimer;
    const setStatus = (text, autoHideMs) => {
      clearTimeout(statusTimer);
      status.textContent = text;
      if (autoHideMs) statusTimer = setTimeout(() => { status.textContent = ''; }, autoHideMs);
    };

    mainBtn.addEventListener('click', () => { menu.hidden = !menu.hidden; });

    menu.addEventListener('click', async (ev) => {
      const act = ev.target && ev.target.dataset && ev.target.dataset.act;
      if (!act) return;
      menu.hidden = true;

      if ((act === 'sheet' || act === 'both') && !GM_getValue('webAppUrl')) {
        if (!configureSheet()) return;
      }

      mainBtn.disabled = true;
      try {
        setStatus('검색결과 수집 중…');
        const result = await requestCollect((p) => setStatus(`수집 중… ${p.count} / ${p.total}건`));
        if (!result.rows.length) throw new Error('저장할 검색결과가 없습니다.');

        const msgs = [`"${result.query}" ${result.rows.length}건 수집 (전체 ${result.total}건)`];
        if (result.pagingUnsupported) msgs.push('⚠ 다음 페이지를 못 받아 일부만 저장했습니다.');

        if (act === 'csv' || act === 'both') {
          downloadCSV(result);
          msgs.push('CSV 다운로드 완료');
        }
        if (act === 'sheet' || act === 'both') {
          setStatus(msgs.concat('스프레드시트 전송 중…').join('\n'));
          const r = await appendToSheet(result.rows);
          msgs.push(`스프레드시트: ${r.appended}건 추가` + (r.skipped ? `, 중복 ${r.skipped}건 건너뜀` : ''));
        }
        setStatus(msgs.join('\n'), 8000);
      } catch (e) {
        setStatus('오류: ' + ((e && e.message) || e), 10000);
      } finally {
        mainBtn.disabled = false;
      }
    });
  }

  function findSearchFrame() {
    return (
      document.querySelector('iframe#searchIframe') ||
      [...document.querySelectorAll('iframe')].find((f) => /pcmap\.place\.naver\.com\/.*\/list/.test(f.src || ''))
    );
  }

  function requestCollect(onProgress) {
    return new Promise((resolve, reject) => {
      const frame = findSearchFrame();
      if (!frame || !frame.contentWindow) {
        reject(new Error('검색결과 패널을 찾지 못했습니다. 먼저 검색해 주세요.'));
        return;
      }
      const reqId = Math.random().toString(36).slice(2);
      let timer;
      const arm = () => {
        clearTimeout(timer);
        timer = setTimeout(() => {
          cleanup();
          reject(new Error('응답이 없습니다. 검색결과 패널에서도 이 스크립트가 실행 중인지 확인해 주세요.'));
        }, 30000);
      };
      const onMsg = (ev) => {
        if (ev.origin !== PLACE_ORIGIN || !ev.data || ev.data.reqId !== reqId) return;
        const d = ev.data;
        if (d.type === 'NMS_PROGRESS') { arm(); onProgress(d); return; }
        cleanup();
        if (d.type === 'NMS_RESULT') resolve(d);
        else reject(new Error(d.message || '알 수 없는 오류'));
      };
      const cleanup = () => { clearTimeout(timer); window.removeEventListener('message', onMsg); };

      window.addEventListener('message', onMsg);
      arm();
      frame.contentWindow.postMessage({ type: 'NMS_COLLECT', reqId }, PLACE_ORIGIN);
    });
  }

  // ───────────────────────────── CSV ─────────────────────────────

  function csvCell(v) {
    let s = v == null ? '' : String(v);
    if (/^[=+@]/.test(s)) s = "'" + s; // 엑셀 수식으로 해석되는 것 방지
    return /[",\r\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  }

  function downloadCSV({ rows, query }) {
    const lines = [COLUMNS.map((c) => csvCell(c[1])).join(',')];
    for (const r of rows) lines.push(COLUMNS.map((c) => csvCell(r[c[0]])).join(','));
    // BOM을 붙여야 엑셀에서 한글이 안 깨짐
    const blob = new Blob(['﻿' + lines.join('\r\n')], { type: 'text/csv;charset=utf-8' });

    const d = new Date();
    const pad = (n) => String(n).padStart(2, '0');
    const stamp = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}_${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
    const safeQuery = (query || 'search').replace(/[\\/:*?"<>|\s]+/g, '_');

    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = `naver_${safeQuery}_${stamp}.csv`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 10000);
  }

  // ───────────────────────────── 구글 스프레드시트 ─────────────────────────────

  function configureSheet() {
    const url = prompt(
      'Apps Script 웹 앱 URL을 입력하세요 (https://script.google.com/macros/s/…/exec)',
      GM_getValue('webAppUrl', '')
    );
    if (url === null) return false;
    if (!/^https:\/\/script\.google\.com\/macros\/s\/[^/]+\/exec/.test(url.trim())) {
      alert('URL 형식이 올바르지 않습니다. 배포 후 받은 /exec 로 끝나는 URL을 넣어 주세요.');
      return false;
    }
    const token = prompt('Apps Script 코드에 넣은 TOKEN 값을 입력하세요', GM_getValue('token', ''));
    if (token === null) return false;
    GM_setValue('webAppUrl', url.trim());
    GM_setValue('token', token.trim());
    return true;
  }

  function appendToSheet(rows) {
    const payload = {
      token: GM_getValue('token', ''),
      headers: COLUMNS.map((c) => c[1]),
      rows: rows.map((r) => COLUMNS.map((c) => (r[c[0]] == null ? '' : r[c[0]]))),
      dedupe: true,
    };
    return new Promise((resolve, reject) => {
      GM_xmlhttpRequest({
        method: 'POST',
        url: GM_getValue('webAppUrl'),
        headers: { 'Content-Type': 'text/plain;charset=utf-8' },
        data: JSON.stringify(payload),
        timeout: 60000,
        onload: (res) => {
          let body;
          try { body = JSON.parse(res.responseText); } catch (_) {
            reject(new Error(`스프레드시트 응답을 해석하지 못했습니다 (HTTP ${res.status}). 웹 앱 액세스 권한이 "모든 사용자"인지 확인하세요.`));
            return;
          }
          if (body.ok) resolve(body);
          else reject(new Error('스프레드시트: ' + (body.error || '실패')));
        },
        onerror: () => reject(new Error('스프레드시트 요청 실패 (네트워크 오류)')),
        ontimeout: () => reject(new Error('스프레드시트 요청 시간 초과')),
      });
    });
  }

  function sleep(ms) {
    return new Promise((r) => setTimeout(r, ms));
  }
})();
