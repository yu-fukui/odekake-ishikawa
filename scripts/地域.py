"""どの県のおでかけかを region.json から読む。

福井版と石川版は同じプログラムで動かす。違うところ（県名・サイト・アカウント・
地名・使う枠）は region.json に書き、プログラムはここから読む。

「言いかえ」は、AI に渡す指示文の中の福井だけの言い回し（例文・地名）を、
上から順に置きかえる表。福井版は空なので、指示文は今までと一字も変わらない。

REGION_FILE を指定すると、別の region.json を読む（テストや石川版の試しに使う）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def 読む() -> dict:
    パス = Path(os.environ.get("REGION_FILE", "").strip() or ROOT / "region.json")
    return json.loads(パス.read_text(encoding="utf-8"))


地域 = 読む()
県名: str = 地域["県名"]
サイトURL: str = 地域["サイトURL"].rstrip("/")


def 使う(名前: str) -> bool:
    return bool(地域.get("使う", {}).get(名前, True))


def 直す(文: str) -> str:
    """指示文の中の言い回しを、この県のものに置きかえる。"""
    for 元, 先 in 地域.get("言いかえ", []):
        文 = 文.replace(元, 先)
    return 文


def 地区(市町: str | None) -> str | None:
    """市町から、サイトの絞り込みに使う地区（嶺北・嶺南、加賀・金沢・能登など）を決める。"""
    if not 市町:
        return None
    区 = 地域.get("地区", {})
    for 名 in 区.get("区切り", []):
        if 市町 in 区.get(名, []):
            return 名
    return 区.get("ほかは")
