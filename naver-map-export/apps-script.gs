/**
 * 네이버 지도 검색결과 저장 – 구글 스프레드시트 수신용 Apps Script
 *
 * 설치
 *  1. 저장할 구글 스프레드시트 열기 → 확장 프로그램 → Apps Script
 *  2. 이 코드를 붙여넣고 TOKEN 값을 아무도 모르는 긴 문자열로 바꾸기
 *  3. 배포 → 새 배포 → 유형: 웹 앱
 *       - 다음 사용자 인증 정보로 실행: 나
 *       - 액세스 권한이 있는 사용자: 모든 사용자
 *  4. 권한 승인 후 나오는 웹 앱 URL(…/exec)을 복사
 *  5. Tampermonkey 메뉴 → "스프레드시트 연결 설정"에 URL과 TOKEN 입력
 *
 * 코드를 수정하면 "배포 관리 → 수정 → 버전: 새 버전"으로 다시 배포해야 반영됩니다.
 */

const TOKEN = 'change-me-to-a-long-random-string';
const SHEET_NAME = '네이버지도';
const TEXT_COLUMNS = ['플레이스ID', '전화번호']; // 앞자리 0이 사라지지 않게 텍스트로 저장

function doPost(e) {
  let body;
  try {
    body = JSON.parse(e.postData.contents);
  } catch (err) {
    return json({ ok: false, error: 'invalid json' });
  }
  if (body.token !== TOKEN) return json({ ok: false, error: 'invalid token' });

  const headers = body.headers || [];
  let rows = body.rows || [];
  if (!headers.length) return json({ ok: false, error: 'no headers' });

  const lock = LockService.getScriptLock();
  lock.waitLock(30000);
  try {
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const sheet = ss.getSheetByName(SHEET_NAME) || ss.insertSheet(SHEET_NAME);

    if (sheet.getLastRow() === 0) {
      sheet.appendRow(headers);
      sheet.setFrozenRows(1);
    }

    const total = rows.length;
    const idIdx = headers.indexOf('플레이스ID');
    if (body.dedupe && idIdx >= 0 && sheet.getLastRow() > 1) {
      const existing = new Set(
        sheet.getRange(2, idIdx + 1, sheet.getLastRow() - 1, 1).getValues().map((r) => String(r[0]))
      );
      rows = rows.filter((r) => !existing.has(String(r[idIdx])));
    }

    if (rows.length) {
      const startRow = sheet.getLastRow() + 1;
      TEXT_COLUMNS.forEach((name) => {
        const idx = headers.indexOf(name);
        if (idx >= 0) sheet.getRange(startRow, idx + 1, rows.length, 1).setNumberFormat('@');
      });
      sheet.getRange(startRow, 1, rows.length, headers.length).setValues(rows);
    }

    return json({ ok: true, appended: rows.length, skipped: total - rows.length });
  } finally {
    lock.releaseLock();
  }
}

function json(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj)).setMimeType(ContentService.MimeType.JSON);
}
