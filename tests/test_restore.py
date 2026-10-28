"""UX B1: an earlier version comes back as the newest one; nothing is deleted and the same edit keeps its review."""
import json

from montivo import human


def test_restore_copies_the_edl_forward_and_carries_its_review(tmp_path, monkeypatch):
    run = tmp_path / "runs" / "r"
    run.mkdir(parents=True)
    v1 = {"version": 1, "brief": "b", "spans": [{"id": "s1"}, {"id": "s2"}], "patches": []}
    v2 = {**v1, "version": 2, "spans": [{"id": "s1"}], "patches": [{"from": 1, "source": "human", "action": "drop"}]}
    for n, e in ((1, v1), (2, v2)):
        (run / f"edl_v{n}.json").write_text(json.dumps(e))
        (run / f"cut_v{n}.mp4").write_bytes(b"x")
    (run / "review_v1.json").write_text(json.dumps({"name": "review_v1", "version": 1, "cut": f"{run}/cut_v1.mp4",
                                                    "review": {"overall": 87, "pass": True}}))
    rendered = []
    monkeypatch.setattr(human, "_finish", lambda rd, p, n, *a: rendered.append(n) or {"version": n})
    out = human.restore(run, 1)
    assert out == {"version": 3} and rendered == [3]
    v3 = json.loads((run / "edl_v3.json").read_text())
    assert v3["spans"] == v1["spans"] and v3["version"] == 3
    assert v3["patches"][-1]["action"] == "restore" and v3["patches"][-1]["from"] == 1
    rv = json.loads((run / "review_v3.json").read_text())
    assert rv["carried_from"] == 1 and rv["name"] == "review_v3" and rv["cut"].endswith("cut_v3.mp4")
    assert (run / "edl_v2.json").is_file()  # nothing after it is deleted
