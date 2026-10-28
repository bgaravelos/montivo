"""The segment cache and the parallel encodes of render.py (E7): hits, misses, one changed caption re-encodes one
segment, and a cached render is bit-identical to an uncached one."""
import copy
import os
import shutil
import subprocess
import time

import pytest

from montivo.engine import render as R
from montivo.engine.reframe import SegmentPath
from montivo.ledger import Ledger

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")

WORDS = [("One", 0.10, 0.40, 0), ("two", 0.45, 0.80, 0), ("three", 1.25, 1.60, 1), ("four", 1.65, 1.95, 1),
         ("five", 2.25, 2.55, 2), ("six", 2.60, 2.90, 2)]


@pytest.fixture
def source(tmp_path):
    """A 1920x1080 30 fps clip with a tone, 3 s."""
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "lavfi", "-i", "testsrc2=s=1920x1080:r=30:d=3",
                    "-f", "lavfi", "-i", "sine=f=440:r=48000:d=3", "-c:v", "libx264", "-preset", "ultrafast",
                    "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)], check=True)
    return src


def edl_for(src):
    spans = [{"id": f"s{i + 1}", "turn_id": i, "role": role, "t_in": a, "t_out": b, "speaker": "A",
              "word_from": 2 * i, "word_to": 2 * i + 1}
             for i, (role, a, b) in enumerate([("hook", 0.0, 0.9), ("body", 1.2, 2.0), ("payoff", 2.2, 2.95)])]
    return {"source": {"path": str(src), "width": 1920, "height": 1080, "fps": 30.0, "duration_s": 3.0},
            "spans": spans, "glossary": {}, "highlight": []}


def words(texts=None):
    return [{"w": (texts or {}).get(w, w), "t_start": a, "t_end": b, "turn_id": turn, "speaker": "A", "i": i}
            for i, (w, a, b, turn) in enumerate(WORDS)]


def do_render(run_dir, src, edl, ws, cache=True, log=None):
    lines = [] if log is None else log
    run_dir.mkdir(exist_ok=True)
    rec = R.render(run_dir, copy.deepcopy(edl), src, Ledger(run_dir / "ledger.json"), out_name="cut_v1.mp4",
                   words=ws, loudnorm=False, cache=cache, log=lines.append)
    return rec, lines


def md5s(media):
    """framemd5 of every video frame and the md5 of the decoded audio."""
    v = subprocess.run(["ffmpeg", "-v", "error", "-i", str(media), "-map", "0:v", "-f", "framemd5", "-"],
                       capture_output=True, text=True, check=True).stdout
    a = subprocess.run(["ffmpeg", "-v", "error", "-i", str(media), "-map", "0:a", "-f", "md5", "-"],
                       capture_output=True, text=True, check=True).stdout
    return [ln for ln in v.splitlines() if not ln.startswith("#")], a.strip()


@needs_ffmpeg
def test_second_render_hits_every_segment_and_a_caption_edit_reencodes_only_its_own(tmp_path, source):
    run = tmp_path / "run"
    edl = edl_for(source)
    first, log1 = do_render(run, source, edl, words())
    assert [s["cached"] for s in first["segments"]] == [False, False, False]
    assert first["segcache"] == {"on": True, "cached": 0, "encoded": 3, "audio_cached": False,
                                 "workers": R.ENCODE_WORKERS, "x264_threads": R.X264_THREADS}
    cache = run / "render" / R.SEGCACHE
    assert sorted(p.stem for p in cache.glob("*.mp4")) == sorted(k for s in first["segments"] for k in s["cache_keys"])
    seg_lines = [ln for ln in log1 if ln.startswith("render: seg ")]
    assert len(seg_lines) == 3 and not any(ln.endswith(", cached") for ln in seg_lines)

    again, log2 = do_render(run, source, edl, words())
    assert [s["cached"] for s in again["segments"]] == [True, True, True] and again["segcache"]["audio_cached"]
    assert [s["cache_keys"] for s in again["segments"]] == [s["cache_keys"] for s in first["segments"]]
    assert all(ln.endswith(", cached") for ln in log2 if ln.startswith("render: seg "))
    assert md5s(run / "cut_v1.mp4")  # concat by stream copy of linked cache files still reads

    # retext a caption word in s2: only segment 2's overlay PNG changes, so only it is encoded again
    edit, log3 = do_render(run, source, edl, words({"three": "THREE"}))
    assert [s["cached"] for s in edit["segments"]] == [True, False, True]
    assert edit["segments"][1]["cache_keys"] != first["segments"][1]["cache_keys"]
    assert edit["segcache"]["encoded"] == 1 and edit["segcache"]["audio_cached"]  # a caption leaves the sound alone
    # the segment lines land in finishing order: the two hits first
    assert [ln.split()[2] for ln in log3 if ln.startswith("render: seg ")] == ["1", "3", "2"]


@needs_ffmpeg
def test_cached_render_is_bit_identical_to_an_uncached_one(tmp_path, source):
    edl = edl_for(source)
    ws = words({"five": "FIVE"})
    a, b = tmp_path / "a", tmp_path / "b"
    do_render(a, source, edl, words())
    cached, _ = do_render(a, source, edl, ws)  # s1 and s2 from the cache, s3 encoded again
    assert [s["cached"] for s in cached["segments"]] == [True, True, False]
    plain, _ = do_render(b, source, edl, ws, cache=False)
    assert plain["segcache"]["on"] is False and not (b / "render" / R.SEGCACHE).exists()
    assert "cache_keys" not in plain["segments"][0]
    va, aa = md5s(a / "cut_v1.mp4")
    vb, ab = md5s(b / "cut_v1.mp4")
    assert len(va) == sum(s["frames"] for s in plain["segments"])
    assert va == vb and aa == ab


@needs_ffmpeg
def test_the_env_switch_turns_the_cache_off(tmp_path, source, monkeypatch):
    monkeypatch.setenv("MONTIVO_SEGCACHE", "0")
    run = tmp_path / "run"
    run.mkdir()
    rec = R.render(run, edl_for(source), source, Ledger(run / "ledger.json"), words=words(), loudnorm=False,
                   log=lambda m: None)
    assert rec["segcache"]["on"] is False and not (run / "render" / R.SEGCACHE).exists()


@needs_ffmpeg
def test_a_long_segment_encodes_in_chunks_and_an_edit_touches_only_its_chunk(tmp_path, source, monkeypatch):
    monkeypatch.setattr(R, "CHUNK_S", 0.3)  # 9 frames: s1 (27 frames) goes in 3 chunks
    edl = edl_for(source)
    run = tmp_path / "run"
    first, log1 = do_render(run, source, edl, words())
    s1 = first["segments"][0]
    assert s1["chunks"] == [[0, 9], [9, 18], [18, 27]] and len(s1["files"]) == 3
    assert any(ln.startswith("render: seg 1 ") and ln.endswith(", 3 chunks") for ln in log1)
    # "two" (0.45-0.80 s) is on screen in chunks 2 and 3 only (its line starts with "One" at 0.10 s, in chunk 1 too)
    edit, log2 = do_render(run, source, edl, words({"six": "SIX"}))
    assert edit["segments"][0]["cached"] and edit["segments"][2]["chunks_cached"] < len(edit["segments"][2]["chunks"])
    # the chunks join into the same picture as one whole encode: the crop and the overlays keep the segment's clock
    chunked = do_render(run, source, edl, words())[0]
    assert chunked["segcache"]["encoded"] == 0  # the first render's chunks, all from the cache
    whole = tmp_path / "whole"
    monkeypatch.setattr(R, "CHUNK_S", 100.0)
    one, _ = do_render(whole, source, edl, words())
    assert one["segments"][0]["chunks"] == [[0, 27]]
    va, _ = md5s(run / "cut_v1.mp4")
    vb, _ = md5s(whole / "cut_v1.mp4")
    assert len(va) == len(vb) == sum(s["frames"] for s in first["segments"]) and va != vb  # other GOPs, same frames
    psnr = subprocess.run(["ffmpeg", "-v", "info", "-i", str(run / "cut_v1.mp4"), "-i", str(whole / "cut_v1.mp4"),
                           "-lavfi", "psnr=stats_file=-", "-f", "null", "-"], capture_output=True, text=True, check=True).stdout
    per_frame = [float(v) if v != "inf" else 99.0
                 for v in (ln.split("psnr_avg:")[1].split()[0] for ln in psnr.splitlines() if "psnr_avg:" in ln)]
    assert len(per_frame) == len(va) and min(per_frame) > 38, min(per_frame)


def _psnr_min(a, b):
    out = subprocess.run(["ffmpeg", "-v", "info", "-i", str(a), "-i", str(b), "-lavfi", "psnr=stats_file=-", "-f",
                          "null", "-"], capture_output=True, text=True, check=True).stdout
    vals = [ln.split("psnr_avg:")[1].split()[0] for ln in out.splitlines() if "psnr_avg:" in ln]
    return len(vals), min(99.0 if v == "inf" else float(v) for v in vals)


@needs_ffmpeg
def test_a_chunk_starts_on_the_crop_x_of_its_first_frame(tmp_path, source):
    """A panning crop, a fading caption across a chunk edge: the chunks give the whole encode's picture."""
    from PIL import Image
    seg = R.Segment(index=0, t_in=0.5, t_out=2.5, span_ids=["s1"], frames=60, fps=30.0)
    path = SegmentPath(t_in=0.5, t_out=2.5, track_id="0", crop_w=608, crop_h=1080,
                       xs=[100 + 16 * k for k in range(60)], fps=30.0)  # 480 px/s pan
    png = tmp_path / "line.png"
    Image.new("RGBA", (600, 120), (255, 198, 41, 255)).save(png)
    ovs = [R.Overlay(png, 200, 1400, 0.6, 1.4, fade_s=0.2)]
    for d in ("whole", "chunks"):
        (tmp_path / d).mkdir()
    whole = R.segment_encode(source, seg, path, tmp_path / "whole", ovs)
    R.run_encode(whole, None, None)
    parts = [R.segment_encode(source, seg, path, tmp_path / "chunks", ovs, part=p) for p in [(0, 25), (25, 45), (45, 60)]]
    assert [len([a for a in e.args if a.endswith(".png")]) for e in parts] == [1, 1, 0]  # the line is on 0.6-1.4 s
    for e in parts:
        R.run_encode(e, None, None)
    lst = tmp_path / "chunks" / "list.txt"
    lst.write_text("".join(f"file '{e.out.as_posix()}'\n" for e in parts))
    joined = tmp_path / "joined.mp4"
    subprocess.run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy",
                    str(joined)], check=True)
    n, low = _psnr_min(joined, whole.out)
    assert n == 60 and low > 38, low


def test_chunk_bounds():
    seg = R.Segment(index=0, t_in=0.0, t_out=30.0, span_ids=["s1"], frames=890, fps=30.0)
    assert R.chunk_bounds(seg) == [(k, k + 120) for k in range(0, 720, 120)] + [(720, 890)]  # a 50-frame tail joins
    seg.frames = 960
    assert R.chunk_bounds(seg)[-1] == (960 - 120, 960) and len(R.chunk_bounds(seg)) == 8
    seg.frames = 200
    assert R.chunk_bounds(seg) == [(0, 200)]  # under two chunks: whole
    assert R.chunk_bounds(R.Segment(0, 0.0, 30.0, ["s1"], frames=900, fps=30.0), zoom={"events": []}) == [(0, 900)]
    path = SegmentPath(t_in=0.0, t_out=1.0, track_id=None, crop_w=608, crop_h=1080, xs=list(range(0, 60, 2)), fps=30.0)
    assert R._sendcmd(path, 0, 30) == path.sendcmd_text()


def test_the_key_follows_content_not_paths(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"video")
    png_a, png_b = tmp_path / "a.png", tmp_path / "b.png"
    png_a.write_bytes(b"caption")
    png_b.write_bytes(b"caption")

    def enc(png, threads="2", src_path=src):
        return R.Encode(["-i", str(src_path), "-i", str(png), "-threads", threads], tmp_path / "seg_01.mp4",
                        {str(src_path): R._source_id(src_path), str(png): R._file_id(png)})
    assert enc(png_a).key() == enc(png_b).key()  # same bytes under another name (a segment moved after a drop)
    assert enc(png_a).key() != enc(png_a, threads="4").key()  # x264's output depends on its thread count
    png_b.write_bytes(b"other caption")
    assert enc(png_a).key() != enc(png_b).key()
    before = enc(png_a).key()
    time.sleep(0.01)
    src.write_bytes(b"video, re-ingested")
    assert enc(png_a).key() != before


def test_prune_keeps_the_render_just_made_and_drops_the_oldest(tmp_path):
    now = time.time()
    for k, (name, age) in enumerate([("cur1", 0), ("cur2", 0), ("old1", 100), ("old2", 200), ("old3", 300)]):
        p = tmp_path / f"{name}.mp4"
        p.write_bytes(b"x" * 100)
        os.utime(p, (now - age, now - age))
    stale = tmp_path / "dead.123.456.part"
    stale.write_bytes(b"x")
    os.utime(stale, (now - 2 * R.SEGCACHE_PART_MAX_AGE_S,) * 2)
    fresh = tmp_path / "live.123.789.part"
    fresh.write_bytes(b"x")
    # budget 2 x 200 bytes: the two current files and the two newest old ones fit
    assert R.prune_cache(tmp_path, {"cur1", "cur2"}, renders=2) == 1
    assert sorted(p.name for p in tmp_path.iterdir()) == ["cur1.mp4", "cur2.mp4", "live.123.789.part", "old1.mp4",
                                                          "old2.mp4"]


def test_progress_counts_segments_landing_out_of_order():
    from montivo.server.runs import parse_log, progress
    lines = [(0.0, "montivo render: runs/x/edl_v3.json -> runs/x/cut_v3.mp4"),
             (0.1, "render: 8 spans -> 3 segments (1000 frames), source a.mp4, track head"),
             (0.2, "render: seg 3 ... (100 frames, s5) track 0, 2 overlays, cached"),
             (5.0, "render: seg 1 ... (600 frames, s1+s2) track 0, 50 overlays")]
    p = progress(parse_log("\n".join(f"[{t:7.1f}s] {m}" for t, m in lines)), costs={"render_s_per_frame": 0.02})
    assert p["detail"] == "segment 3 of 3" and p["floor"] == pytest.approx(0.05 + 0.75 * 0.7)
    lines.append((6.0, "render: seg 2 ... (300 frames, s4) track 0, 9 overlays"))
    p = progress(parse_log("\n".join(f"[{t:7.1f}s] {m}" for t, m in lines)), costs={"render_s_per_frame": 0.02})
    assert p["detail"] == "audio and mux" and p["ceil"] == 0.98
