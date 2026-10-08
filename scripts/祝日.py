"""日本の祝日と連休を調べる（翌日ぶんの投稿文に「祝日か・連休の何日目か」を入れるため）。

運用部・石川担当の依頼（2026-10-09）：「10/12（月）はスポーツの日。10/10〜12は3連休」のような情報が
投稿文づくりに渡っていなかった。祝日は内閣府の祝日CSV（syukujitsu.csv）を読み、読めないときは下の表を使う。
表は 2026・2027 年ぶん（内閣府CSVから写した）。2028 年以降は CSV から読む。
"""

from __future__ import annotations

import urllib.request
from datetime import date, timedelta

CSV = "https://www8.cao.go.jp/chosei/shukujitsu/syukujitsu.csv"

表 = {
    "2026-01-01": "元日",
    "2026-01-12": "成人の日",
    "2026-02-11": "建国記念の日",
    "2026-02-23": "天皇誕生日",
    "2026-03-20": "春分の日",
    "2026-04-29": "昭和の日",
    "2026-05-03": "憲法記念日",
    "2026-05-04": "みどりの日",
    "2026-05-05": "こどもの日",
    "2026-05-06": "休日",
    "2026-07-20": "海の日",
    "2026-08-11": "山の日",
    "2026-09-21": "敬老の日",
    "2026-09-22": "休日",
    "2026-09-23": "秋分の日",
    "2026-10-12": "スポーツの日",
    "2026-11-03": "文化の日",
    "2026-11-23": "勤労感謝の日",
    "2027-01-01": "元日",
    "2027-01-11": "成人の日",
    "2027-02-11": "建国記念の日",
    "2027-02-23": "天皇誕生日",
    "2027-03-21": "春分の日",
    "2027-03-22": "休日",
    "2027-04-29": "昭和の日",
    "2027-05-03": "憲法記念日",
    "2027-05-04": "みどりの日",
    "2027-05-05": "こどもの日",
    "2027-07-19": "海の日",
    "2027-08-11": "山の日",
    "2027-09-20": "敬老の日",
    "2027-09-23": "秋分の日",
    "2027-10-11": "スポーツの日",
    "2027-11-03": "文化の日",
    "2027-11-23": "勤労感謝の日",
}

_読んだ: dict[str, str] | None = None


def 祝日たち() -> dict[str, str]:
    global _読んだ
    if _読んだ is not None:
        return _読んだ
    出 = dict(表)
    try:
        with urllib.request.urlopen(CSV, timeout=15) as r:
            文 = r.read().decode("shift_jis", errors="replace")
        for 行 in 文.splitlines()[1:]:
            if "," not in 行:
                continue
            日, 名 = 行.split(",", 1)
            y, m, d = 日.strip().split("/")
            出[f"{int(y):04d}-{int(m):02d}-{int(d):02d}"] = 名.strip()
    except Exception as exc:  # noqa: BLE001
        print(f"祝日CSVを読めませんでした（{exc}）。手元の表を使います。")
    _読んだ = 出
    return 出


def 祝日の名前(日: date) -> str | None:
    return 祝日たち().get(日.isoformat())


def 休み(日: date) -> bool:
    return 日.weekday() >= 5 or 祝日の名前(日) is not None


def 連休(日: date) -> tuple[date, date] | None:
    """日が3日以上続く休みの中なら、その最初と最後の日。"""
    if not 休み(日):
        return None
    始 = 終 = 日
    while 休み(始 - timedelta(days=1)):
        始 -= timedelta(days=1)
    while 休み(終 + timedelta(days=1)):
        終 += timedelta(days=1)
    return (始, 終) if (終 - 始).days >= 2 else None


def 説明(日: date) -> str:
    """投稿文づくりに渡す一言。平日なら空。"""
    曜 = "月火水木金土日"
    名 = 祝日の名前(日)
    文 = []
    if 名:
        文.append(f"{日.month}/{日.day}（{曜[日.weekday()]}）は{名}（祝日）です。")
    連 = 連休(日)
    if 連:
        始, 終 = 連
        n = (終 - 始).days + 1
        k = (日 - 始).days + 1
        文.append(f"{始.month}/{始.day}〜{終.month}/{終.day} は{n}連休の{k}日目です"
                  + ("（最終日）。" if 日 == 終 else "。"))
    else:
        # 明日から連休が始まる日（前日の夜に読まれる投稿のため）
        明 = 日 + timedelta(days=1)
        連2 = 連休(明)
        if 連2 and 連2[0] == 明:
            n = (連2[1] - 連2[0]).days + 1
            文.append(f"明日 {明.month}/{明.day} から{n}連休です（〜{連2[1].month}/{連2[1].day}）。")
    return "".join(文)
