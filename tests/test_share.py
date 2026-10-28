"""UX B4: a read-only share link per version (montivo/server/share.py)."""
from montivo.server import hosted
from montivo.server.share import Shares, page, poster_time


def test_a_version_keeps_its_token_and_another_gets_a_new_one(tmp_path):
    s = Shares(tmp_path)
    a = s.create("015", 5)
    assert s.create("015", 5) == a and s.create("015", 3) != a and s.get(a)["version"] == 5


def test_the_page_escapes_what_it_shows_and_points_only_at_its_token():
    v = {"n": 5, "title_card_text": "<b>x</b>", "duration_s": 37.9, "title_s": 1.5, "spans": [{}, {}],
         "review": {"overall": 87, "pass": True, "summary": "ok", "scores": {"pacing": 10},
                    "issues": [{"t_s": 13.6, "severity": "major", "what": "a <script>"}]}}
    h = page("tok", {"brief": "b"}, v, [{"n": 1, "overall": 68}, {"n": 5, "overall": 87}], "http://h/")
    assert "<b>x</b>" not in h and "&lt;b&gt;x&lt;/b&gt;" in h and "a &lt;script&gt;" in h
    assert 'src="/s/tok/video.mp4"' in h and "http://h/s/tok/poster.jpg" in h and "/api/" not in h
    assert "v1 <b>68</b> → v5 <b>87</b>" in h


def test_share_paths_are_open_on_the_hosted_demo_and_the_poster_sits_after_the_title():
    assert "/s/x".startswith(hosted.OPEN_PATHS) and not "/api/runs".startswith(hosted.OPEN_PATHS)
    assert poster_time({"duration_s": 31.5, "title_s": 1.5}) == 11.5
