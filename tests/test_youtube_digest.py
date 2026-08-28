# youtube_digest의 파싱·창·중복제거·렌더를 네트워크 없이 검산한다.
# 실행: python -m unittest discover -s tests

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import youtube_digest as yd  # noqa: E402

UTC = timezone.utc

FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>샤를의 군사연구소</title>
  <entry>
    <id>yt:video:aaaaaaaaaaa</id>
    <yt:videoId>aaaaaaaaaaa</yt:videoId>
    <title>[긴급] 오늘 나온 영상</title>
    <published>2026-08-27T09:00:00+00:00</published>
    <media:group><media:description>본문</media:description></media:group>
  </entry>
  <entry>
    <id>yt:video:bbbbbbbbbbb</id>
    <yt:videoId>bbbbbbbbbbb</yt:videoId>
    <title>라이브 예고</title>
    <published>2026-08-26T01:30:00+00:00</published>
    <media:group><media:description>예고</media:description></media:group>
  </entry>
  <entry>
    <id>yt:video:ccccccccccc</id>
    <yt:videoId>ccccccccccc</yt:videoId>
    <title>지난주 영상</title>
    <published>2026-08-10T00:00:00+00:00</published>
  </entry>
</feed>
"""


class ParseFeedTest(unittest.TestCase):
    def test_reads_title_and_entries_newest_first(self):
        channel, videos = yd.parse_feed(FEED.encode("utf-8"))
        self.assertEqual(channel, "샤를의 군사연구소")
        self.assertEqual([v["video_id"] for v in videos],
                         ["aaaaaaaaaaa", "bbbbbbbbbbb", "ccccccccccc"])
        self.assertEqual(videos[0]["url"],
                         "https://www.youtube.com/watch?v=aaaaaaaaaaa")
        self.assertEqual(videos[0]["published"],
                         datetime(2026, 8, 27, 9, 0, tzinfo=UTC))

    def test_entry_without_video_id_is_dropped(self):
        broken = FEED.replace("<yt:videoId>ccccccccccc</yt:videoId>", "")
        _, videos = yd.parse_feed(broken.encode("utf-8"))
        self.assertEqual(len(videos), 2)


class SelectTest(unittest.TestCase):
    def setUp(self):
        _, self.videos = yd.parse_feed(FEED.encode("utf-8"))
        self.since = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)

    def test_window_cuts_older_videos(self):
        picked = yd.select_videos(self.videos, {}, self.since, {}, 20)
        self.assertEqual([v["video_id"] for v in picked],
                         ["aaaaaaaaaaa", "bbbbbbbbbbb"])

    def test_already_sent_video_is_skipped(self):
        seen = {"aaaaaaaaaaa": "2026-08-27T10:00:00+00:00"}
        picked = yd.select_videos(self.videos, {}, self.since, seen, 20)
        self.assertEqual([v["video_id"] for v in picked], ["bbbbbbbbbbb"])

    def test_exclude_pattern_drops_by_title(self):
        picked = yd.select_videos(
            self.videos, {"exclude": "라이브|예고"}, self.since, {}, 20
        )
        self.assertEqual([v["video_id"] for v in picked], ["aaaaaaaaaaa"])

    def test_match_pattern_keeps_only_hits(self):
        picked = yd.select_videos(
            self.videos, {"match": "긴급"}, self.since, {}, 20
        )
        self.assertEqual([v["video_id"] for v in picked], ["aaaaaaaaaaa"])

    def test_limit_caps_per_channel(self):
        picked = yd.select_videos(self.videos, {}, self.since, {}, 1)
        self.assertEqual(len(picked), 1)


class ResolveTest(unittest.TestCase):
    def test_channel_id_in_config_wins(self):
        entry = {"channel_id": "UC" + "a" * 22, "handle": "@x"}
        self.assertEqual(yd.resolve_channel_id(entry, {}), "UC" + "a" * 22)

    def test_cached_handle_is_reused_without_network(self):
        state = {"resolved": {"@kkam": {"channel_id": "UC" + "b" * 22}}}
        self.assertEqual(
            yd.resolve_channel_id({"handle": "@kkam"}, state), "UC" + "b" * 22
        )

    def test_candidate_urls_percent_encode_hangul_handle(self):
        urls = yd.candidate_urls({"handle": "@까치살모"})
        self.assertTrue(urls[0].startswith("https://www.youtube.com/@%"))
        self.assertEqual(len(urls), 3)

    def test_extract_channel_id_from_page_markup(self):
        page = '{"externalId":"UC' + "c" * 22 + '","x":1}'
        self.assertEqual(yd.extract_channel_id(page), "UC" + "c" * 22)


class WindowTest(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 8, 28, 22, 0, tzinfo=UTC)

    def test_first_run_uses_full_interval(self):
        self.assertEqual(
            yd.window_start({}, self.now, 3), self.now - timedelta(days=3)
        )

    def test_window_reaches_back_to_last_digest(self):
        last = self.now - timedelta(days=4)
        start = yd.window_start({"last_digest_at": last.isoformat()}, self.now, 3)
        self.assertEqual(start, last)

    def test_long_outage_is_capped(self):
        last = self.now - timedelta(days=60)
        start = yd.window_start({"last_digest_at": last.isoformat()}, self.now, 3)
        self.assertEqual(start, self.now - timedelta(days=yd.MAX_LOOKBACK_DAYS))

    def test_due_is_false_right_after_a_digest(self):
        last = self.now - timedelta(days=1)
        self.assertFalse(yd.due({"last_digest_at": last.isoformat()}, self.now, 3))

    def test_due_allows_a_few_hours_early(self):
        last = self.now - timedelta(days=2, hours=20)
        self.assertTrue(yd.due({"last_digest_at": last.isoformat()}, self.now, 3))

    def test_due_when_never_run(self):
        self.assertTrue(yd.due({}, self.now, 3))


class RenderTest(unittest.TestCase):
    def setUp(self):
        _, videos = yd.parse_feed(FEED.encode("utf-8"))
        self.groups = [{"name": "샤를의 군사연구소", "videos": videos[:2]}]
        self.since = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
        self.until = datetime(2026, 8, 28, 0, 0, tzinfo=UTC)

    def test_links_file_is_bare_urls_one_per_line(self):
        body = yd.render_links(self.groups)
        lines = body.strip().splitlines()
        self.assertEqual(len(lines), 2)
        for line in lines:
            self.assertTrue(line.startswith("https://www.youtube.com/watch?v="))

    def test_markdown_lists_channel_and_titles(self):
        body = yd.render_markdown(self.groups, self.since, self.until)
        self.assertIn("## 샤를의 군사연구소", body)
        self.assertIn("[긴급] 오늘 나온 영상", body)
        self.assertIn("영상 2건", body)

    def test_telegram_lines_escape_html(self):
        groups = [{"name": "a<b>", "videos": self.groups[0]["videos"]}]
        lines = yd.build_telegram_lines(groups, self.since, self.until, None)
        self.assertIn("<b>a&lt;b&gt;</b>", lines)

    def test_chunking_respects_limit(self):
        lines = [f"line-{i}" * 10 for i in range(200)]
        chunks = yd.chunk_lines(lines, limit=500)
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            self.assertLessEqual(len(chunk), 500)
        self.assertEqual("\n".join(chunks).count("line-0"), 10)


class PruneTest(unittest.TestCase):
    def test_old_ids_are_dropped(self):
        now = datetime(2026, 8, 28, tzinfo=UTC)
        seen = {
            "fresh": (now - timedelta(days=5)).isoformat(),
            "stale": (now - timedelta(days=yd.SEEN_RETENTION_DAYS + 5)).isoformat(),
            "junk": "not-a-date",
        }
        self.assertEqual(list(yd.prune_seen(seen, now)), ["fresh"])


class WriteOutputsTest(unittest.TestCase):
    """파일 3종(+index.json)이 실제로 어떻게 떨어지는지 임시 디렉터리에서 확인한다."""

    def setUp(self):
        _, videos = yd.parse_feed(FEED.encode("utf-8"))
        self.groups = [{"name": "샤를의 군사연구소", "videos": videos[:2]}]
        self.since = datetime(2026, 8, 25, 0, 0, tzinfo=UTC)
        self.until = datetime(2026, 8, 28, 5, 0, tzinfo=UTC)  # KST로 8/28 14:00
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.original_out_dir = yd.OUT_DIR
        yd.OUT_DIR = Path(self.tmp.name) / "youtube"
        self.addCleanup(setattr, yd, "OUT_DIR", self.original_out_dir)

    def test_writes_dated_and_latest_files(self):
        written = yd.write_outputs(self.groups, self.since, self.until)
        self.assertEqual(written["date"], "2026-08-28")
        for name in ("2026-08-28.md", "2026-08-28-links.txt",
                     "latest.md", "latest-links.txt", "index.json"):
            self.assertTrue((yd.OUT_DIR / name).exists(), name)
        links = (yd.OUT_DIR / "latest-links.txt").read_text(encoding="utf-8")
        self.assertEqual(len(links.strip().splitlines()), 2)

    def test_index_keeps_one_row_per_date_newest_first(self):
        yd.write_outputs(self.groups, self.since, self.until)
        yd.write_outputs(self.groups, self.since, self.until)  # 같은 날 재실행
        yd.write_outputs(self.groups, self.since, self.until + timedelta(days=3))
        index = json.loads((yd.OUT_DIR / "index.json").read_text(encoding="utf-8"))
        dates = [row["date"] for row in index["digests"]]
        self.assertEqual(dates, ["2026-08-31", "2026-08-28"])
        self.assertEqual(index["digests"][0]["videos"], 2)


SHORTS_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns:yt="http://www.youtube.com/xml/schemas/2015"
      xmlns:media="http://search.yahoo.com/mrss/"
      xmlns="http://www.w3.org/2005/Atom">
  <title>샤를의 군사연구소 - Videos</title>
  <entry>
    <yt:videoId>aaaaaaaaaaa</yt:videoId>
    <title>[긴급] 오늘 나온 영상</title>
    <published>2026-08-27T09:00:00+00:00</published>
  </entry>
  <entry>
    <yt:videoId>4i20qy1FyOU</yt:videoId>
    <title>한국형 상륙돌격장갑차 II</title>
    <published>2026-08-27T12:00:00+00:00</published>
  </entry>
</feed>
"""


class FeedMergeTest(unittest.TestCase):
    """채널 피드가 쇼츠를 빼고 주더라도 업로드 재생목록 쪽에서 잡아 온다."""

    def setUp(self):
        self.original = yd.http_get
        self.addCleanup(setattr, yd, "http_get", self.original)

    def test_uploads_playlist_id(self):
        self.assertEqual(yd.uploads_playlist_id("UC" + "x" * 22), "UU" + "x" * 22)

    def test_merge_dedupes_and_sorts_newest_first(self):
        _, channel_videos = yd.parse_feed(FEED.encode("utf-8"))
        _, shorts_videos = yd.parse_feed(SHORTS_FEED.encode("utf-8"))
        merged = yd.merge_videos(channel_videos, shorts_videos)
        ids = [v["video_id"] for v in merged]
        self.assertEqual(ids[0], "4i20qy1FyOU")          # 가장 최신
        self.assertEqual(ids.count("aaaaaaaaaaa"), 1)    # 양쪽에 있어도 한 번
        self.assertEqual(len(ids), 4)

    def test_fetch_hits_both_feeds_and_merges(self):
        asked = []

        def fake(url, timeout=30, retries=3):
            asked.append(url)
            if "playlist_id=UU" in url:
                return SHORTS_FEED.encode("utf-8")
            return FEED.encode("utf-8")

        yd.http_get = fake
        title, videos = yd.fetch_channel_videos("UC" + "z" * 22)
        self.assertEqual(title, "샤를의 군사연구소")
        self.assertEqual(len(asked), 2)
        self.assertIn("4i20qy1FyOU", [v["video_id"] for v in videos])

    def test_one_feed_failing_does_not_lose_the_other(self):
        def fake(url, timeout=30, retries=3):
            if "playlist_id=UU" in url:
                raise RuntimeError("HTTP Error 404: Not Found")
            return FEED.encode("utf-8")

        yd.http_get = fake
        _, videos = yd.fetch_channel_videos("UC" + "z" * 22)
        self.assertEqual(len(videos), 3)

    def test_both_feeds_failing_raises(self):
        def fake(url, timeout=30, retries=3):
            raise RuntimeError("blocked")

        yd.http_get = fake
        with self.assertRaises(RuntimeError):
            yd.fetch_channel_videos("UC" + "z" * 22)


class BufferTest(unittest.TestCase):
    """수집은 매일, 발송은 3일에 한 번 — 그 사이 버퍼가 새는지 본다."""

    def setUp(self):
        _, self.videos = yd.parse_feed(FEED.encode("utf-8"))
        self.now = datetime(2026, 8, 28, 22, 0, tzinfo=UTC)
        self.groups = [{"name": "샤를의 군사연구소", "videos": self.videos[:2]}]

    def test_buffering_is_idempotent(self):
        state = {}
        self.assertEqual(yd.buffer_videos(state, self.groups, self.now), 2)
        self.assertEqual(yd.buffer_videos(state, self.groups, self.now), 0)
        self.assertEqual(len(state["pending"]), 2)

    def test_buffer_accumulates_across_days(self):
        state = {}
        yd.buffer_videos(state, [{"name": "A", "videos": self.videos[:1]}], self.now)
        yd.buffer_videos(
            state,
            [{"name": "A", "videos": self.videos[1:2]}],
            self.now + timedelta(days=1),
        )
        self.assertEqual(len(state["pending"]), 2)

    def test_groups_from_buffer_follows_config_order(self):
        state = {}
        yd.buffer_videos(state, [{"name": "kkam", "videos": self.videos[:1]}], self.now)
        yd.buffer_videos(
            state, [{"name": "샤를의 군사연구소", "videos": self.videos[1:]}], self.now
        )
        config = {"channels": [{"name": "샤를의 군사연구소"}, {"name": "kkam"}]}
        groups = yd.groups_from_buffer(state, config)
        self.assertEqual([g["name"] for g in groups], ["샤를의 군사연구소", "kkam"])
        self.assertEqual(len(groups[0]["videos"]), 2)

    def test_groups_from_buffer_sorts_videos_newest_first(self):
        state = {}
        yd.buffer_videos(state, self.groups, self.now)
        groups = yd.groups_from_buffer(state, {"channels": []})
        published = [v["published"] for v in groups[0]["videos"]]
        self.assertEqual(published, sorted(published, reverse=True))

    def test_empty_buffer_makes_no_groups(self):
        self.assertEqual(yd.groups_from_buffer({"pending": {}}, {}), [])


if __name__ == "__main__":
    unittest.main()
