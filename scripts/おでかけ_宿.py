"""おでかけサイトの「泊まる」タブのデータを作る。

宿のリストは Threads の宿紹介と同じもの（scripts/宿.py が読む neta/宿.jsonl）を使い、
docs/hotels.json に書き出す。リンクは楽天トラベルのアフィリエイトリンク
（短縮があればそちら）。サイトでは「PR」と明記して出す。

    python scripts/おでかけ_宿.py            # 書き出す
    python scripts/おでかけ_宿.py --images   # 写真があるかも確かめる（無い宿は写真なし）

「ある」は書けるが「ない」は書けない（宿.py の大前提）。特徴の印は、当てはまった
ものだけを付け、当てはまらないことは何も書かない。
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "docs" / "hotels.json"

sys.path.insert(0, str(ROOT / "scripts"))
import 地域  # noqa: E402  どの県のおでかけか（region.json）

_spec = importlib.util.spec_from_file_location("宿", ROOT / "scripts" / "宿.py")
宿 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(宿)

# 楽天トラベルの宿の写真（宿ページの共有用画像と同じもの）
IMAGE = "https://trvimg.r10s.jp/share/HOTEL/{n}/{n}.jpg"

# サイトに出す特徴の印。宿.py の切り口をそのまま使う（判定をそろえるため）。
FEATURES = [
    ("温泉", "温泉？"),
    ("露天風呂", "露天風呂ある？"),
    ("サウナ", "サウナある？"),
    ("大浴場", "大浴場ある？"),
    ("朝食バイキング", "朝食バイキングある？"),
    ("チェックアウト遅め", "チェックアウト何時？"),
]

def _切り口(名: str) -> dict:
    return next(k for k in 宿.切り口たち if k["名"] == 名)


def features(h: dict) -> list[str]:
    out = [label for label, name in FEATURES if 宿.当てはまる(_切り口(name), h)]
    種 = str(h.get("種別") or "")
    if 種 in ("一棟貸し", "グランピング"):
        out.insert(0, 種)
    return out


def _num(x):
    try:
        return float(x) if "." in str(x) else int(x)
    except (TypeError, ValueError):
        return None


def build(hotels: list[dict], 短縮: dict[int, str]) -> list[dict]:
    out = []
    for h in hotels:
        n = _num(h.get("番号"))
        url = 宿.宿のリンク先(h, 短縮)
        if not n or not url:
            continue
        city = 宿.市町(h)
        out.append({
            "id": n,
            "name": 宿.見せる名(h),
            "city": city,
            "area": str(h.get("エリア") or ""),
            "region": 地域.地区(city),
            "rating": _num(h.get("評価")),
            "reviews": _num(h.get("レビュー数")),
            "price": _num(h.get("最安")),
            "kind": str(h.get("種別") or "") or None,
            "pick": bool(h.get("手で選んだ")),
            "text": html.unescape(宿.一文(h, None, 60)),
            "features": features(h),
            "url": url,
            "image": IMAGE.format(n=n),
            "checked": str(h.get("調べた日") or "") or None,
        })
    return out


def has_image(url: str) -> bool:
    req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status == 200 and res.headers.get_content_type().startswith("image/")
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--images", action="store_true", help="写真があるかを確かめる")
    args = ap.parse_args(argv)

    if not 地域.使う("宿"):
        # 宿のリストがまだ無い県（石川版の立ち上げ時など）。サイトの「泊まる」は空にする
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps({"items": []}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print("宿は使わない設定です（region.json）。hotels.json は空にします。")
        return 0
    hotels = 宿.読む()
    if not hotels:
        # 読めなかった日は、前の hotels.json をそのまま残す（空にしない）
        print("::warning::宿のリストを読めませんでした。hotels.json はそのままにします。")
        return 0
    items = build(hotels, 宿.短縮を読む())
    if args.images:
        for it in items:
            if not has_image(it["image"]):
                it["image"] = None
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(f"宿 {len(items)} 軒（写真あり {sum(1 for i in items if i['image'])}）→ {OUT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
