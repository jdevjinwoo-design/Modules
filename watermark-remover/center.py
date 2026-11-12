"""정중앙 기준 워터마크를 '역블렌딩'으로 걷어내는 모드 (--pick / --box / --center).

워터마크 합성식  I = a*W + (1-a)*B   (a: 불투명도 맵, W: 워터마크 색, B: 원래 사진)
여러 장에서 a, W를 추정하면  B = (I - a*W) / (1-a)  로 원래 픽셀이 되살아난다.
인페인팅처럼 주변 색으로 '덮는' 게 아니라서 뭉개지지 않는다.

좌표계: 사진마다 (중심 cx,cy, 배율 s)로 '정규화 패치'에 옮긴다.
    패치 좌표 u  <->  사진 좌표 X = cx + (u + x0) / s
배율 s는 워터마크 크기가 사진 가로/짧은 변/고정 픽셀 중 무엇에 비례하는지 자동 판별한 뒤,
사진마다 템플릿 매칭으로 위치와 크기를 미세 보정한다.
"""
from __future__ import annotations

import cv2
import numpy as np

from wmremove import _fill_holes, _grad_mag, imread


def _median_resp(grays):
    mags = []
    for g in grays:
        m = _grad_mag(g)
        mags.append(m / (np.mean(m) + 1e-6))
    med = np.median(np.stack(mags), axis=0)
    k = max(3, (min(med.shape) // 8) | 1)
    k = min(k, 21)
    bg = cv2.medianBlur(np.clip(med * 50, 0, 255).astype(np.uint8), k).astype(np.float32) / 50
    return np.clip(med - bg, 0, None)


def _mask_from_resp(resp, thresh=None, min_area=20):
    if thresh is None:
        u8 = np.clip(resp / (np.percentile(resp, 99.8) + 1e-6) * 255, 0, 255).astype(np.uint8)
        t, _ = cv2.threshold(u8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        mask = (u8 > max(t, 40)).astype(np.uint8) * 255
    else:
        mask = (resp > thresh).astype(np.uint8) * 255
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k, iterations=2)
    n, lab, st, _ = cv2.connectedComponentsWithStats(mask, 8)
    keep = np.zeros_like(mask)
    for i in range(1, n):
        if st[i, cv2.CC_STAT_AREA] >= min_area:
            keep[lab == i] = 255
    return _fill_holes(keep)


def consensus_mask(crops, min_votes=None):
    """모든 사진에 공통으로 있는 윤곽선 → 윤곽으로 나뉜 영역 중 '흰색이 덧칠된' 영역을 채워 글자 모양 마스크."""
    k = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1,) * 2)
    n_img = len(crops)
    min_votes = min_votes or max(2, n_img - max(1, n_img // 5))
    grays = [cv2.cvtColor(c, cv2.COLOR_BGR2GRAY) for c in crops]
    votes = np.zeros(grays[0].shape, np.float32)
    for g in grays:
        e = cv2.Canny(cv2.GaussianBlur(g, (3, 3), 0), 10, 30)
        votes += cv2.dilate(e, np.ones((3, 3), np.uint8)) > 0
    cons = (votes >= min_votes).astype(np.uint8) * 255
    e = cv2.morphologyEx(cons, cv2.MORPH_CLOSE, k(2))
    n, lab, st, _ = cv2.connectedComponentsWithStats(e, 8)
    keep = np.zeros_like(e)
    if n > 1:
        big = st[1:, cv2.CC_STAT_AREA].max()
        for i in range(1, n):
            if st[i, cv2.CC_STAT_AREA] >= 0.02 * big:
                keep[lab == i] = 255
    h, w = keep.shape
    gf = [g.astype(np.float32) for g in grays]
    bgs = [cv2.medianBlur(g, 151 if min(h, w) > 151 else (min(h, w) // 2) * 2 - 1).astype(np.float32) for g in grays]
    # 윤곽의 작은 틈 때문에 글자 안쪽이 바깥과 이어지지 않도록, 영역 분할은 틈을 더 메운 윤곽으로
    loose = (votes >= max(2, min_votes - 1)).astype(np.uint8) * 255
    loose = cv2.bitwise_and(loose, cv2.dilate(keep, k(6)))   # 주 윤곽 근처의 끊긴 부분만 이어줌
    sep = cv2.morphologyEx(cv2.bitwise_or(keep, loose), cv2.MORPH_CLOSE, k(3))
    n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.bitwise_not(sep), 4)
    sil = keep.copy()
    for i in range(1, n):
        x, y, ww, hh, _a = st[i]
        if x == 0 or y == 0 or x + ww == w or y + hh == h:
            continue
        r = lab == i
        num = sum(float((g[r] - b[r]).mean()) for g, b in zip(gf, bgs))
        den = sum(float((255 - b[r]).mean()) for b in bgs)
        if num / max(den, 1) > 0.06:
            sil[r] = 255
    sil = cv2.morphologyEx(sil, cv2.MORPH_OPEN, k(2))
    # 안쪽 판정이 빠진 얇은 획(예: m의 다리)도 덮이도록 윤곽선 띠를 넉넉히 포함
    sil = cv2.bitwise_or(sil, cv2.dilate(keep, k(4)))
    # 획 사이 좁은 틈(m의 다리 사이 등)은 메울 때 엉뚱한 색을 끌어오므로 같이 덮음
    return _fill_holes(cv2.morphologyEx(sil, cv2.MORPH_CLOSE, k(6)))


class CenterModel:
    def __init__(self, paths, box=None, *, thresh=None, dilate=3, min_area=20, log=print, debug_dir=None):
        self.log = log
        imgs = [imread(p) for p in paths]
        self.names = [p.name for p in paths]
        H1, W1 = imgs[0].shape[:2]
        if box is None:  # 박스를 안 주면 첫 사진 가운데 60%를 탐색 영역으로
            box = (int(W1 * .2), int(H1 * .2), int(W1 * .6), int(H1 * .6))
        bx, by, bw, bh = box
        dims = [im.shape[1::-1] for im in imgs]

        # 1) 워터마크 크기 기준 가설 선택: 가로 / 짧은 변 / 고정 픽셀
        best = None
        for mode in ("width", "short", "fixed"):
            self._setup(mode, dims, (W1, H1), box)
            grays = [cv2.cvtColor(self._patch(im, self._T0(im)), cv2.COLOR_BGR2GRAY) for im in imgs]
            resp = _median_resp(grays)
            score = float(np.percentile(resp, 99.5))
            log(f"      크기 기준 '{mode}': 선명도 {score:.2f}")
            if best is None or score > best[0]:
                best = (score, mode)
        self._setup(best[1], dims, (W1, H1), box)
        self.T = [self._T0(im) for im in imgs]

        # 2) 사진별 위치·크기 미세 보정 (2회 반복)
        for it in range(2):
            grays = [cv2.cvtColor(self._patch(im, T), cv2.COLOR_BGR2GRAY) for im, T in zip(imgs, self.T)]
            tmpl = _median_resp(grays)
            m = _mask_from_resp(tmpl, thresh, min_area)
            if not m.any():
                raise SystemExit("박스 안에서 워터마크를 찾지 못했습니다. 박스를 워터마크에 더 딱 맞게 그려 보세요.")
            self.T = [self._refine(im, T, tmpl, m) for im, T in zip(imgs, self.T)]

        crops = [self._patch(im, T) for im, T in zip(imgs, self.T)]
        edge = _mask_from_resp(_median_resp([cv2.cvtColor(c, cv2.COLOR_BGR2GRAY) for c in crops]), thresh, min_area)
        self.shape = consensus_mask(crops)
        if dilate:
            self.shape = cv2.dilate(self.shape, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * dilate + 1,) * 2))
        self.mask, self.alpha, self.W, D = self._estimate(crops, edge, dilate)
        log(f"      크기 기준='{self.mode}', 워터마크 색(BGR)={np.round(self.W).astype(int).tolist()}, "
            f"최대 불투명도={self.alpha.max():.2f}")
        if debug_dir:
            debug_dir.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(debug_dir / "mean_diff.png"), np.clip(np.abs(D).max(2) * 4, 0, 255).astype(np.uint8))
            cv2.imwrite(str(debug_dir / "mask.png"), self.mask)
            cv2.imwrite(str(debug_dir / "shape_mask.png"), self.shape)
            cv2.imwrite(str(debug_dir / "alpha.png"), np.clip(self.alpha * 255, 0, 255).astype(np.uint8))
        self._byname = dict(zip(self.names, self.T))

    # ---------------- 좌표계
    def _setup(self, mode, dims, first, box):
        self.mode = mode
        self.fixed_ref = float(np.median([w for w, h in dims]))
        lens = {"width": [w for w, h in dims], "short": [min(w, h) for w, h in dims], "fixed": [self.fixed_ref]}[mode]
        self.N = float(np.median(lens))  # 정규화 기준 길이 = 대표 해상도 (원본 해상도 유지)
        W1, H1 = first
        s1 = self._s0(W1, H1)
        bx, by, bw, bh = box
        self.x0, self.y0 = (bx - W1 / 2) * s1, (by - H1 / 2) * s1
        self.pw, self.ph = max(8, round(bw * s1)), max(8, round(bh * s1))

    def _s0(self, w, h):
        return self.N / {"width": w, "short": min(w, h), "fixed": self.fixed_ref}[self.mode]

    def _T0(self, img):
        h, w = img.shape[:2]
        return (w / 2, h / 2, self._s0(w, h))

    def _M(self, T, ox=0.0, oy=0.0):
        cx, cy, s = T  # 사진 → 패치:  u = s*(X - cx) - x0
        return np.float32([[s, 0, -s * cx - self.x0 + ox], [0, s, -s * cy - self.y0 + oy]])

    def _patch(self, img, T, margin=0):
        return cv2.warpAffine(img, self._M(T, margin, margin), (self.pw + 2 * margin, self.ph + 2 * margin),
                              flags=cv2.INTER_AREA if T[2] < 1 else cv2.INTER_CUBIC, borderMode=cv2.BORDER_REFLECT)

    def _refine(self, img, T, tmpl, mask):
        cx, cy, s = T
        mg = max(4, round(0.08 * max(self.pw, self.ph)))
        wmask = cv2.dilate(mask, np.ones((5, 5), np.uint8)).astype(np.float32) / 255
        tm = (tmpl * wmask).astype(np.float32)
        best = (-1, T)
        for k in (0.94, 0.97, 1.0, 1.03, 1.06):
            Tk = (cx, cy, s * k)
            g = _grad_mag(cv2.cvtColor(self._patch(img, Tk, mg), cv2.COLOR_BGR2GRAY))
            g = g / (g.mean() + 1e-6)
            r = cv2.matchTemplate(g.astype(np.float32), tm, cv2.TM_CCORR_NORMED, mask=wmask)
            _, sc, _, loc = cv2.minMaxLoc(r)
            if sc > best[0]:
                dx, dy = loc[0] - mg, loc[1] - mg      # 패치 좌표 이동 → 사진 좌표 이동
                best = (sc, (cx + dx / (s * k), cy + dy / (s * k), s * k))
        return best[1]

    # ---------------- 모양·불투명도·색 추정
    @staticmethod
    def _estimate(crops, edge, dilate):
        """평균 이미지 이용:  mean(I) = a*W + (1-a)*mean(B)  (합성식이 선형이라 평균에도 그대로 성립).
        여러 장의 평균 배경 mean(B)는 매끈하므로 주변에서 정확히 메울 수 있다 → a를 픽셀별로 직접 계산."""
        I = np.stack([c.astype(np.float32) for c in crops])
        M = I.mean(0)
        k = lambda r: cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1,) * 2)
        Mu8 = np.clip(M, 0, 255).astype(np.uint8)
        # 1차: 윤곽 마스크를 넉넉히 키워 메운 평균 배경과의 차이로 '속이 찬' 워터마크 모양을 얻음
        region = cv2.dilate(edge, k(9))
        D = M - cv2.inpaint(Mu8, region, 9, cv2.INPAINT_TELEA).astype(np.float32)
        mag = np.abs(D).max(2)
        sig = mag[region > 0]
        noise = np.median(np.abs(D).max(2)[region == 0]) if (region == 0).any() else 1.0
        t = max(3 * noise + 1.5, np.percentile(sig, 50) * 0.25)
        mask = ((mag > t) & (region > 0)).astype(np.uint8) * 255
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k(2))
        mask = cv2.bitwise_or(mask, edge)
        mask = _fill_holes(mask)
        # 2차: 실제 모양 + 여유로 다시 메워 평균 배경을 정밀하게
        fill = cv2.dilate(mask, k(3))
        Bm = cv2.inpaint(Mu8, fill, 7, cv2.INPAINT_TELEA).astype(np.float32)
        D = M - Bm
        # 워터마크 색 W: 사진마다 배경이 다르다는 점을 이용한 픽셀별 회귀(I-B = aW - aB)
        Bi = np.stack([cv2.inpaint(c, fill, 7, cv2.INPAINT_TELEA).astype(np.float32) for c in crops])
        y = I - Bi
        yc, Bc = y - y.mean(0), Bi - Bi.mean(0)
        a0 = np.clip(-(yc * Bc).sum((0, 3)) / ((Bc ** 2).sum((0, 3)) + 1e-3), 0, 0.97)
        c = (y + a0[None, ..., None] * Bi).mean(0)
        inside = mask > 0
        sel = inside & (a0 > np.percentile(a0[inside], 60))
        W = np.clip(np.median(c[sel] / np.maximum(a0[sel][:, None], 1e-3), axis=0), 0, 255)
        support = cv2.dilate(mask, k(1)) > 0

        def alpha_for(Wc, gain=1.0):
            # 불투명도: 평균 이미지에서 직접  a = D·(W-Bm) / |W-Bm|²
            d = Wc[None, None, :] - Bm
            a = np.clip(gain * (D * d).sum(2) / ((d ** 2).sum(2) + 1e-3), 0, 0.97) * support
            a[a < 0.02] = 0
            return a.astype(np.float32)

        # 잔상 최소화로 W(색)와 세기를 확정: 복원 결과들에 공통으로 남는 엣지가 가장 적은 값
        edge_zone = cv2.dilate(mask, k(2)) > 0
        sub = I[:: max(1, len(I) // 12)]

        def residual(Wc, gain=1.0):
            a = alpha_for(Wc, gain)[None, ..., None]
            R = np.clip((sub - a * Wc) / (1 - a), 0, 255).astype(np.uint8)
            resp = _median_resp([cv2.cvtColor(r, cv2.COLOR_BGR2GRAY) for r in R])
            return float(resp[edge_zone].mean())

        cands = [W] + [np.full(3, g, np.float32) for g in range(0, 256, 15)] + [np.full(3, 255.0)]
        if W.max() > 0:
            cands += [np.clip(W * f, 0, 255) for f in (0.9, 1.05, 1.1)]
        W = min(cands, key=residual)
        gain = min((0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15), key=lambda g_: residual(W, g_))
        a = alpha_for(W, gain)
        if dilate:
            mask = cv2.dilate(mask, k(dilate))
        return mask, a.astype(np.float32), W.astype(np.float32), D

    # ---------------- 사진 한 장 복원
    def mask_for(self, img, name=None):
        """글자 모양 마스크를 이 사진 좌표로 옮긴 것 (인페인팅 백엔드용)."""
        h, w = img.shape[:2]
        cx, cy, s = self._byname.get(name) or self._T0(img)
        Mi = np.float32([[1 / s, 0, self.x0 / s + cx], [0, 1 / s, self.y0 / s + cy]])
        return cv2.warpAffine(self.shape, Mi, (w, h), flags=cv2.INTER_NEAREST)

    edge_fix = True

    def restore(self, img, name=None, inpaint_above=0.85):
        h, w = img.shape[:2]
        T = self._byname.get(name) or self._T0(img)
        cx, cy, s = T
        X0, Y0 = cx + self.x0 / s, cy + self.y0 / s
        X1, Y1 = X0 + self.pw / s, Y0 + self.ph / s
        bx0, by0 = max(0, int(np.floor(X0)) - 1), max(0, int(np.floor(Y0)) - 1)
        bx1, by1 = min(w, int(np.ceil(X1)) + 1), min(h, int(np.ceil(Y1)) + 1)
        # 패치 → 사진 지역 좌표:  X - bx0 = (u + x0)/s + cx - bx0
        Mi = np.float32([[1 / s, 0, self.x0 / s + cx - bx0], [0, 1 / s, self.y0 / s + cy - by0]])
        size = (bx1 - bx0, by1 - by0)
        a = cv2.warpAffine(self.alpha, Mi, size, flags=cv2.INTER_LINEAR)
        m = cv2.warpAffine(self.mask, Mi, size, flags=cv2.INTER_NEAREST)
        patch = img[by0:by1, bx0:bx1].astype(np.float32)
        out = (patch - a[..., None] * self.W) / (1 - a[..., None])
        bad = ((a > inpaint_above) | (out.min(2) < -10) | (out.max(2) > 265)) & (m > 0)
        if self.edge_fix:
            # 워터마크 윤곽선(투명도가 급변하는 1~2px)은 JPEG 번짐 때문에 역블렌딩 오차가 커서 얇게 메움
            gx = cv2.Sobel(a, cv2.CV_32F, 1, 0, ksize=3); gy = cv2.Sobel(a, cv2.CV_32F, 0, 1, ksize=3)
            gm = np.hypot(gx, gy)
            if gm.max() > 0:
                bad |= gm > 0.35 * gm.max()
        out = np.clip(out, 0, 255).astype(np.uint8)
        if bad.any():
            out = cv2.inpaint(out, cv2.dilate(bad.astype(np.uint8) * 255, np.ones((3, 3), np.uint8)), 3, cv2.INPAINT_TELEA)
        res = img.copy()
        res[by0:by1, bx0:bx1] = out
        full = np.zeros((h, w), np.uint8)
        full[by0:by1, bx0:bx1] = m
        return res, full


def pick_box(path):
    """첫 사진을 창에 띄워 마우스로 워터마크 박스를 그리게 한다. (x, y, w, h) 원본 좌표."""
    import tkinter as tk
    from PIL import Image, ImageTk

    img = Image.open(path).convert("RGB")
    root = tk.Tk()
    root.title("워터마크를 박스로 감싸고 Enter (다시 그리려면 다시 드래그)")
    sw, sh = root.winfo_screenwidth() * 0.9, root.winfo_screenheight() * 0.85
    k = min(1.0, sw / img.width, sh / img.height)
    disp = img.resize((int(img.width * k), int(img.height * k)))
    tkimg = ImageTk.PhotoImage(disp)
    cv = tk.Canvas(root, width=disp.width, height=disp.height, cursor="cross")
    cv.pack()
    cv.create_image(0, 0, image=tkimg, anchor="nw")
    st = {"r": None, "p": None, "box": None}

    def down(e):
        st["p"] = (e.x, e.y)
        if st["r"]:
            cv.delete(st["r"])
        st["r"] = cv.create_rectangle(e.x, e.y, e.x, e.y, outline="#ff2d55", width=2)

    def move(e):
        if st["p"]:
            cv.coords(st["r"], *st["p"], e.x, e.y)

    def up(e):
        x0, y0 = st["p"]
        x0, x1 = sorted((x0, e.x)); y0, y1 = sorted((y0, e.y))
        if x1 - x0 > 5 and y1 - y0 > 5:
            st["box"] = (round(x0 / k), round(y0 / k), round((x1 - x0) / k), round((y1 - y0) / k))

    def ok(_=None):
        if st["box"]:
            root.destroy()

    cv.bind("<ButtonPress-1>", down); cv.bind("<B1-Motion>", move); cv.bind("<ButtonRelease-1>", up)
    root.bind("<Return>", ok); root.bind("<KP_Enter>", ok)
    root.mainloop()
    if not st["box"]:
        raise SystemExit("박스를 그리지 않고 창을 닫았습니다.")
    return st["box"]
