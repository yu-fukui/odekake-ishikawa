"""Threads のフォロワーの内訳（年代・性別・国・市区町村）とフォロワー数を API で取り、表にして残す。

代表 10/9「アプリで見れる利用者層データがほしい」。
- Threads API の follower_demographics（フォロワー 100 人以上のアカウントだけ取れる）と followers_count を使う
- 結果は insights/利用者層/YYYY-MM-DD.md に書く（個人は分からない、まとめた数だけ）
使い方: THREADS_ACCESS_TOKEN=xxx python scripts/利用者層.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))
NAMES = {"age": "年代", "gender": "性別", "country": "国", "city": "市区町村"}
GENDER = {"F": "女性", "M": "男性", "U": "不明"}


def get(path: str, **params) -> dict:
    params["access_token"] = os.environ["THREADS_ACCESS_TOKEN"]
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:400]}


def breakdown(uid: str, kind: str) -> list[tuple[str, int]] | str:
    d = get(f"{uid}/threads_insights", metric="follower_demographics", breakdown=kind)
    if "error" in d:
        return f"取れませんでした: {d['error']}"
    rows = []
    for item in d.get("data", []):
        for b in (item.get("total_value") or {}).get("breakdowns", []):
            for r in b.get("results", []):
                rows.append(("・".join(r.get("dimension_values", [])), int(r.get("value", 0))))
    return sorted(rows, key=lambda x: -x[1])


def main() -> None:
    me = get("me", fields="id,username")
    if "error" in me:
        sys.exit(f"アカウントを読めませんでした: {me['error']}")
    uid, name = me["id"], me.get("username", "")
    fc = get(f"{uid}/threads_insights", metric="followers_count")
    followers = next((i.get("total_value", {}).get("value") for i in fc.get("data", [])), None)
    today = datetime.now(JST).date().isoformat()
    lines = [f"# フォロワーの内訳 @{name}（{today}）", "", "アプリのインサイトの「フォロワー」のグラフの下にある利用者層データと同じ数字。閲覧者（フォロワー以外も含む）の内訳は API では取れない。", "", f"フォロワー数: {followers if followers is not None else '取れませんでした'}", ""]
    for kind, label in NAMES.items():
        rows = breakdown(uid, kind)
        lines += [f"## {label}", ""]
        if isinstance(rows, str):
            lines += [rows, ""]
            continue
        total = sum(v for _, v in rows) or 1
        lines += ["| | 人数 | 割合 |", "|---|---:|---:|"]
        for k, v in rows[:15]:
            k = GENDER.get(k, k) if kind == "gender" else k
            lines.append(f"| {k} | {v} | {v * 100 / total:.1f}% |")
        lines.append("")
    text = "\n".join(lines)
    print(text)
    out = Path("insights/利用者層") / f"{today}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
