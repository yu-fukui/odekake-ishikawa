"""アフィリエイト投稿（楽天トラベル・ふるさと納税）の数字を集めて、次に何を変えるかを決める（PDCA の C と A）。

代表 10/9「おでかけ福井・石川のアフィリエイト投稿を分析して、PDCAを回すサイクルを構築して。
重点は楽天トラベルと、ふるさと納税。」

やること（毎日、compose の前に動く）
  1. Threads API のアカウントのインサイト clicks（リンクごとのクリック数）を日ごとに取る
  2. リンクを posts/queue.jsonl の PR 投稿に結びつける。同じリンクを何度も使うので、
     その日のクリックは「その日までに出た、そのリンクを使った一番新しい投稿」に数える
  3. 閲覧（insights/metrics.jsonl）と合わせて、投稿ごと・切り口ごと・宿ごとにクリック率を出す
  4. 楽天の成果（neta/楽天の成果.jsonl。代表がレポートの画面を送ったら運用部が足す）があれば並べる
  5. insights/アフィリの成績.md（人が読む）と insights/アフィリの学び.json（compose が読む）を書く

クリックは Threads の中で押された回数。楽天側の「クリック」「売上」とは数え方が違う。
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

API = "https://graph.threads.net/v1.0"
JST = timezone(timedelta(hours=9))
見る日数 = int(os.environ.get("AFFI_DAYS", "28"))
QUEUE = Path("posts/queue.jsonl")
METRICS = Path("insights/metrics.jsonl")
成果 = Path("neta/楽天の成果.jsonl")
成績 = Path("insights/アフィリの成績.md")
学び = Path("insights/アフィリの学び.json")
クリックの記録 = Path("insights/アフィリのクリック.jsonl")
リンクの形 = re.compile(r"https?://(?:a\.r10\.to|hb\.afl\.rakuten\.co\.jp)/[^\s）)」]+")


def get(path: str, **params) -> dict:
    params["access_token"] = os.environ["THREADS_ACCESS_TOKEN"]
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as res:
            return json.load(res)
    except urllib.error.HTTPError as e:
        return {"error": e.read().decode()[:300]}


def 日ごとのクリック(uid: str, 今日: date) -> dict[str, dict[str, int]]:
    """{日付: {リンク: クリック数}}。取れなかった日は入れない。"""
    出: dict[str, dict[str, int]] = {}
    for i in range(1, 見る日数 + 1):
        d = 今日 - timedelta(days=i)
        since = int(datetime(d.year, d.month, d.day, tzinfo=JST).timestamp())
        r = get(f"{uid}/threads_insights", metric="clicks", since=since, until=since + 86400)
        if "error" in r:
            print(f"{d} のクリックを取れませんでした: {r['error']}")
            continue
        日 = {}
        for item in r.get("data", []):
            for v in item.get("link_total_values") or item.get("values") or []:
                u = v.get("link_url")
                if u:
                    日[u] = 日.get(u, 0) + int(v.get("value", 0))
        出[d.isoformat()] = 日
    return 出


def 種類(q: dict) -> str:
    n = q.get("note") or ""
    if "ふるさと納税" in n:
        return "ふるさと納税"
    if "宿" in n or "楽天トラベル" in n or "クーポン" in n:
        return "楽天トラベル"
    return "その他"


def 切り口(q: dict) -> str:
    n = q.get("note") or ""
    m = re.search(r"切り口(?:は|「)([^」。／]+)", n)
    if m:
        return m.group(1)
    m = re.search(r"／([^／]+に泊まる)／", n)
    return m.group(1) if m else ""


def 宿(q: dict) -> str:
    m = re.search(r"宿[:：]([^／。\s]+)", q.get("note") or "")
    return m.group(1) if m else ""


def main() -> None:
    今日 = datetime.now(JST).date()
    me = get("me", fields="id,username")
    if "error" in me:
        raise SystemExit(f"アカウントを読めませんでした: {me['error']}")
    名 = me.get("username", "")
    クリック = 日ごとのクリック(me["id"], 今日)
    with クリックの記録.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"取った日": 今日.isoformat(), "日ごと": クリック}, ensure_ascii=False) + "\n")

    # 投稿の閲覧（いちばん新しい測定）
    閲覧 = {}
    if METRICS.exists():
        for l in METRICS.read_text(encoding="utf-8").splitlines():
            if l.strip():
                r = json.loads(l)
                閲覧[r.get("id")] = r.get("views") or 0

    # PR 投稿
    投稿 = []
    for l in QUEUE.read_text(encoding="utf-8").splitlines():
        if not l.strip():
            continue
        q = json.loads(l)
        文 = q["text"] + "\n" + "\n".join(q.get("thread") or [])
        リンク = sorted(set(リンクの形.findall(文)))
        if not リンク:
            continue
        日 = q["scheduled_at"][:10]
        if 日 < (今日 - timedelta(days=見る日数)).isoformat() or 日 >= 今日.isoformat():
            continue
        投稿.append({"id": q["id"], "日": 日, "時": int(q["scheduled_at"][11:13]), "種類": 種類(q),
                   "切り口": 切り口(q), "宿": 宿(q), "1行目": q["text"].splitlines()[0][:40],
                   "リンク": リンク, "閲覧": 閲覧.get(q["id"], 0), "クリック": 0})
    投稿.sort(key=lambda p: (p["日"], p["時"]))

    # その日のクリックを、そのリンクを使った一番新しい投稿に数える
    for 日, 回 in クリック.items():
        for u, c in 回.items():
            候補 = [p for p in 投稿 if u in p["リンク"] and p["日"] <= 日]
            if 候補:
                候補[-1]["クリック"] += c

    def まとめ(キー) -> list[dict]:
        g = defaultdict(lambda: {"本数": 0, "閲覧": 0, "クリック": 0})
        for p in 投稿:
            k = キー(p)
            if not k:
                continue
            g[k]["本数"] += 1
            g[k]["閲覧"] += p["閲覧"]
            g[k]["クリック"] += p["クリック"]
        out = []
        for k, v in g.items():
            v["名"] = k
            v["クリック率"] = round(v["クリック"] * 100 / v["閲覧"], 2) if v["閲覧"] else 0
            out.append(v)
        return sorted(out, key=lambda v: (-v["クリック率"], -v["クリック"]))

    種類別 = まとめ(lambda p: p["種類"])
    切り口別 = {t: まとめ(lambda p, t=t: p["切り口"] if p["種類"] == t else "") for t in ("楽天トラベル", "ふるさと納税")}
    宿別 = まとめ(lambda p: p["宿"])
    時刻別 = まとめ(lambda p: f"{p['時']}時")
    クリックが取れた = any(クリック.values())

    # 楽天の成果（代表のレポート画面から）
    成果行 = []
    if 成果.exists():
        成果行 = [json.loads(l) for l in 成果.read_text(encoding="utf-8").splitlines() if l.strip()]

    行 = [f"# アフィリエイト投稿の成績 @{名}（{今日}・直近{見る日数}日）", "",
          "クリックは Threads の中でリンクが押された回数（API）。同じリンクを何度も使うため、"
          "その日のクリックは、その日までに出た一番新しい投稿に数えている。", ""]
    if not クリックが取れた:
        行 += ["**リンクのクリック数がまだ取れていません。** 表のクリックは 0 になっています。", ""]
    行 += ["## 種類ごと", "", "| 種類 | 本数 | 閲覧 | クリック | クリック率 |", "|---|---:|---:|---:|---:|"]
    行 += [f"| {v['名']} | {v['本数']} | {v['閲覧']:,} | {v['クリック']} | {v['クリック率']}% |" for v in 種類別]
    for t, rows in 切り口別.items():
        行 += ["", f"## {t}：切り口ごと", "", "| 切り口 | 本数 | 閲覧 | クリック | クリック率 |", "|---|---:|---:|---:|---:|"]
        行 += [f"| {v['名']} | {v['本数']} | {v['閲覧']:,} | {v['クリック']} | {v['クリック率']}% |" for v in rows] or ["| （まだなし） | | | | |"]
    if 宿別:
        行 += ["", "## 楽天トラベル：宿ごと", "", "| 宿 | 本数 | 閲覧 | クリック | クリック率 |", "|---|---:|---:|---:|---:|"]
        行 += [f"| {v['名']} | {v['本数']} | {v['閲覧']:,} | {v['クリック']} | {v['クリック率']}% |" for v in 宿別]
    行 += ["", "## 時刻ごと", "", "| 時刻 | 本数 | 閲覧 | クリック | クリック率 |", "|---|---:|---:|---:|---:|"]
    行 += [f"| {v['名']} | {v['本数']} | {v['閲覧']:,} | {v['クリック']} | {v['クリック率']}% |" for v in 時刻別]
    行 += ["", "## 投稿ごと", "", "| 日 | 時 | 種類 | 切り口・宿 | 閲覧 | クリック | 1行目 |", "|---|---:|---|---|---:|---:|---|"]
    行 += [f"| {p['日'][5:]} | {p['時']} | {p['種類']} | {p['切り口'] or p['宿']} | {p['閲覧']:,} | {p['クリック']} | {p['1行目']} |" for p in 投稿]
    行 += ["", "## 楽天の成果（代表が送ったレポートから）", ""]
    if 成果行:
        行 += ["| 期間 | 種類 | クリック | 件数 | 報酬 |", "|---|---|---:|---:|---:|"]
        行 += [f"| {r.get('期間','')} | {r.get('種類','')} | {r.get('クリック','')} | {r.get('件数','')} | {r.get('報酬','')} |" for r in 成果行[-20:]]
    else:
        行 += ["まだありません。楽天アフィリエイトの成果レポートの画面を代表から受け取ったら、neta/楽天の成果.jsonl に足す。"]
    文 = "\n".join(行) + "\n"
    print(文)
    成績.write_text(文, encoding="utf-8")

    # 次の投稿が読む学び。数字が少ないうちは何も決めない（本数3以上・閲覧1000以上の切り口だけ）
    def 確か(rows):
        return [v for v in rows if v["本数"] >= 3 and v["閲覧"] >= 1000]
    学びの中身 = {"更新": 今日.isoformat(), "クリックが取れた": クリックが取れた}
    if クリックが取れた:
        for t, rows in 切り口別.items():
            ok = 確か(rows)
            if len(ok) >= 2:
                学びの中身[t] = {"よく押された切り口": [v["名"] for v in ok[:2]],
                             "押されなかった切り口": [v["名"] for v in ok[-2:] if v["クリック率"] < ok[0]["クリック率"] / 2]}
    学び.write_text(json.dumps(学びの中身, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
