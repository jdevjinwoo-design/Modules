# watermark-remover

여러 장의 사진에 **같은 워터마크**가 같은 자리에 박혀 있을 때, 워터마크 위치를 자동으로 찾아 한 번에 지우는 파이프라인입니다.

> 본인이 권리를 가진 사진(예: 원본을 잃어버린 내 사진, 납품받은 내 사진의 샘플 워터마크)에만 사용하세요.

## 동작 방식

1. **마스크 자동 추정** — 모든 사진의 엣지 강도를 픽셀별 중앙값으로 합칩니다. 사진마다 다른 장면 엣지는 사라지고, 매번 같은 자리에 있는 워터마크 엣지만 남습니다. 이를 이진화 → 획 채우기 → 잡음 제거 → 약간 확장해 마스크를 만듭니다.
2. **사진별 마스크** — 해상도가 달라도 비율이 같으면 마스크를 비례 축소/확대해 씁니다. `--align`을 주면 사진마다 몇 픽셀씩 밀린 워터마크를 템플릿 매칭으로 보정합니다.
3. **복원(인페인팅)** — 마스크 영역을 채웁니다.

| backend | 위치 | 특징 |
|---|---|---|
| `opencv` (기본) | 로컬 | 설치 간단, 빠름. 작은 글자/로고에 충분 |
| `lama` | 로컬 (torch) | 큰 워터마크도 자연스럽게. `pip install simple-lama-inpainting` |
| `stability` | API | Stability AI Erase. `STABILITY_API_KEY` |
| `clipdrop` | API | Clipdrop Cleanup. `CLIPDROP_API_KEY` |

API 백엔드는 워터마크 주변만 잘라서 보내고 결과를 원본에 다시 붙이므로, 업로드 크기·비용이 작고 나머지 픽셀은 원본 그대로 유지됩니다.

## 사용법

```bash
pip install -r requirements.txt
```

```bash
python wmremove.py ./photos ./cleaned
```

```bash
STABILITY_API_KEY=sk-... python wmremove.py ./photos ./cleaned --backend stability
```

먼저 마스크만 확인하고 싶으면:

```bash
python wmremove.py ./photos ./cleaned --mask-only --debug
```

`cleaned/mask.png`를 열어 보고, 필요하면 그림판 등으로 고친 뒤(흰색 = 지울 부분) `--mask`로 넘깁니다.

```bash
python wmremove.py ./photos ./cleaned --mask ./cleaned/mask.png
```

### 옵션

| 옵션 | 설명 |
|---|---|
| `--mask PATH` | 직접 만든 마스크 사용 (자동 추정 생략) |
| `--mask-only` | 마스크만 저장하고 종료 |
| `--thresh F` | 엣지 임계값 수동 지정. 마스크가 너무 크면 올리고, 워터마크가 덜 잡히면 내림 |
| `--dilate N` | 마스크 확장 픽셀 (자동 추정 기본 4, `--mask` 기본 0). 테두리 잔상이 남으면 키움 |
| `--align PX` | 워터마크 위치가 사진마다 ±PX 다를 때 보정 |
| `--debug` | `output/_debug`에 중앙값 엣지 맵, 마스크, 사진별 마스크 저장 |
| `--workers N` | 병렬 처리 수 (API는 기본 4) |
| `--overwrite` | 이미 결과가 있는 파일도 다시 처리 |

결과는 입력 폴더의 하위 구조와 파일명을 유지해 저장됩니다. 전후 비교는 이 저장소의 [`before-after`](../before-after) 모듈로 볼 수 있습니다.

## 팁 / 한계

- 자동 추정은 **사진이 많고 서로 다를수록** 정확합니다. 최소 3장, 8장 이상 권장.
- 비율이 다른 사진이 섞여 있으면 가장 흔한 비율의 사진으로만 마스크를 추정합니다. 비율별로 폴더를 나눠 돌리는 게 안전합니다.
- 사진마다 워터마크 위치·크기가 크게 다르면(무작위 배치, 타일 반복) 자동 추정이 안 됩니다. 이때는 `--mask`를 쓰세요.
- 넓은 영역을 덮는 반투명 워터마크는 `opencv`로는 흐릿한 자국이 남습니다. `lama`나 API 백엔드를 쓰세요.
