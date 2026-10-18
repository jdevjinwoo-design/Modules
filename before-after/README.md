# before-after

두 장의 사진(Before / After)을 슬라이더로 비교하는 임베드용 모듈입니다. 외부 라이브러리 없이 파일 하나(`before-after.js`)로 동작합니다.

- 가로 비교(좌/우)와 세로 비교(위/아래)
- 마우스, 터치, 펜 드래그 · 클릭하면 그 위치로 이동 · 키보드(←→↑↓, Shift는 10단위, Home/End)
- Shadow DOM으로 스타일이 격리되어 어떤 페이지에 넣어도 충돌하지 않음
- `role="slider"` 등 접근성 속성 포함

## 1. 스크립트로 임베드 (Web Component)

```html
<script src="before-after.js" defer></script>

<before-after
  before="before.jpg"
  after="after.jpg"
  orientation="horizontal">
</before-after>
```

위/아래 비교:

```html
<before-after orientation="vertical" before="before.jpg" after="after.jpg"></before-after>
```

## 2. iframe으로 임베드

스크립트를 넣을 수 없는 곳(블로그, CMS, 노션 등)에서는 `embed.html`을 iframe으로 사용합니다. 옵션은 쿼리 스트링으로 넘기며, 이미지 URL은 URL 인코딩해야 합니다.

```html
<iframe
  src="https://YOUR-HOST/before-after/embed.html?before=https%3A%2F%2Fexample.com%2Fa.jpg&after=https%3A%2F%2Fexample.com%2Fb.jpg&orientation=vertical"
  style="width:100%;aspect-ratio:3/2;border:0"
  title="Before/after 비교"></iframe>
```

iframe은 슬라이더를 놓을 때마다 부모 페이지에 `{ type: 'before-after:change', value }` 메시지를 `postMessage`로 보냅니다.

## 옵션

| 속성 / 쿼리 파라미터 | 기본값 | 설명 |
|---|---|---|
| `before`, `after` | — | 이미지 URL (필수) |
| `orientation` | `horizontal` | `horizontal` = 좌/우, `vertical` = 위/아래 |
| `value` | `50` | 처음 구분선 위치 (0–100) |
| `before-label`, `after-label` | `Before`, `After` | 모서리 라벨. 빈 문자열이면 숨김 |
| `before-alt`, `after-alt` | `Before`, `After` | 이미지 대체 텍스트 |
| `ratio` | After 이미지 원본 비율 | 고정 비율. 예: `16/9`, `1/1` |
| `hover` | 없음 | 있으면 클릭 없이 마우스를 따라 움직임 (iframe에서는 `hover=1`) |

## 스타일 변경

요소에 CSS 변수를 지정합니다.

```html
<before-after style="
  --ba-radius: 12px;
  --ba-line-color: #ffcc00;
  --ba-line-width: 3px;
  --ba-handle-size: 40px;
  --ba-handle-bg: #ffcc00;
  --ba-handle-color: #000;
  --ba-label-bg: rgba(0,0,0,.6);
  --ba-label-color: #fff;" ...></before-after>
```

더 세밀하게 바꾸려면 `::part()`를 사용합니다: `line`, `handle`, `label`, `before-label`, `after-label`, `before-image`, `after-image`.

```css
before-after::part(handle) { border: 2px solid #000; }
```

## JavaScript

```js
const el = document.querySelector('before-after');
el.value = 30;                    // 위치 변경
el.orientation = 'vertical';      // 방향 변경
el.addEventListener('input',  e => console.log(e.detail.value)); // 드래그 중
el.addEventListener('change', e => console.log(e.detail.value)); // 놓았을 때
```

## 데모

`index.html`을 브라우저로 열면 가로, 세로, hover, iframe 예시를 볼 수 있습니다.
