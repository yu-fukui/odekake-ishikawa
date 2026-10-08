"""AI（Anthropic API）を1回呼ぶごとに、使った量と概算の金額を state/usage.jsonl に1行ずつ残す。

代表の指示（2026-10-08）「使用量を見てもらいたい。想定予算の60％超えたら教えて」。
請求の正確な数字は Admin キーが要るので、ここでは API の返事の usage から見積もる（B案）。
集計と知らせは scripts/予算.mjs が行う。
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

置き場 = Path("state/usage.jsonl")

# 1M トークンあたりのドル（入力, 出力）。2026-10 時点の Anthropic の料金。上から順に見る
単価 = [
    ("fable", 10.0, 50.0),
    ("mythos", 10.0, 50.0),
    ("opus-5-5", 4.0, 20.0),
    ("opus", 5.0, 25.0),
    ("sonnet-4", 3.0, 15.0),
    ("sonnet", 2.0, 10.0),
    ("haiku-4", 1.0, 5.0),
    ("haiku", 0.1, 0.5),
]
検索の単価 = 0.01  # Web検索 1回あたり（$10 / 1000回）


def ドル(モデル: str, usage: dict, 検索: int = 0) -> float:
    入, 出 = next(((a, b) for 名, a, b in 単価 if 名 in (モデル or "")), (5.0, 25.0))
    u = usage or {}
    入力 = (u.get("input_tokens") or 0) + 1.25 * (u.get("cache_creation_input_tokens") or 0) + 0.1 * (u.get("cache_read_input_tokens") or 0)
    return 入力 / 1e6 * 入 + (u.get("output_tokens") or 0) / 1e6 * 出 + 検索 * 検索の単価


def 記録(何: str, モデル: str, usage: dict, 検索: int = 0) -> None:
    """1行足す。記録に失敗しても本来の仕事は止めない。"""
    try:
        u = usage or {}
        行 = {
            "時刻": datetime.now(timezone(timedelta(hours=9))).isoformat(timespec="seconds"),
            "何": 何,
            "モデル": モデル,
            "入力": u.get("input_tokens") or 0,
            "出力": u.get("output_tokens") or 0,
            "検索": 検索,
            "ドル": round(ドル(モデル, u, 検索), 5),
        }
        置き場.parent.mkdir(parents=True, exist_ok=True)
        with 置き場.open("a", encoding="utf-8") as f:
            f.write(json.dumps(行, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001
        print(f"::warning::使用量を記録できませんでした（{exc}）")
