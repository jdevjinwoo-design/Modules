# heic-to-png

아이폰 사진(HEIC/HEIF)을 PNG로 변환합니다. 두 가지 방법이 있습니다.

| | 웹 페이지 `index.html` | 명령줄 `heic_to_png.py` |
|---|---|---|
| 설치 | 없음 (브라우저로 열기) | Python + `pip install pillow pillow-heif` |
| 사용 | 끌어다 놓기 · 클릭 | 파일/폴더 지정, 하위 폴더 일괄 변환 |
| 결과 | 한 장씩 받기 또는 ZIP으로 한 번에 | 원본 옆 또는 지정한 폴더에 저장 |
| 개인정보 | 파일이 서버로 올라가지 않음 (브라우저 안에서 변환) | 로컬에서만 처리 |

## 1. 웹 페이지 (`index.html`)

`index.html`을 브라우저로 열고 HEIC 파일을 끌어다 놓으면 됩니다. 여러 장을 한꺼번에 넣을 수 있고, **모두 ZIP으로 받기**로 한 번에 내려받을 수 있습니다.

- Safari처럼 HEIC를 직접 읽을 수 있는 브라우저는 바로 변환합니다.
- Chrome·Edge·Firefox에서는 처음 변환할 때 변환 엔진([heic-to](https://github.com/hoppergee/heic-to), libheif 기반, 약 3MB)을 jsDelivr CDN에서 한 번 내려받습니다. 그래서 첫 변환에는 인터넷 연결이 필요합니다.
- 사진의 회전 정보는 변환 결과에 반영됩니다.

## 2. 명령줄 (`heic_to_png.py`)

```bash
pip install pillow pillow-heif
```

```bash
# 파일 하나 → 같은 폴더에 IMG_0001.png
python heic_to_png.py IMG_0001.HEIC

# 폴더 안의 HEIC 전부(하위 폴더 포함) → 결과 폴더에 같은 구조로 저장
python heic_to_png.py 사진폴더 -o 결과폴더 -r

# 4개씩 병렬 변환, 이미 있는 PNG 덮어쓰기
python heic_to_png.py 사진폴더 -j 4 --overwrite
```

| 옵션 | 설명 |
|---|---|
| `-o, --out 폴더` | 출력 폴더 (기본: 원본과 같은 폴더) |
| `-r, --recursive` | 하위 폴더까지 검색 |
| `--overwrite` | 이미 있는 PNG 덮어쓰기 (기본은 건너뜀) |
| `--no-exif` | 촬영 정보·GPS 위치 등 EXIF를 PNG에 넣지 않음 |
| `-j, --jobs N` | 동시에 변환할 개수 |

- 회전 정보(EXIF Orientation)를 픽셀에 적용해 어떤 뷰어에서도 똑바로 보입니다.
- 색 프로필(ICC)은 유지합니다. EXIF도 기본으로 유지하므로, 사진을 공유할 때 위치 정보가 걱정되면 `--no-exif`를 쓰세요.
- 한 파일이 실패해도 나머지는 계속 변환하고, 마지막에 성공/실패/건너뜀 개수를 보여 줍니다. 실패가 있으면 종료 코드 1을 반환합니다.
