import os
from pathlib import Path as _P

# 福井のデータで書いたテストなので、どの県のリポジトリでも福井の設定で走らせる
os.environ["REGION_FILE"] = str(_P(__file__).resolve().parent / "region_fukui.json")
import importlib.util
import unittest
from pathlib import Path

_path = Path(__file__).resolve().parent.parent / "scripts" / "おでかけ_宿.py"
_spec = importlib.util.spec_from_file_location("odekake_stay", _path)
stay = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(stay)

HOTEL = {
    "番号": 168,
    "名": "テストホテル（テストホテルズグループ）",
    "エリア": "敦賀",
    "住所": "福井県敦賀市白銀町1-1",
    "url": "https://hb.afl.rakuten.co.jp/long",
    "評価": 4.39,
    "レビュー数": 120,
    "最安": 5100,
    "特色": "駅から徒歩1分。&#12220;層の眺め",
    "風呂": ["天然温泉の大浴場", "サウナ"],
    "館内設備": [],
    "部屋の備品": ["浴衣"],
}


class StayTest(unittest.TestCase):
    def test_build_fields(self):
        h = stay.build([HOTEL], {})[0]
        self.assertEqual(h["id"], 168)
        self.assertEqual(h["city"], "敦賀市")
        self.assertEqual(h["region"], "嶺南")
        self.assertEqual(h["url"], "https://hb.afl.rakuten.co.jp/long")
        self.assertEqual(h["image"], "https://trvimg.r10s.jp/share/HOTEL/168/168.jpg")
        self.assertEqual((h["rating"], h["reviews"], h["price"]), (4.39, 120, 5100))
        self.assertNotIn("&#", h["text"])

    def test_short_link_wins(self):
        h = stay.build([HOTEL], {168: "https://a.r10.to/abc"})[0]
        self.assertEqual(h["url"], "https://a.r10.to/abc")

    def test_features_only_what_is_there(self):
        feats = stay.build([HOTEL], {})[0]["features"]
        self.assertIn("温泉", feats)
        self.assertIn("サウナ", feats)
        self.assertNotIn("露天風呂", feats)  # 無いことは書かない（印を付けないだけ）

    def test_artificial_onsen_is_not_onsen(self):
        h = dict(HOTEL, 風呂=["人工温泉の大浴場"])
        self.assertNotIn("温泉", stay.build([h], {})[0]["features"])

    def test_skip_without_number_or_url(self):
        self.assertEqual(stay.build([dict(HOTEL, url="")], {}), [])


if __name__ == "__main__":
    unittest.main()
