"""ネタ帳から、おでかけサイト用のデータを作る。

neta/ネタ帳.md の「書き足す場所」にある箇条書きを 1 件ずつ読み、
日付・エリア・種類・出典を取り出して docs/data.json に書き出す。
サイト（docs/index.html）はこの JSON を読むだけ。

    python scripts/おでかけ.py            # 書き出す
    python scripts/おでかけ.py --thumbs   # 出典ページのリンクカード（OG）も取りに行く
    python scripts/おでかけ.py --check    # 書き出さずに件数だけ見る

日付の読み取りは本文の書き方に頼った推測なので、読めなかったものは
「日付なし」として残す（捨てない）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from html import unescape
from pathlib import Path
from urllib.parse import urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

import 地域  # noqa: E402  どの県のおでかけか（region.json）

KEN = 地域.県名
SITE_NAME = 地域.地域["サイト名"]
ACCOUNT = 地域.地域["アカウント"]
NETA = ROOT / "neta" / "ネタ帳.md"
OUT = ROOT / "docs" / "data.json"

# 地名・施設名 → 市町。本文の中でいちばん前に出てくるものを採る（region.json の「地名」）。
AREAS = [tuple(x) for x in 地域.地域["地名"]]

SPOT_WORDS = ("オープン", "開業", "新店", "グランドオープン", "リニューアル", "出店")
EVENT_WORDS = ("開催", "まつり", "祭", "フェス", "展", "イベント", "マルシェ",
               "フェア", "ツアー", "体験", "募集", "公開", "ライブ", "開館")

_MD = r"(\d{1,2})/(\d{1,2})(?:\s*\([月火水木金土日祝・]+\))?"
# 9/26-10/12、10/3-4、9/10〜11/3 のような期間
RANGE = re.compile(_MD + r"\s*[-〜～－–]\s*(?:(\d{1,2})/)?(\d{1,2})(?!\d)")
SINGLE = re.compile(_MD)
JP_DATE = re.compile(r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日")


def _year_for(month: int, base: date) -> int:
    """書いた日から見て、月だけの日付が何年のことかを決める。"""
    if month - base.month > 6:
        return base.year - 1
    if base.month - month > 6:
        return base.year + 1
    return base.year


def _mk(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def parse_dates(text: str, base: date) -> tuple[date | None, date | None]:
    """本文から (始まり, 終わり) を読む。読めなければ (None, None)。"""
    found: list[date] = []

    m = RANGE.search(text)
    if m:
        m1, d1, m2, d2 = m.group(1), m.group(2), m.group(3), m.group(4)
        start = _mk(_year_for(int(m1), base), int(m1), int(d1))
        end_month = int(m2) if m2 else int(m1)
        end = _mk(_year_for(end_month, base), end_month, int(d2))
        if start and end and end < start:
            end = _mk(end.year + 1, end.month, end.day)
        if start and end:
            return start, end

    for m in SINGLE.finditer(text):
        dt = _mk(_year_for(int(m.group(1)), base), int(m.group(1)), int(m.group(2)))
        if dt:
            found.append(dt)
    if not found:
        for m in JP_DATE.finditer(text):
            y = int(m.group(1)) if m.group(1) else _year_for(int(m.group(2)), base)
            dt = _mk(y, int(m.group(2)), int(m.group(3)))
            if dt:
                found.append(dt)
    if not found:
        return None, None
    # 9/25・10/2・10/9 のように並んでいるときは、最初から最後までを期間とみなす
    return min(found), max(found)


def guess_area(text: str) -> str | None:
    best: tuple[int, int, str] | None = None
    for word, area in AREAS:
        pos = text.find(word)
        if pos < 0:
            continue
        # 前に出てくるもの、同じ位置なら長い語（南越前町 > 越前町）を採る
        key = (pos, -len(word), area)
        if best is None or key < best:
            best = key
    return best[2] if best else None


def guess_kind(text: str) -> str:
    head = text[:80]
    if any(w in head for w in SPOT_WORDS) and not re.search(r"開催|まつり|フェス", head):
        return "スポット"
    if any(w in text for w in EVENT_WORDS):
        return "催し"
    return "話題"


# ネタ帳の行末に付く公式 Instagram の印。収集はアカウントの URL を、人が手で足すときは
# 投稿やリールの URL を入れてもよい（サイトでは投稿を公式の埋め込みで出す）。
INSTAGRAM_MARK = re.compile(r"\s*［Instagram:\s*(https?://(?:www\.)?instagram\.com/[^\s］]+)\s*］")


def split_instagram(line: str) -> tuple[str, str | None]:
    """行から［Instagram: URL］を外し、(残りの行, URL) を返す。URL の ? 以降は捨てる。"""
    m = INSTAGRAM_MARK.search(line)
    if not m:
        return line, None
    url = m.group(1).split("?")[0]
    # アプリの共有で付く /reels/（複数形）は、埋め込みに使える /reel/ にそろえる
    url = re.sub(r"instagram\.com/reels/", "instagram.com/reel/", url)
    return (line[: m.start()] + line[m.end():]).strip(), url


def split_note(line: str) -> tuple[str, str]:
    """「本文（→ ひとこと）」を本文とひとことに分ける。"""
    m = re.search(r"（→\s*(.+?)）\s*$", line)
    if not m:
        return line.strip(), ""
    return line[: m.start()].strip(), m.group(1).strip()


def parse_neta(md: str) -> list[dict]:
    """ネタ帳の「書き足す場所」から項目を取り出す。"""
    items: list[dict] = []
    section = md.split("## 書き足す場所", 1)
    if len(section) < 2:
        return items
    body = re.split(r"\n## ", section[1], maxsplit=1)[0]

    for block in re.split(r"\n(?=### )", body):
        head = re.match(r"### (\d{4})-(\d{2})-(\d{2})(.*)", block)
        if not head:
            continue
        written = date(int(head.group(1)), int(head.group(2)), int(head.group(3)))
        # 「### 2026-10-01（代表が見つけた催し）」のように、代表が手で足した欄はおすすめとして目立たせる
        pick = "代表" in head.group(4)
        main, _, rest = block.partition("<details>")
        bullets = [ln[2:].strip() for ln in main.splitlines() if ln.startswith("- ")]
        sources = re.findall(r"^- (https?://\S+)", rest, flags=re.M)
        # 出典は箇条書きと同じ順に並べる決まり。数が合わないときは結び付けない。
        paired = len(sources) == len(bullets)

        for i, line in enumerate(bullets):
            line, instagram = split_instagram(line)
            text, note = split_note(line)
            start, end = parse_dates(text, written)
            items.append({
                "text": text,
                "note": note,
                "kind": guess_kind(text),
                "area": guess_area(text),
                "start": start.isoformat() if start else None,
                "end": end.isoformat() if end else None,
                "source": sources[i] if paired else None,
                "instagram": instagram,
                "written": written.isoformat(),
                "pick": pick,
            })

    # 同じ本文が二度入っていたら、新しく書いたほうを残す
    seen: set[str] = set()
    unique = []
    for it in sorted(items, key=lambda x: x["written"], reverse=True):
        if it["text"] in seen:
            continue
        seen.add(it["text"])
        unique.append(it)
    return unique


def build(md: str) -> dict:
    items = parse_neta(md)
    for it in items:
        it["region"] = 地域.地区(it["area"])
    # 実行日ではなくネタの最新日にする（中身が同じなら毎日コミットが出ないように）
    updated = max((it["written"] for it in items), default=None)
    return {"updated": updated, "items": items}


# リンクカード（OG）を取りに行かない出典。Instagram などは画像 URL が期限付きですぐ切れ、
# ログインを求められて中身も取れない。サイト名だけのカードにする。
NO_OG_HOSTS = ("instagram.com", "facebook.com", "x.com", "twitter.com", "threads.com", "threads.net")

# サイト共通のロゴや既定の OG 画像。どの記事にも同じ絵が出るだけなので使わない。
# WordPress の uploads に置かれたものは記事ごとの画像のことが多いので、ロゴ類だけ除く。
GENERIC_IMAGE = re.compile(r"logo|cropped-|site[-_]?icon|no[-_]?image", re.I)
GENERIC_OUTSIDE_UPLOADS = re.compile(r"ogp|og[-_]?im(age|g)|fb_ogp|/shared/|/common/|default", re.I)

OG_TITLE_MAX = 80
OG_DESC_MAX = 100


def is_generic_image(path: str) -> bool:
    if GENERIC_IMAGE.search(path):
        return True
    return "/uploads/" not in path and bool(GENERIC_OUTSIDE_UPLOADS.search(path))


_META = re.compile(r"<meta\b[^>]*>", re.I)
_ATTR = re.compile(r"""([a-zA-Z:_-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)""")
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


def _metas(html: str) -> dict[str, str]:
    found: dict[str, str] = {}
    for tag in _META.findall(html):
        attrs = {k.lower(): v.strip("\"'") for k, v in _ATTR.findall(tag)}
        key = (attrs.get("property") or attrs.get("name") or "").lower()
        if key and attrs.get("content", "").strip():
            found.setdefault(key, unescape(attrs["content"]).strip())
    return found


def _clip(text: str | None, limit: int) -> str | None:
    if not text:
        return None
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def find_og_image(html: str, page_url: str, metas: dict[str, str] | None = None) -> str | None:
    """HTML から og:image（なければ twitter:image）を取り出し、絶対 URL にする。"""
    metas = _metas(html) if metas is None else metas
    for key in ("og:image:secure_url", "og:image", "og:image:url", "twitter:image"):
        if key in metas:
            url = urljoin(page_url, metas[key])
            if not url.startswith(("https://", "http://")):
                return None
            return None if is_generic_image(urlparse(url).path) else url
    return None


def parse_og(html: str, page_url: str) -> dict | None:
    """リンクカードに出すもの（画像・タイトル・説明・サイト名）を取り出す。

    SNS のリンクプレビューと同じく、出典ページが共有用に用意した情報だけを使う。
    説明は長くなりすぎないように切る。何も取れなければ None。
    """
    metas = _metas(html)
    title = metas.get("og:title") or metas.get("twitter:title")
    if not title:
        m = _TITLE.search(html)
        title = unescape(m.group(1)) if m else None
    og = {
        "image": find_og_image(html, page_url, metas),
        "title": _clip(title, OG_TITLE_MAX),
        "description": _clip(metas.get("og:description") or metas.get("description")
                             or metas.get("twitter:description"), OG_DESC_MAX),
        "site": _clip(metas.get("og:site_name"), 40),
    }
    return og if any(og.values()) else None


def fetch_html(url: str, timeout: float = 15) -> str:
    req = urllib.request.Request(url, headers={
        # 独自の UA だと 403 を返す自治体サイトがあるので、ふつうのブラウザを名乗る
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36",
        "Accept-Language": "ja",
    })
    with urllib.request.urlopen(req, timeout=timeout) as res:
        # 頭の部分に meta があるので、全部は読まない
        raw = res.read(400_000)
        charset = res.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, errors="replace")


def attach_og(items: list[dict], previous: dict[str, dict | None], fetch=fetch_html) -> int:
    """各項目に出典のリンクカード情報（og）を付ける。前回取れたもの（取れなかったものも）は使い回す。

    previous は 出典 URL → og（取れなかったら None）。通信に失敗したものは
    覚えずに次回また試す。新しく取りに行った件数を返す。
    """
    fetched = 0
    for it in items:
        src = it.get("source")
        if not src:
            it["og"] = None
            continue
        if src in previous:
            it["og"] = previous[src]
            continue
        host = urlparse(src).hostname or ""
        if any(host == h or host.endswith("." + h) for h in NO_OG_HOSTS):
            it["og"] = None
            continue
        try:
            it["og"] = parse_og(fetch(src), src)
        except urllib.error.HTTPError as e:
            if not 400 <= e.code < 500:
                print(f"  リンクカードの取得に失敗（次回また試す）: {src} — {e}", file=sys.stderr)
                continue
            # ページが無い・断られた：何度試しても同じなので、無しとして覚える
            print(f"  リンクカードなし（{e.code}）: {src}", file=sys.stderr)
            it["og"] = None
        except Exception as e:  # noqa: BLE001 — 1 件の失敗で全体を止めない
            print(f"  リンクカードの取得に失敗（次回また試す）: {src} — {e}", file=sys.stderr)
            continue  # og キーを付けない = 覚えない
        previous[src] = it["og"]
        fetched += 1
    return fetched


def load_previous_og(path: Path) -> dict[str, dict | None]:
    try:
        old = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {it["source"]: it["og"] for it in old.get("items", [])
            if it.get("source") and "og" in it}


# ---- 検索エンジン向けの静的な中身（2026-10-04 SEO 第1段階） ----
# サイトは JavaScript で描くので、HTML だけを読む検索エンジンには中身が見えない。
# index.html の目印のあいだに、これからの催しと新しい場所を素の HTML で書き込む
# （ブラウザでは JavaScript がこの部分を描き直す）。sitemap.xml もここで作る。
SITE = 地域.サイトURL
INDEX = ROOT / "docs" / "index.html"
SITEMAP = ROOT / "docs" / "sitemap.xml"
MARK_START, MARK_END = "<!--prerender:start-->", "<!--prerender:end-->"
WD = "月火水木金土日"


def _title(it: dict) -> str:
    m = re.search(r"「([^」]{2,40})」", it["text"])
    if m:
        return m.group(1)
    # 「」が無いときは、先頭の日付（10/3-4 など）を外した最初の一文
    head = re.sub(r"^\s*" + _MD + r"(?:\s*[-〜～－–]\s*(?:\d{1,2}/)?\d{1,2})?\s*", "", it["text"])
    return head.split("。")[0][:44]


def _when(it: dict) -> str:
    if not it.get("start"):
        return ""
    s = date.fromisoformat(it["start"])
    e = date.fromisoformat(it["end"]) if it.get("end") else s
    f = lambda d: f"{d.month}月{d.day}日（{WD[d.weekday()]}）"
    if it["kind"] == "スポット":
        return f"{s.year}年{s.month}月{s.day}日オープン"
    return f(s) if s == e else f"{f(s)}〜{f(e)}"


def _card(it: dict) -> str:
    from html import escape as h
    meta = "・".join(x for x in (_when(it), it.get("area") or "") if x)
    out = [f'<article><h3>{h(_title(it))}</h3>']
    if meta:
        out.append(f"<p>{h(meta)}</p>")
    out.append(f"<p>{h(it['text'])}</p>")
    if it.get("note"):
        out.append(f"<p>{h(it['note'])}</p>")
    if it.get("source"):
        out.append(f'<p><a href="{h(it["source"])}" rel="noopener">出典</a></p>')
    out.append("</article>")
    return "".join(out)


def prerender(data: dict, today: date, limit_events: int = 40, limit_spots: int = 15) -> str:
    items = data["items"]
    alive = lambda it: it.get("end") and date.fromisoformat(it["end"]) >= today
    events = sorted((it for it in items if it["kind"] == "催し" and alive(it)), key=lambda it: it["start"])
    spots = sorted((it for it in items if it["kind"] == "スポット" and it.get("start")),
                   key=lambda it: it["start"], reverse=True)
    parts = [MARK_START, '<div class="prerender">']
    parts.append(f"<h2>開催中・これからの{KEN}のイベント（{today.month}月{today.day}日時点）</h2>")
    parts += [_card(it) for it in events[:limit_events]]
    parts.append(f"<h2>{KEN}に新しくできたお店・スポット</h2>")
    parts += [_card(it) for it in spots[:limit_spots]]
    parts.append("</div>")
    parts.append(MARK_END)
    return "\n".join(parts)


# ---- 月別ページ（2026-10-04 SEO 第2段階） ----
# 「福井 イベント 10月」のような検索を受けるページ。中身の薄いページを量産しないよう、
# 今月以降で、短い催しが MONTH_MIN 件以上ある月だけ作る。過ぎた月のページは消す。
MONTH_DIR = ROOT / "docs" / "month"
MONTH_MIN = 5
MONTHS_MARK = ("<!--months:start-->", "<!--months:end-->")
_CF = 地域.地域.get("Cloudflare", "")
_GA = 地域.地域.get("GA", "")
CF_BEACON = ((('<script type="module" src="https://static.cloudflareinsights.com/beacon.min.js" '
              f'data-cf-beacon=\'{{"token": "{_CF}"}}\'></script>') if _CF else "")
             + ((f'<script async src="https://www.googletagmanager.com/gtag/js?id={_GA}"></script>'
                 '<script>window.dataLayer=window.dataLayer||[];function gtag(){dataLayer.push(arguments);}'
                 f'gtag("js",new Date());gtag("config","{_GA}");</script>') if _GA else ""))
# 月ごとの冒頭の一言（手で書いたもの。年をまたいでも使える季節の話だけにする）
MONTH_NOTE = {
    1: "雪の季節。屋内の展示や、冬ならではの味覚の催しが中心になります。",
    2: "まだ雪の残る時期。冬の祭りや、春を待つ催しが少しずつ始まります。",
    3: "雪がとけて、外の催しが戻ってくる季節。年度替わりの新しいお店も増えます。",
    4: "桜の季節。足羽川の桜並木をはじめ、花見に合わせた催しが県内各地で開かれます。",
    5: "新緑の行楽シーズン。連休のイベントや、外で楽しむマルシェが増えます。",
    6: "梅雨入りの時期。屋内の展示や、初夏の味覚を楽しむ催しが中心になります。",
    7: "夏祭りと海開きの季節。花火大会や海辺のイベントが続きます。",
    8: "夏祭り・花火大会の本番。お盆の帰省に合わせた催しも多い月です。",
    9: "暑さがやわらぎ、秋の祭りやフェスが増えてくる季節です。",
    10: "行楽の秋。新そばや秋の味覚の催し、音楽フェスやマルシェが週末ごとに続きます。",
    11: "紅葉と冬の味覚の季節。越前がにの解禁を待つ時期で、食の催しが増えます。",
    12: "冬のはじまり。クリスマスや年末の催し、イルミネーションが中心になります。",
}
# 県ごとの一言があれば、そちらを使う（region.json の「月の一言」。キーは "1"〜"12"）
MONTH_NOTE.update({int(k): v for k, v in 地域.地域.get("月の一言", {}).items()})


def _span(it: dict) -> tuple[date, date] | None:
    if it["kind"] != "催し" or not it.get("start"):
        return None
    s = date.fromisoformat(it["start"])
    return s, date.fromisoformat(it["end"]) if it.get("end") else s


def _is_long(sp: tuple[date, date]) -> bool:
    return (sp[1] - sp[0]).days >= 7


def month_pages(data: dict, today: date) -> dict[str, dict]:
    """作る月 → {"short": [...], "long": [...]}。今月以降で短い催しが MONTH_MIN 件以上の月だけ。"""
    out: dict[str, dict] = {}
    for it in data["items"]:
        sp = _span(it)
        if not sp:
            continue
        y, m = sp[0].year, sp[0].month
        while (y, m) <= (sp[1].year, sp[1].month):
            if (y, m) >= (today.year, today.month):
                b = out.setdefault(f"{y}-{m:02d}", {"short": [], "long": []})
                b["long" if _is_long(sp) else "short"].append(it)
            y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return {k: v for k, v in sorted(out.items()) if len(v["short"]) >= MONTH_MIN}


def _month_label(key: str) -> str:
    y, m = key.split("-")
    return f"{int(y)}年{int(m)}月"


def render_month(key: str, b: dict, keys: list[str], today: date) -> str:
    from collections import Counter
    from html import escape as h
    y, m = map(int, key.split("-"))
    label = _month_label(key)
    first = date(y, m, 1)
    short = sorted(b["short"], key=lambda it: (it["start"], _title(it)))
    long_ = sorted(b["long"], key=lambda it: it["end"])
    areas = Counter(it.get("area") for it in short + long_ if it.get("area")).most_common(3)
    area_txt = "、".join(f"{a}（{n}件）" for a, n in areas)
    url = f"{SITE}/month/{key}/"
    title = f"{KEN}のイベント {label}｜{SITE_NAME}"
    desc = (f"{label}に{KEN}県内で開かれるイベント・祭り・マルシェ・展示を日付順にまとめました。"
            f"全{len(short) + len(long_)}件、主催者や自治体などの出典つき。")

    def card(it: dict) -> str:
        sp = _span(it)
        ended = sp[1] < today
        meta = "・".join(x for x in (_when(it), it.get("area") or "") if x)
        site = (it.get("og") or {}).get("site") or "出典"
        cls = ' class="ended"' if ended else ""
        mark = "<small>終了</small>" if ended else ""
        out = [f"<article{cls}><h3>{h(_title(it))}{mark}</h3>"]
        if meta:
            out.append(f'<p class="meta">{h(meta)}</p>')
        out.append(f"<p>{h(it['text'])}</p>")
        if it.get("note"):
            out.append(f'<p class="note">{h(it["note"])}</p>')
        if it.get("source"):
            out.append(f'<p class="src"><a href="{h(it["source"])}" rel="noopener" target="_blank">{h(site)}で詳しく見る</a></p>')
        out.append("</article>")
        return "".join(out)

    body = []
    days: dict[date, list] = {}
    for it in short:
        days.setdefault(max(_span(it)[0], first), []).append(it)
    for d, its in days.items():
        head = f"{d.month}月{d.day}日（{WD[d.weekday()]}）"
        if d == first and any(_span(i)[0] < first for i in its):
            head += "〜 先月から続く催しを含む"
        body.append(f"<h2>{h(head)}</h2>" + "".join(card(i) for i in its))
    if long_:
        body.append(f"<h2>{m}月の期間中に行ける展示・フェア</h2>"
                    "<p class=\"muted\">1週間以上つづく催しです。期間中の好きな日に行けます。</p>"
                    + "".join(card(i) for i in long_))
    nav = " ・ ".join(f'<a href="../{k}/">{_month_label(k)}</a>' if k != key else f"<b>{_month_label(k)}</b>" for k in keys)
    ld = json.dumps({"@context": "https://schema.org", "@type": "BreadcrumbList", "itemListElement": [
        {"@type": "ListItem", "position": 1, "name": SITE_NAME, "item": f"{SITE}/"},
        {"@type": "ListItem", "position": 2, "name": f"{KEN}のイベント {label}", "item": url}]}, ensure_ascii=False)
    return f"""<!doctype html>
<html lang="ja">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{h(title)}</title>
<meta name="description" content="{h(desc)}">
<link rel="canonical" href="{url}">
<meta property="og:type" content="article">
<meta property="og:title" content="{h(title)}">
<meta property="og:description" content="{h(desc)}">
<meta property="og:url" content="{url}">
<meta property="og:site_name" content="{h(SITE_NAME)}">
<meta property="og:locale" content="ja_JP">
<meta property="og:image" content="{SITE}/icon-192.png">
<link rel="icon" href="../../favicon.ico" sizes="any">
<link rel="apple-touch-icon" href="../../apple-touch-icon.png">
<meta name="theme-color" content="#1f7a57">
<script type="application/ld+json">{ld}</script>
<style>
:root {{ --bg: #f5f1e8; --surface: #fff; --ink: #22302a; --muted: #6d7570; --main: #1f7a57; --line: #e4ddcf; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #141815; --surface: #1d231f; --ink: #e8eee9; --muted: #a2aba5; --main: #4cc292; --line: #2e3631; }} }}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font-family: "Hiragino Sans", "Noto Sans JP", system-ui, sans-serif; line-height: 1.8; }}
header {{ background: #1f7a57; color: #fff; padding: 14px 16px; }}
header a {{ color: #fff; text-decoration: none; font-weight: 700; }}
main {{ max-width: 760px; margin: 0 auto; padding: 24px 16px 48px; }}
h1 {{ font-size: 1.5rem; margin: 0 0 8px; }}
h2 {{ font-size: 1.05rem; margin: 30px 0 10px; padding-bottom: 6px; border-bottom: 2px dashed var(--line); }}
article {{ background: var(--surface); border-radius: 14px; padding: 12px 16px; margin: 10px 0; }}
article h3 {{ font-size: 1rem; margin: 0 0 4px; }}
article h3 small {{ margin-left: 8px; font-size: .72rem; color: var(--muted); border: 1px solid var(--line); border-radius: 99px; padding: 1px 8px; }}
article.ended {{ opacity: .6; }}
article p {{ margin: 4px 0; font-size: .92rem; }}
.meta {{ color: var(--main); font-weight: 700; font-size: .85rem; }}
.note, .muted {{ color: var(--muted); font-size: .85rem; }}
.src a {{ font-size: .85rem; }}
a {{ color: var(--main); }}
.lead {{ background: var(--surface); border-radius: 14px; padding: 12px 16px; }}
nav.months {{ margin-top: 36px; font-size: .9rem; }}
footer {{ color: var(--muted); font-size: .78rem; text-align: center; padding: 0 16px 32px; }}
</style>
</head>
<body>
<header><a href="../../">← {h(SITE_NAME)}</a></header>
<main>
<h1>{h(KEN)}のイベント {h(label)}</h1>
<div class="lead">
<p>{h(MONTH_NOTE[m])}</p>
<p>{h(label)}に{h(KEN)}県内で開かれるイベントを、日付順にまとめました。1日〜数日の催しが{len(short)}件、期間中いつでも行ける展示・フェアが{len(long_)}件です。{h(f"多いのは{area_txt}。") if area_txt else ""}</p>
<p class="muted">日付・内容は集めた時点のものです。お出かけ前に、出典（主催者・自治体などの公式）で必ず確かめてください。最終更新：{today.year}年{today.month}月{today.day}日</p>
</div>
{"".join(body)}
<nav class="months">月別：{nav}</nav>
<p><a href="../../">今週末のイベント・新しいお店を見る（{h(SITE_NAME)}）</a></p>
</main>
<footer>Threads <a href="https://www.threads.com/@{h(ACCOUNT)}" rel="noopener">@{h(ACCOUNT)}</a> が集めた{h(KEN)}の情報をまとめています。<br><a href="../../about/">運営者情報・編集方針・プライバシー</a></footer>
{CF_BEACON}
</body>
</html>
"""


def write_months(data: dict, today: date) -> list[str]:
    pages = month_pages(data, today)
    keys = list(pages)
    if MONTH_DIR.exists():
        for d in MONTH_DIR.iterdir():
            if d.is_dir() and d.name not in pages:
                for f in d.iterdir():
                    f.unlink()
                d.rmdir()
    for k, b in pages.items():
        (MONTH_DIR / k).mkdir(parents=True, exist_ok=True)
        (MONTH_DIR / k / "index.html").write_text(render_month(k, b, keys, today), encoding="utf-8")
    return keys


def write_static(data: dict, today: date) -> None:
    months = write_months(data, today)
    html_text = INDEX.read_text(encoding="utf-8")
    a, b = html_text.find(MARK_START), html_text.find(MARK_END)
    if a < 0 or b < 0:
        print("::warning::index.html に prerender の目印がありません。静的な中身は書きません。")
    else:
        html_text = html_text[:a] + prerender(data, today) + html_text[b + len(MARK_END):]
    ma, mb = html_text.find(MONTHS_MARK[0]), html_text.find(MONTHS_MARK[1])
    if ma >= 0 and mb >= 0:
        links = "".join(f'<a href="./month/{k}/">{_month_label(k)}のイベント</a><br>' for k in months)
        html_text = html_text[:ma] + MONTHS_MARK[0] + links + html_text[mb:]
    INDEX.write_text(html_text, encoding="utf-8")
    lastmod = data.get("updated") or today.isoformat()
    urls = [(f"{SITE}/", lastmod, "daily"), (f"{SITE}/about/", "2026-10-04", "monthly")]
    urls += [(f"{SITE}/month/{k}/", lastmod, "daily") for k in months]
    SITEMAP.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n'
        + "".join(f"  <url><loc>{u}</loc><lastmod>{m}</lastmod><changefreq>{c}</changefreq></url>\n" for u, m, c in urls)
        + "</urlset>\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="書き出さずに件数だけ表示する")
    ap.add_argument("--thumbs", action="store_true", help="出典ページのリンクカード（OG）を取りに行く")
    args = ap.parse_args(argv)

    data = build(NETA.read_text(encoding="utf-8"))
    items = data["items"]
    previous = load_previous_og(OUT)
    if args.thumbs:
        n = attach_og(items, previous)
        print(f"リンクカード: 新しく {n} 件を取りに行った")
    else:
        # 取りに行かないときも、前回取れたものは残す
        for it in items:
            if it.get("source") in previous:
                it["og"] = previous[it["source"]]
    with_og = [i for i in items if i.get("og")]
    print(f"リンクカードあり {len(with_og)} 件（うち画像あり {sum(1 for i in with_og if i['og'].get('image'))} 件）")
    counts = {k: sum(1 for i in items if i["kind"] == k) for k in ("催し", "スポット", "話題")}
    undated = sum(1 for i in items if not i["start"])
    print(f"{len(items)} 件（{'・'.join(f'{k}{v}' for k, v in counts.items())}、日付なし {undated}）")

    if not args.check:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        print(f"→ {OUT.relative_to(ROOT)}")
        # 日本時間の今日で、終わった催しを外す
        from datetime import datetime, timedelta, timezone
        write_static(data, datetime.now(timezone(timedelta(hours=9))).date())
        print("→ docs/index.html（静的な中身）・docs/month/（月別ページ）・docs/sitemap.xml")
    return 0


if __name__ == "__main__":
    sys.exit(main())
