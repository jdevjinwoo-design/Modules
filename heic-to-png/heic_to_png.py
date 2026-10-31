#!/usr/bin/env python3
"""HEIC/HEIF 이미지를 PNG로 변환합니다.

사용법:
    python heic_to_png.py IMG_0001.HEIC                # 같은 폴더에 IMG_0001.png 생성
    python heic_to_png.py 사진폴더 -o 결과폴더 -r      # 하위 폴더까지 일괄 변환
    python heic_to_png.py *.heic --overwrite -j 4      # 덮어쓰기, 4개 병렬

필요 패키지:
    pip install pillow pillow-heif
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

try:
    from PIL import Image, ImageOps
    from pillow_heif import register_heif_opener
except ImportError:
    sys.exit("필요한 패키지가 없습니다. 먼저 설치하세요:\n  pip install pillow pillow-heif")

register_heif_opener()

# 중국어(GBK) 등 한글이 없는 콘솔 인코딩에서도 한글 경로를 출력하다 죽지 않게 한다.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(errors="replace")
    except (AttributeError, ValueError):
        pass

HEIC_EXTS = {".heic", ".heif", ".hif"}


def collect(inputs: list[str], recursive: bool) -> list[tuple[Path, Path]]:
    """(원본 파일, 기준 폴더) 목록을 만든다. 기준 폴더는 출력 시 하위 경로를 유지하는 데 쓴다."""
    found: list[tuple[Path, Path]] = []
    for raw in inputs:
        p = Path(raw)
        if p.is_dir():
            pattern = "**/*" if recursive else "*"
            others = 0
            for f in sorted(p.glob(pattern)):
                if not f.is_file():
                    continue
                if f.suffix.lower() in HEIC_EXTS:
                    found.append((f, p))
                else:
                    others += 1
            if others:
                print(f"[안내] {p} 안의 HEIC가 아닌 파일 {others}개(JPG, 동영상 등)는 그대로 둡니다.")
        elif p.is_file():
            found.append((p, p.parent))
        else:
            print(f"[건너뜀] 찾을 수 없음: {raw}", file=sys.stderr)
    return found


def target_path(src: Path, base: Path, out_dir: Path | None) -> Path:
    if out_dir is None:
        return src.with_suffix(".png")
    return (out_dir / src.relative_to(base)).with_suffix(".png")


def convert(src: Path, dst: Path, keep_exif: bool) -> None:
    with Image.open(src) as im:
        icc = im.info.get("icc_profile")
        exif = im.getexif()
        # 회전 정보(EXIF Orientation)를 픽셀에 반영해 어떤 뷰어에서도 똑바로 보이게 한다.
        im = ImageOps.exif_transpose(im)
        if im.mode not in ("RGB", "RGBA", "L", "LA", "I;16"):
            im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
        params: dict = {}
        if icc:
            params["icc_profile"] = icc
        if keep_exif and exif:
            exif[0x0112] = 1  # 이미 회전을 적용했으므로 Orientation을 '정상'으로
            params["exif"] = exif.tobytes()
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        try:
            im.save(tmp, format="PNG", **params)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
    os.replace(tmp, dst)


def _job(args: tuple[Path, Path, bool]) -> tuple[Path, Path, str | None]:
    src, dst, keep_exif = args
    try:
        convert(src, dst, keep_exif)
        return src, dst, None
    except Exception as e:  # noqa: BLE001 - 한 파일 실패가 전체를 멈추지 않게
        return src, dst, f"{type(e).__name__}: {e}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="HEIC/HEIF 이미지를 PNG로 변환합니다.")
    ap.add_argument("inputs", nargs="+", help="HEIC 파일 또는 폴더")
    ap.add_argument("-o", "--out", type=Path, help="출력 폴더 (기본: 원본과 같은 폴더)")
    ap.add_argument("-r", "--recursive", action="store_true", help="폴더의 하위 폴더까지 검색")
    ap.add_argument("--overwrite", action="store_true", help="이미 있는 PNG를 덮어쓰기")
    ap.add_argument("--no-exif", action="store_true", help="EXIF(촬영 정보·위치 등)를 PNG에 넣지 않기")
    ap.add_argument("-j", "--jobs", type=int, default=1, help="동시에 변환할 개수 (기본 1)")
    a = ap.parse_args(argv)

    files = collect(a.inputs, a.recursive)
    if not files:
        print("변환할 HEIC 파일이 없습니다.", file=sys.stderr)
        return 1

    jobs, skipped = [], 0
    for src, base in files:
        dst = target_path(src, base, a.out)
        if dst.exists() and not a.overwrite:
            print(f"[건너뜀] 이미 있음: {dst}  (--overwrite로 덮어쓰기)")
            skipped += 1
            continue
        jobs.append((src, dst, not a.no_exif))

    ok = fail = 0
    if a.jobs > 1 and len(jobs) > 1:
        with ProcessPoolExecutor(max_workers=a.jobs) as ex:
            results = ex.map(_job, jobs)
            for src, dst, err in results:
                ok, fail = _report(src, dst, err, ok, fail)
    else:
        for job in jobs:
            ok, fail = _report(*_job(job), ok, fail)

    print(f"\n완료: 성공 {ok}, 실패 {fail}, 건너뜀 {skipped}")
    return 1 if fail else 0


def _report(src: Path, dst: Path, err: str | None, ok: int, fail: int) -> tuple[int, int]:
    if err:
        print(f"[실패] {src}: {err}", file=sys.stderr)
        return ok, fail + 1
    print(f"[완료] {src} -> {dst}")
    return ok + 1, fail


if __name__ == "__main__":
    sys.exit(main())
