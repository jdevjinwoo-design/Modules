#!/usr/bin/env python3
"""여러 장의 사진에 공통으로 박힌 워터마크를 자동으로 찾아 지우는 파이프라인.

1) 마스크 추정: 모든 사진의 엣지(그래디언트 크기)를 픽셀별로 중앙값을 내면
   사진마다 다른 장면 내용은 사라지고, 같은 자리에 반복되는 워터마크 엣지만 남는다.
2) 정렬(선택): 사진마다 워터마크가 몇 픽셀씩 밀려 있으면 템플릿 매칭으로 위치를 보정한다.
3) 복원: 마스크 영역을 인페인팅한다. 로컬(OpenCV/LaMa) 또는 API(Stability, Clipdrop).
"""
from __future__ import annotations

import argparse
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import cv2
import numpy as np

IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}


# ---------------------------------------------------------------- I/O
def imread(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)  # 한글 경로 대응
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"이미지를 읽을 수 없음: {path}")
    return img


def imwrite(path: Path, img: np.ndarray, quality: int = 95) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ext = path.suffix.lower() or ".png"
    params = [cv2.IMWRITE_JPEG_QUALITY, quality] if ext in (".jpg", ".jpeg") else []
    ok, buf = cv2.imencode(ext, img, params)
    if not ok:
        raise ValueError(f"저장 실패: {path}")
    buf.tofile(str(path))


def list_images(folder: Path) -> list[Path]:
    return sorted(p for p in folder.rglob("*") if p.suffix.lower() in IMG_EXT and p.is_file())


# ---------------------------------------------------------------- 1) 마스크 추정
def _grad_mag(gray: np.ndarray) -> np.ndarray:
    g = gray.astype(np.float32) / 255.0
    gx = cv2.Sobel(g, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(g, cv2.CV_32F, 0, 1, ksize=3)
    return cv2.magnitude(gx, gy)


def estimate_mask(paths: list[Path], size: tuple[int, int], *, max_images: int = 60,
                  thresh: float | None = None, dilate: int = 4, min_area: int = 30,
                  debug_dir: Path | None = None) -> np.ndarray:
    """기준 크기(size=(w,h)) 좌표계에서 워터마크 마스크(uint8 0/255)를 추정."""
    w, h = size
    if len(paths) > max_images:
        idx = np.linspace(0, len(paths) - 1, max_images).astype(int)
        paths = [paths[i] for i in idx]
    if len(paths) < 3:
        raise SystemExit("마스크 자동 추정에는 최소 3장(권장 8장 이상)이 필요합니다. --mask로 직접 지정하세요.")

    mags = []
    for p in paths:
        gray = cv2.cvtColor(imread(p), cv2.COLOR_BGR2GRAY)
        gray = cv2.resize(gray, (w, h), interpolation=cv2.INTER_AREA)
        m = _grad_mag(gray)
        mags.append(m / (np.mean(m) + 1e-6))  # 사진마다 질감 강도가 달라도 동등하게
    med = np.median(np.stack(mags), axis=0)

    # 장면 성분의 잔차(완만한 배경)를 빼서 워터마크 엣지만 두드러지게
    bg = cv2.medianBlur(np.clip(med * 50, 0, 255).astype(np.uint8), 21).astype(np.float32) / 50
    resp = np.clip(med - bg, 0, None)

    if thresh is None:
        u8 = np.clip(resp / (np.percentile(resp, 99.9) + 1e-6) * 255, 0, 255).astype(np.uint8)
        t, _ = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = (u8 > max(t, 40)).astype(np.uint8) * 255
    else:
        mask = (resp > thresh).astype(np.uint8) * 255

    # 엣지 → 획(stroke) 채우기 → 작은 잡음 제거 → 여유 확장
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    n, lab, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    keep = np.zeros_like(mask)
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] >= min_area:
            keep[lab == i] = 255
    mask = _fill_holes(keep)
    if dilate > 0:
        mask = cv2.dilate(mask, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate + 1,) * 2))

    if debug_dir:
        debug_dir.mkdir(parents=True, exist_ok=True)
        imwrite(debug_dir / "median_gradient.png",
                np.clip(resp / (resp.max() + 1e-6) * 255, 0, 255).astype(np.uint8))
        imwrite(debug_dir / "mask.png", mask)
    return mask


def _fill_holes(mask: np.ndarray) -> np.ndarray:
    """글자 'O', 'A' 같은 획 내부 구멍까지 채운다."""
    h, w = mask.shape
    flood = mask.copy()
    ff = np.zeros((h + 2, w + 2), np.uint8)
    cv2.floodFill(flood, ff, (0, 0), 255)
    holes = cv2.bitwise_not(flood)
    # 너무 큰 구멍(배경 전체 등)은 채우지 않음
    n, lab, stats, _ = cv2.connectedComponentsWithStats(holes, 8)
    out = mask.copy()
    for i in range(1, n):
        if stats[i, cv2.CC_STAT_AREA] < 0.001 * h * w:
            out[lab == i] = 255
    return out


# ---------------------------------------------------------------- 2) 이미지별 마스크 (스케일 + 정렬)
def mask_for_image(img: np.ndarray, base_mask: np.ndarray, *, align: int = 0,
                   edge_template: np.ndarray | None = None) -> np.ndarray:
    h, w = img.shape[:2]
    m = cv2.resize(base_mask, (w, h), interpolation=cv2.INTER_NEAREST)
    if align <= 0 or edge_template is None:
        return m
    # 워터마크 영역의 엣지 템플릿을 주변 ±align 픽셀 안에서 찾아 이동량 보정
    ys, xs = np.where(m > 0)
    if len(xs) == 0:
        return m
    x0, x1, y0, y1 = xs.min(), xs.max() + 1, ys.min(), ys.max() + 1
    tmpl = cv2.resize(edge_template, (w, h))[y0:y1, x0:x1]
    sx0, sy0 = max(0, x0 - align), max(0, y0 - align)
    sx1, sy1 = min(w, x1 + align), min(h, y1 + align)
    search = _grad_mag(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))[sy0:sy1, sx0:sx1]
    if search.shape[0] < tmpl.shape[0] or search.shape[1] < tmpl.shape[1]:
        return m
    res = cv2.matchTemplate(search, tmpl.astype(np.float32), cv2.TM_CCORR_NORMED)
    _, score, _, loc = cv2.minMaxLoc(res)
    if score < 0.2:
        return m
    dx, dy = sx0 + loc[0] - x0, sy0 + loc[1] - y0
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(m, M, (w, h), flags=cv2.INTER_NEAREST)


# ---------------------------------------------------------------- 3) 인페인팅 백엔드
class OpenCVBackend:
    name = "opencv"

    def __init__(self, radius: int = 5, method: str = "telea"):
        self.radius = radius
        self.flag = cv2.INPAINT_TELEA if method == "telea" else cv2.INPAINT_NS

    def __call__(self, img, mask):
        return cv2.inpaint(img, mask, self.radius, self.flag)


class LamaBackend:
    """LaMa 인페인팅 (로컬, 무료). 필요한 것: pip install torch
    모델(약 200MB)은 처음 실행할 때 자동으로 받아 이 폴더의 models/ 에 저장."""
    name = "lama"
    url = "https://github.com/enesmsahin/simple-lama-inpainting/releases/download/v0.1.0/big-lama.pt"

    def __init__(self):
        try:
            import torch
        except ImportError:
            raise SystemExit("lama 백엔드에는 torch가 필요합니다:  py -m pip install torch")
        self.torch = torch
        path = Path(__file__).resolve().parent / "models" / "big-lama.pt"
        if not path.exists():
            import requests
            path.parent.mkdir(exist_ok=True)
            print("      LaMa 모델 다운로드 중 (약 200MB, 처음 한 번만)...")
            with requests.get(self.url, stream=True, timeout=60) as r:
                r.raise_for_status()
                tmp = path.with_suffix(".part")
                with open(tmp, "wb") as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                tmp.replace(path)
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.model = torch.jit.load(str(path), map_location=self.device).eval()

    def _run(self, img, mask):
        t = self.torch
        h, w = img.shape[:2]
        ph, pw = (8 - h % 8) % 8, (8 - w % 8) % 8
        rgb = cv2.copyMakeBorder(cv2.cvtColor(img, cv2.COLOR_BGR2RGB), 0, ph, 0, pw, cv2.BORDER_REFLECT)
        m = cv2.copyMakeBorder(mask, 0, ph, 0, pw, cv2.BORDER_CONSTANT, value=0)
        x = t.from_numpy(rgb).permute(2, 0, 1)[None].float().div(255).to(self.device)
        mm = t.from_numpy((m > 127).astype(np.float32))[None, None].to(self.device)
        with t.no_grad():
            out = self.model(x, mm)[0].permute(1, 2, 0).clamp(0, 1).mul(255).byte().cpu().numpy()
        return cv2.cvtColor(out[:h, :w], cv2.COLOR_RGB2BGR)

    def __call__(self, img, mask):
        # 워터마크 주변만 잘라서 처리 (빠르고, LaMa가 잘 맞는 해상도)
        x, y, w, h = _crop_box(mask, img.shape, pad=max(64, int(0.15 * max(img.shape[:2]))))
        crop, mc = img[y:y + h, x:x + w], mask[y:y + h, x:x + w]
        k = min(1.0, 1024 / max(w, h))
        if k < 1:
            small = cv2.resize(crop, None, fx=k, fy=k, interpolation=cv2.INTER_AREA)
            ms = (cv2.resize(mc, (small.shape[1], small.shape[0]), interpolation=cv2.INTER_NEAREST))
            fixed = cv2.resize(self._run(small, ms), (w, h), interpolation=cv2.INTER_CUBIC)
        else:
            fixed = self._run(crop, mc)
        out = img.copy()
        sel = mc > 127
        out[y:y + h, x:x + w][sel] = fixed[sel]   # 마스크 밖은 원본 픽셀 그대로
        return out


class _HTTPBackend:
    env_key = ""
    max_side = 4096

    def __init__(self, api_key: str | None = None):
        import requests
        self.requests = requests
        self.api_key = api_key or os.environ.get(self.env_key)
        if not self.api_key:
            raise SystemExit(f"{self.name} 백엔드에는 환경변수 {self.env_key} 가 필요합니다.")

    @staticmethod
    def _png(a):
        return cv2.imencode(".png", a)[1].tobytes()

    def _decode(self, resp, shape):
        if resp.status_code != 200:
            raise RuntimeError(f"{self.name} API 오류 {resp.status_code}: {resp.text[:300]}")
        out = cv2.imdecode(np.frombuffer(resp.content, np.uint8), cv2.IMREAD_COLOR)
        if out.shape[:2] != shape[:2]:
            out = cv2.resize(out, (shape[1], shape[0]), interpolation=cv2.INTER_CUBIC)
        return out

    def __call__(self, img, mask):
        # 워터마크 주변만 잘라 보내면 업로드/비용이 줄고 해상도 제한도 피한다
        x, y, w, h = _crop_box(mask, img.shape, pad=96)
        crop, mcrop = img[y:y + h, x:x + w], mask[y:y + h, x:x + w]
        fixed = self.call_api(crop, mcrop)
        out = img.copy()
        out[y:y + h, x:x + w] = fixed
        return out


class StabilityBackend(_HTTPBackend):
    """Stability AI 'Erase' (https://platform.stability.ai/docs/api-reference)."""
    name, env_key = "stability", "STABILITY_API_KEY"
    url = "https://api.stability.ai/v2beta/stable-image/edit/erase"

    def call_api(self, img, mask):
        r = self.requests.post(
            self.url,
            headers={"authorization": f"Bearer {self.api_key}", "accept": "image/*"},
            files={"image": ("image.png", self._png(img)), "mask": ("mask.png", self._png(mask))},
            data={"output_format": "png"}, timeout=120)
        return self._decode(r, img.shape)


class ClipdropBackend(_HTTPBackend):
    """Clipdrop Cleanup (https://clipdrop.co/apis/docs/cleanup)."""
    name, env_key = "clipdrop", "CLIPDROP_API_KEY"
    url = "https://clipdrop-api.co/cleanup/v1"

    def call_api(self, img, mask):
        r = self.requests.post(
            self.url, headers={"x-api-key": self.api_key},
            files={"image_file": ("image.jpg", cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 97])[1].tobytes()),
                   "mask_file": ("mask.png", self._png(mask))},
            data={"mode": "quality"}, timeout=120)
        return self._decode(r, img.shape)


def _crop_box(mask, shape, pad):
    ys, xs = np.where(mask > 0)
    H, W = shape[:2]
    x0, y0 = max(0, xs.min() - pad), max(0, ys.min() - pad)
    x1, y1 = min(W, xs.max() + pad + 1), min(H, ys.max() + pad + 1)
    return x0, y0, x1 - x0, y1 - y0


BACKENDS = {"opencv": OpenCVBackend, "lama": LamaBackend,
            "stability": StabilityBackend, "clipdrop": ClipdropBackend}


# ---------------------------------------------------------------- 파이프라인
def run(args) -> int:
    src, dst = Path(args.input), Path(args.output)
    paths = list_images(src)
    if not paths:
        print(f"이미지가 없습니다: {src}", file=sys.stderr)
        return 1

    dims = {}
    for p in paths:
        h, w = imread(p).shape[:2]
        dims[p] = (w, h)
    sizes = Counter(dims.values())
    ref = sizes.most_common(1)[0][0]
    aspect = {round(w / h, 2) for w, h in sizes}
    print(f"[1/3] 이미지 {len(paths)}장, 기준 크기 {ref[0]}x{ref[1]}"
          + (f" (비율 {len(aspect)}종 — 비율이 다르면 위치가 어긋날 수 있음)" if len(aspect) > 1 else ""))

    debug = dst / "_debug" if args.debug else None
    if args.pick or args.box or args.center:
        return run_center(args, src, dst, paths, debug)
    if args.mask:
        base = cv2.imread(args.mask, cv2.IMREAD_GRAYSCALE)
        base = (cv2.resize(base, ref, interpolation=cv2.INTER_NEAREST) > 127).astype(np.uint8) * 255
        if args.dilate:  # 직접 준 마스크는 --dilate를 명시했을 때만 확장
            base = cv2.dilate(base, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * args.dilate + 1,) * 2))
    else:
        # 비율이 기준과 같은 사진만으로 추정 (비율이 다르면 워터마크 좌표가 달라짐)
        ref_aspect = round(ref[0] / ref[1], 2)
        same_aspect = [p for p in paths if round(dims[p][0] / dims[p][1], 2) == ref_aspect]
        base = estimate_mask(same_aspect, ref, thresh=args.thresh,
                             dilate=4 if args.dilate is None else args.dilate,
                             min_area=args.min_area, debug_dir=debug)
    cover = (base > 0).mean() * 100
    print(f"[2/3] 마스크 면적 {cover:.2f}%" + ("  ⚠ 너무 큼 — --thresh를 올리거나 --mask 사용" if cover > 15 else ""))
    if cover == 0:
        print("워터마크를 찾지 못했습니다. 사진 수를 늘리거나 --thresh를 낮추세요.", file=sys.stderr)
        return 1
    if args.mask_only:
        imwrite(dst / "mask.png", base)
        print(f"마스크만 저장: {dst / 'mask.png'}")
        return 0

    edge_tmpl = None
    if args.align:
        edge_tmpl = np.median(np.stack([
            _grad_mag(cv2.resize(cv2.cvtColor(imread(p), cv2.COLOR_BGR2GRAY), ref, interpolation=cv2.INTER_AREA))
            for p in paths[:40]]), axis=0) * (base > 0)

    backend = BACKENDS[args.backend or "opencv"]()
    print(f"[3/3] 복원 중 (backend={backend.name})")

    def work(p: Path):
        out_path = dst / p.relative_to(src)
        if out_path.exists() and not args.overwrite:
            return p, "skip"
        img = imread(p)
        m = mask_for_image(img, base, align=args.align, edge_template=edge_tmpl)
        res = backend(img, m)
        imwrite(out_path, res, quality=args.quality)
        if debug:
            imwrite(debug / "masks" / (p.stem + ".png"), m)
        return p, "ok"

    workers = args.workers or (4 if backend.name in ("stability", "clipdrop") else 1 if backend.name == "lama" else os.cpu_count())
    fails = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(work, p) for p in paths]
        for i, f in enumerate(futs, 1):
            try:
                p, st = f.result()
                print(f"  ({i}/{len(paths)}) {st:4} {p.relative_to(src)}")
            except Exception as e:  # 한 장 실패해도 계속
                fails += 1
                print(f"  ({i}/{len(paths)}) FAIL {paths[i - 1].name}: {e}", file=sys.stderr)
    print(f"완료: {len(paths) - fails}장 → {dst}" + (f", 실패 {fails}장" if fails else ""))
    return 1 if fails else 0


def run_center(args, src, dst, paths, debug) -> int:
    from center import CenterModel, pick_box
    box = None
    if args.box:
        box = tuple(int(v) for v in args.box.split(","))
    elif args.pick:
        print(f"      첫 사진에서 워터마크를 박스로 감싸세요: {paths[0].name}")
        box = pick_box(paths[0])
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "box.txt").write_text(",".join(map(str, box)))
        print(f"      박스 {box}  (다음엔 --box {','.join(map(str, box))} 로 재사용)")
    print("[2/3] 워터마크 위치·크기·투명도 추정 중...")
    model = CenterModel(paths, box, thresh=args.thresh, dilate=2 if args.dilate is None else args.dilate,
                        min_area=args.min_area, debug_dir=debug)
    blend = args.fill == "blend"
    if not blend and args.backend is None:
        try:
            import torch  # noqa: F401
            args.backend = "lama"
        except ImportError:
            args.backend = "opencv"
            print("      (torch가 없어 opencv로 채웁니다. 더 자연스럽게 하려면:  py -m pip install torch)")
    backend = None if blend else BACKENDS[args.backend]()
    print(f"[3/3] 복원 중 ({'역블렌딩' if blend else '글자 모양 마스크 + ' + backend.name})")
    fails = 0
    for i, p in enumerate(paths, 1):
        try:
            out_path = dst / p.relative_to(src)
            img = imread(p)
            if blend:
                res, m = model.restore(img, p.name)
            else:
                m = model.mask_for(img, p.name)
                res = backend(img, m)
            imwrite(out_path, res, quality=args.quality)
            if debug:
                imwrite(debug / "masks" / (p.stem + ".png"), m)
            print(f"  ({i}/{len(paths)}) ok   {p.relative_to(src)}")
        except Exception as e:
            fails += 1
            print(f"  ({i}/{len(paths)}) FAIL {p.name}: {e}", file=sys.stderr)
    print(f"완료: {len(paths) - fails}장 → {dst}" + (f", 실패 {fails}장" if fails else ""))
    return 1 if fails else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="여러 사진에 공통으로 있는 워터마크 자동 제거")
    ap.add_argument("input", help="원본 사진 폴더")
    ap.add_argument("output", help="결과 저장 폴더 (하위 폴더 구조 유지)")
    ap.add_argument("-b", "--backend", choices=BACKENDS, help="채우기 방식 (기본: --pick 모드는 torch가 있으면 lama, 아니면 opencv)")
    ap.add_argument("--pick", action="store_true", help="첫 사진에서 워터마크를 마우스로 박스 지정 (정중앙 워터마크용)")
    ap.add_argument("--box", help="박스를 직접 지정: x,y,w,h (첫 사진 기준 픽셀)")
    ap.add_argument("--center", action="store_true", help="박스 없이 정중앙 워터마크 자동 탐색")
    ap.add_argument("--fill", choices=["inpaint", "blend"], default="inpaint",
                    help="--pick 모드 복원 방식: inpaint=글자 모양을 지우고 채움(기본), blend=반투명 역계산(사진이 많고 배경이 다양할 때)")
    ap.add_argument("--mask", help="직접 그린 마스크(흰색=워터마크). 지정하면 자동 추정 생략")
    ap.add_argument("--mask-only", action="store_true", help="마스크만 추정해서 저장하고 종료")
    ap.add_argument("--thresh", type=float, help="엣지 임계값 수동 지정 (기본: 자동)")
    ap.add_argument("--dilate", type=int, help="마스크 확장 픽셀 (자동 추정 기본 4, --mask 기본 0)")
    ap.add_argument("--min-area", type=int, default=30, help="이보다 작은 조각은 잡음으로 제거")
    ap.add_argument("--align", type=int, default=0, metavar="PX", help="사진마다 워터마크 위치가 ±PX만큼 다를 때 보정")
    ap.add_argument("--workers", type=int)
    ap.add_argument("--quality", type=int, default=95, help="JPEG 저장 품질")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--debug", action="store_true", help="output/_debug 에 중간 결과 저장")
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(run(ap.parse_args(argv)))


if __name__ == "__main__":
    main()
