"""core/media.py：文件名季集识别、中文季号、集号区间、清晰度。"""
from __future__ import annotations

import unittest

from reelay.core.media import (cn_number, episode_list, episode_of, format_episodes, resolution_label,
                               resolution_name, resolution_rank, season_of)


class EpisodeOfTest(unittest.TestCase):
    def test_common_file_names(self):
        cases = {
            "繁花.S01E05.2160p.mkv": (1, 5),
            "Show.S02.EP03.mkv": (2, 3),
            "Show S01 E07 1080p.mp4": (1, 7),
            "某剧 第2季 第05集.mp4": (2, 5),
            "某剧 第二季 第12话.mkv": (2, 12),
            "第05集.mp4": (None, 5),
            "Squid Game 2021 E05.mkv": (None, 5),
            "[字幕组] 某番 - 05 [1080p].mkv": (None, 5),
            "[字幕组] 某番 S2 - 11v2 (1080p).mkv": (2, 11),
            "[字幕组][某番][07][1080p].mp4": (None, 7),
            "05.mkv": (None, 5),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(episode_of(name), expected)

    def test_years_and_resolutions_are_not_episodes(self):
        for name in ("House.1080p.03.mkv", "Movie.2023.2160p.mkv", "2021.mkv", "1080.mkv", "Movie.mkv"):
            with self.subTest(name=name):
                self.assertIsNone(episode_of(name)[1])

    def test_season_marker_without_episode(self):
        self.assertEqual(episode_of("某剧 第二季 合集.mkv"), (2, None))


class NumbersTest(unittest.TestCase):
    def test_chinese_numbers(self):
        for text, value in {"一": 1, "两": 2, "十": 10, "十二": 12, "二十": 20, "二十三": 23, "7": 7}.items():
            self.assertEqual(cn_number(text), value)
        self.assertIsNone(cn_number("百"))
        self.assertIsNone(cn_number("一二"))

    def test_season_of(self):
        self.assertEqual(season_of("漫长的季节 第二季 更新至 5 集"), 2)
        self.assertEqual(season_of("Show S03 1080p"), 3)
        self.assertEqual(season_of("Show Season 4"), 4)
        self.assertIsNone(season_of("Show 2023 1080p"))

    def test_episode_list(self):
        self.assertEqual(episode_list("1-3,5、E07-E08"), [1, 2, 3, 5, 7, 8])
        with self.assertRaises(ValueError):
            episode_list("5-1")
        with self.assertRaises(ValueError):
            episode_list("第五集")

    def test_format_episodes(self):
        self.assertEqual(format_episodes([1, 2, 3, 5]), "E01-E03、E05")


class ResolutionTest(unittest.TestCase):
    def test_names_and_ranks(self):
        self.assertEqual(resolution_name("✨4K HDR"), "2160p")
        self.assertEqual(resolution_name("1080i"), "1080p")
        self.assertEqual(resolution_rank("WEB-DL 720p"), 2)
        self.assertEqual(resolution_label("576p"), "标清")
        self.assertIsNone(resolution_name("HDR WEB-DL"))
