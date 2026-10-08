"""県ごとの設定（region.json）の確かめ。

福井版のリポジトリでは regions/<県>/ を、石川版などのリポジトリではリポジトリ直下の
region.json を見て、AI に渡す指示文に福井の言い回しが残っていないかを確かめる。
地域のモジュールは読み込んだ時点の設定を覚えるので、県ごとに別のプロセスで走らせる。
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 福井にしかない言葉。石川版などの指示文・ページに残っていたら置きかえ漏れ
福井の言葉 = (
    "福井|ふくい|嶺北|嶺南|鯖江|越前|若狭|小浜|勝山|敦賀|坂井|あわら|三国|永平寺|大野市|一乗谷|"
    "東尋坊|九頭竜|足羽|丸岡|美浜|高浜|おおい町|今庄|ふーぽ|フクブロ|URALA|ウララ|urala|"
    "恐竜|ロクメイ|大越|めがね|夏障子|天一祭|fuku-e|fupo|日野川|C-Base"
)

確かめる = r"""
import importlib.util, json, re, sys
from datetime import date
sys.path.insert(0, "scripts")
spec = importlib.util.spec_from_file_location("compose", "scripts/compose.py")
m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
import 地域
本物 = 地域.直す
地域.直す = lambda t: t
元 = m.build_prompt("", "", "", "", "", date(2026, 10, 10), m.SLOTS, {}, hotel_hour=None)
地域.直す = 本物
指示 = m.build_prompt("", "", "", "", "", date(2026, 10, 10), m.SLOTS, {}, hotel_hour=None)
集め = open("scripts/neta-collect.mjs", encoding="utf-8").read()
for a, b in 地域.地域.get("言いかえ", []):
    集め = 集め.replace(a, b)
当たらない = [a[:40] for a, _ in 地域.地域.get("言いかえ", [])
            if a not in 元 and a not in open("scripts/neta-collect.mjs", encoding="utf-8").read()
            and a != "福井イベント"]
w = re.compile(sys.argv[1])
残り = [l for l in 指示.splitlines() if w.search(l)]
# 情報収集の指示文は、テンプレートの部分（指示 = ` 〜 `）だけを見る
本文 = 集め[集め.index("const 指示 = `"):集め.index("const 地域 = ")]
残り += [l for l in 本文.splitlines() if w.search(l) and not l.lstrip().startswith("//")]
print(json.dumps({"当たらない": 当たらない, "残り": 残り, "県": 地域.県名,
                  "15時": [s[1] for s in m.SLOTS if s[0] == 15][0]}, ensure_ascii=False))
"""


def 県の設定たち() -> list[Path]:
    直下 = json.loads((ROOT / "region.json").read_text(encoding="utf-8"))
    if 直下["県名"] != "福井":
        return [ROOT / "region.json"]
    return sorted((ROOT / "regions").glob("*/region.json"))


class RegionTest(unittest.TestCase):
    def test_fukui_fixture_matches(self):
        直下 = json.loads((ROOT / "region.json").read_text(encoding="utf-8"))
        if 直下["県名"] != "福井":
            self.skipTest("福井版のリポジトリではない")
        写し = json.loads((ROOT / "tests" / "region_fukui.json").read_text(encoding="utf-8"))
        self.assertEqual(直下, 写し, "tests/region_fukui.json を region.json と同じにしてください")
        self.assertEqual(直下["言いかえ"], [], "福井版の言いかえは空のまま（指示文を変えないため）")

    def test_other_regions_have_no_fukui_words(self):
        for 設定 in 県の設定たち():
            with self.subTest(設定=str(設定.relative_to(ROOT))):
                env = {**os.environ, "REGION_FILE": str(設定)}
                出 = subprocess.run([sys.executable, "-c", 確かめる, 福井の言葉], cwd=ROOT, env=env,
                                   capture_output=True, text=True, check=True)
                結果 = json.loads(出.stdout.strip().splitlines()[-1])
                self.assertEqual(結果["当たらない"], [], "言いかえの元の文が、指示文に見つからない")
                self.assertEqual(結果["残り"], [], "福井の言葉が指示文に残っている")
                地 = json.loads(設定.read_text(encoding="utf-8"))
                PRあり = 地.get("使う", {}).get("宿", True) or 地.get("使う", {}).get("ふるさと納税", True)
                self.assertEqual(結果["15時"], "楽天トラベルの紹介（PR）" if PRあり else f"{結果['県']}の話題紹介")

    def test_noto_check_flags_only_with_place_and_word(self):
        for 設定 in 県の設定たち():
            地 = json.loads(設定.read_text(encoding="utf-8"))
            if "能登の確認" not in 地:
                continue
            with self.subTest(設定=str(設定.relative_to(ROOT))):
                env = {**os.environ, "REGION_FILE": str(設定)}
                式 = (
                    "import importlib.util,sys,json; sys.path.insert(0,'scripts');"
                    "s=importlib.util.spec_from_file_location('c','scripts/compose.py');"
                    "m=importlib.util.module_from_spec(s); s.loader.exec_module(m);"
                    "print(json.dumps([m.確かめる言葉('輪島市の朝市が復興へ一歩',[]),"
                    "m.確かめる言葉('輪島市の朝市が10月1日から開かれます',[]),"
                    "m.確かめる言葉('金沢で地震の防災訓練',[])],ensure_ascii=False))"
                )
                出 = subprocess.run([sys.executable, "-c", 式], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
                self.assertEqual(json.loads(出.stdout.strip().splitlines()[-1]), ["復興", None, None])

    def test_copy_site_has_no_fukui_words(self):
        if not (ROOT / "regions").exists():
            self.skipTest("写しの元（福井版）ではない")
        import re
        w = re.compile(福井の言葉)
        for 設定 in 県の設定たち():
            with self.subTest(設定=str(設定.relative_to(ROOT))), tempfile.TemporaryDirectory() as 先:
                subprocess.run([sys.executable, "scripts/地域の写し.py", str(設定.parent.relative_to(ROOT)), 先],
                               cwd=ROOT, check=True, capture_output=True)
                for 名 in ("docs/index.html", "docs/about/index.html", "docs/robots.txt"):
                    文 = (Path(先) / 名).read_text(encoding="utf-8")
                    残り = [l for l in 文.splitlines()
                           if w.search(l) and "姉妹サイト" not in l]
                    self.assertEqual(残り, [], f"{名} に福井の言葉が残っている")
                self.assertFalse((Path(先) / "neta" / "ふるさと納税_リンク.jsonl").exists())
                self.assertTrue((Path(先) / "neta" / "ネタ帳.md").exists())


if __name__ == "__main__":
    unittest.main()
