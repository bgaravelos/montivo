"""`python -m montivo.human <op> RUN --from N` refuses a version that is not there (or under 1) with one line and exit 1,
not a traceback (and `--from 0` no longer means the latest); `reorder --before` itself is refused the same way."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from montivo import human

ROOT = Path(__file__).resolve().parents[1]


def _run_dir(tmp_path: Path) -> Path:
    d = tmp_path / "runs" / "x"
    d.mkdir(parents=True)
    for n in (1, 2):
        (d / f"edl_v{n}.json").write_text(json.dumps({"spans": [{"id": "s1"}, {"id": "s2"}, {"id": "s3"}]}))
    return d


def _cli(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in ("MODEL_API_KEY", "BASETEN_API_KEY")}
    env["PYTHONPATH"] = str(ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run([sys.executable, "-m", "montivo.human", *args], cwd=cwd, env=env, capture_output=True,
                          text=True, timeout=60)


@pytest.mark.parametrize("op", [["drop", "--span", "s1"], ["reorder", "--span", "s1", "--end"],
                                ["music", "--off"], ["trim", "--span", "s1", "--edge", "in", "--word", "0"]])
@pytest.mark.parametrize("frm,want", [("9", "edl_v9.json not found"), ("0", "versions start at 1"),
                                      ("-2", "versions start at 1")])
def test_from_missing_or_below_one_is_refused(tmp_path, op, frm, want):
    d = _run_dir(tmp_path)
    r = _cli(op[0], str(d), *op[1:], "--from", frm, cwd=tmp_path)
    assert r.returncode != 0
    assert want in r.stderr
    assert "Traceback" not in r.stderr
    assert sorted(p.name for p in d.glob("edl_v*.json")) == ["edl_v1.json", "edl_v2.json"]  # nothing written


def test_missing_version_names_the_ones_there(tmp_path):
    d = _run_dir(tmp_path)
    with pytest.raises(SystemExit, match=r"versions: 1, 2"):
        human.from_version(d, 7)


def test_from_version_default_and_named(tmp_path):
    d = _run_dir(tmp_path)
    assert human.from_version(d, None) == 2
    assert human.from_version(d, 1) == 1


def test_reorder_before_itself_is_refused(tmp_path):
    d = _run_dir(tmp_path)
    r = _cli("reorder", str(d), "--span", "s2", "--before", "s2", cwd=tmp_path)
    assert r.returncode != 0
    assert "cannot move s2 before itself" in r.stderr
    assert "Traceback" not in r.stderr and "ValueError" not in r.stderr
    assert not (d / "edl_v3.json").exists()
