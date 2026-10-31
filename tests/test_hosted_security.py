"""The hosted demo's security fixes of 31/10 (cloud security check): requests from other sites, the judge id only at
sign-in, a run id the server picks, the copy of a sample, the sign-in rate limit, bad input as 400."""
import json

from fastapi.testclient import TestClient

import montivo.server.app as A
from montivo.server import hosted as H
from tests.test_hosted import fake_jobs, hosted_env, ledger  # noqa: F401 (fixture)
from tests.test_ui import _clip, _run_folder


def signed_in(tmp_path, pw="pw"):
    c = TestClient(A.create_app(tmp_path))
    assert c.post("/api/login", data={"password": pw}, follow_redirects=False).status_code == 303
    return c


def host(tmp_path, hosted_env, budget=True):
    d = _run_folder(tmp_path)
    ledger(d, 0.36)
    hosted_env.setenv("MONTIVO_PASSWORD", "pw")
    if budget:
        hosted_env.setenv("MONTIVO_BUDGET_USD", "15")
    return d


def test_a_post_from_another_site_is_refused(tmp_path, hosted_env):
    host(tmp_path, hosted_env)
    fake_jobs(hosted_env)
    c = signed_in(tmp_path)
    # modal.run is not on the Public Suffix List: another *.modal.run app is the "same site" and SameSite=Lax lets
    # its POSTs carry the judge's cookies
    for hdr in ({"origin": "https://evil--app.modal.run"}, {"sec-fetch-site": "same-site"},
                {"sec-fetch-site": "cross-site", "origin": "https://evil.example"}):
        assert c.post("/api/runs/r1/archive", headers=hdr).status_code == 403, hdr
        assert c.post("/api/runs/r1/drop", json={"span": "s1"}, headers=hdr).status_code == 403, hdr
    assert not (tmp_path / "runs" / "_archive").exists() and not list((tmp_path / "runs").glob("r1-*"))
    assert c.post("/api/login", data={"password": "pw"}, headers={"origin": "https://evil.example"}).status_code == 403
    # the page's own requests: same origin by either header, or a client that sends neither (not a browser)
    ok = {"sec-fetch-site": "same-origin", "origin": "http://testserver"}
    assert c.post("/api/runs/r1/drop", json={"span": "s1"}, headers=ok).status_code == 200
    assert c.post("/api/runs/r1/drop", json={"span": "s1"}, headers={"origin": "http://testserver"}).status_code == 200
    assert c.get("/api/runs", headers={"origin": "https://evil.example"}).status_code == 200  # reads are not refused


def test_the_judge_id_comes_only_with_the_sign_in(tmp_path, hosted_env):
    host(tmp_path, hosted_env)
    hosted_env.setenv("MONTIVO_MAX_UPLOADS", "1")
    c = TestClient(A.create_app(tmp_path))
    c.cookies.set(H.COOKIE, H.token("pw"))  # the password cookie alone: a new judge each request used to be minted
    for _ in range(3):
        r = c.put("/api/footage/x.mp4", content=b"x")
        assert r.status_code == 401 and not r.cookies.get(H.JUDGE)
    assert not list((tmp_path / "footage").glob("x*.mp4"))


def test_all_judges_together_have_an_upload_quota(tmp_path, hosted_env, monkeypatch):
    host(tmp_path, hosted_env)
    hosted_env.setenv("MONTIVO_MAX_UPLOADS", "5")
    hosted_env.setenv("MONTIVO_MAX_UPLOADS_ALL", "2")
    _clip(tmp_path / "src.mp4")
    data = (tmp_path / "src.mp4").read_bytes()
    codes = [signed_in(tmp_path).put(f"/api/footage/u{k}.mp4", content=data).status_code for k in range(3)]
    assert codes == [200, 200, 429]  # three sign-ins are three judges, the third upload still waits


def test_the_host_picks_the_run_id_and_claims_its_folder(tmp_path, hosted_env):
    host(tmp_path, hosted_env)
    seen = fake_jobs(hosted_env)
    c = signed_in(tmp_path)
    clip = tmp_path / "footage" / "clip.mp4"
    clip.parent.mkdir(exist_ok=True)
    _clip(clip)
    owners = tmp_path / "footage" / ".owners.json"
    owners.write_text(json.dumps({"clip.mp4": c.cookies.get(H.JUDGE).partition(".")[0]}))
    # an id from the client could be another judge's copy name, runs/<sample>-<their id>
    r = c.post("/api/runs", json={"footage": "footage/clip.mp4", "brief": "b", "id": "r1-6fa922fb", "review": False})
    assert r.status_code == 200 and r.json()["id"].startswith("ui-") and not (tmp_path / "runs" / "r1-6fa922fb").exists()
    r2 = c.post("/api/runs", json={"footage": "footage/clip.mp4", "brief": "b", "review": False})
    assert r2.json()["id"] != r.json()["id"]  # two in one minute
    assert seen[-1][seen[-1].index("--out") + 1] == f"runs/{r2.json()['id']}"


def test_a_copy_folder_someone_else_made_is_never_used(tmp_path, hosted_env):
    d = host(tmp_path, hosted_env)
    fake_jobs(hosted_env)
    c = signed_in(tmp_path)
    jid = c.cookies.get(H.JUDGE).partition(".")[0]
    trap = tmp_path / "runs" / f"r1-{jid}"
    A.shutil.copytree(d, trap)
    (trap / "ui").mkdir(exist_ok=True)
    (trap / "ui" / "owner").write_text("ffff0000")
    assert c.post("/api/runs/r1/drop", json={"span": "s1"}).status_code == 409
    A.shutil.rmtree(trap)
    # other judges' drafts and version names stay on the sample; the edit media comes along
    (d / "ui" / "draft" / "ffff0000").mkdir(parents=True)
    (d / "ui" / "draft" / "ffff0000" / "state.json").write_text("{}")
    (d / "ui" / "labels").mkdir()
    (d / "ui" / "labels" / "ffff0000.json").write_text("{}")
    (d / "ui" / "labels" / f"{jid}.json").write_text('{"labels": {"2": {"star": true}}}')
    (d / "ui" / "cache" / "edit").mkdir(parents=True)
    (d / "ui" / "cache" / "edit" / "proxy.mp4").write_bytes(b"p")
    assert c.post("/api/runs/r1/drop", json={"span": "s1"}).json()["job"]["run"] == f"r1-{jid}"
    assert not (trap / "ui" / "draft").exists() and not (trap / "ui" / "labels" / "ffff0000.json").exists()
    assert (trap / "ui" / "labels" / f"{jid}.json").is_file() and (trap / "ui" / "cache" / "edit" / "proxy.mp4").is_file()


def test_wrong_passwords_are_rate_limited(tmp_path, hosted_env, monkeypatch):
    host(tmp_path, hosted_env, budget=False)
    real = H.asyncio.sleep

    async def no_wait(s):
        await real(0)
    monkeypatch.setattr(H.asyncio, "sleep", no_wait)
    c = TestClient(A.create_app(tmp_path))
    codes = [c.post("/api/login", data={"password": "no"}, follow_redirects=False).status_code
             for _ in range(H.LOGIN_FAILS_PER_MIN + 2)]
    assert codes[:H.LOGIN_FAILS_PER_MIN] == [303] * H.LOGIN_FAILS_PER_MIN and codes[-1] == 429
    assert c.post("/api/login", data={"password": "pw"}, follow_redirects=False).status_code == 429  # wait a minute
    other = TestClient(A.create_app(tmp_path))  # another address is not held back by this one
    r = other.post("/api/login", data={"password": "pw"}, headers={"x-forwarded-for": "10.0.0.9"}, follow_redirects=False)
    assert r.status_code == 303


def test_the_cookies_are_secure_behind_https(tmp_path, hosted_env):
    host(tmp_path, hosted_env, budget=False)
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/login", data={"password": "pw"}, headers={"x-forwarded-proto": "https"}, follow_redirects=False)
    jar = [h for h in r.headers.get_list("set-cookie")]
    assert all("Secure" in h for h in jar) and len(jar) == 2


def test_dot_segments_and_null_bytes(tmp_path, hosted_env):
    host(tmp_path, hosted_env)
    # httpx removes ../ from a URL, the encoded dots reach the server as /s/../index.html
    r = TestClient(A.create_app(tmp_path)).get("/s/%2e%2e/index.html", follow_redirects=False)
    assert r.status_code == 404  # no UI without the sign-in
    c = signed_in(tmp_path)
    assert c.get("/api/runs/r1/file/a%00b").status_code == 404


def test_bad_input_is_a_400_not_a_500(tmp_path, hosted_env):
    _run_folder(tmp_path)
    fake_jobs(hosted_env)
    c = TestClient(A.create_app(tmp_path), raise_server_exceptions=False)
    assert c.post("/api/runs/r1/drop", json={"span": "s1", "from": "x"}).status_code == 400
    assert c.post("/api/runs/r1/drop", content=b"not json", headers={"content-type": "application/json"}).status_code == 400
    assert c.post("/api/runs/r1/drop", json=["a"]).status_code == 400
    assert c.post("/api/runs", content=b"[1]", headers={"content-type": "application/json"}).status_code == 400


def test_paid_export_calls_on_a_sample_go_to_the_judges_copy(tmp_path, hosted_env):
    host(tmp_path, hosted_env)
    calls = []
    hosted_env.setattr(A.subprocess, "run", lambda args, **k: calls.append(args) or type(
        "P", (), {"returncode": 0, "stdout": json.dumps({"platforms": {}}), "stderr": ""})())
    (tmp_path / "runs" / "r1" / "cut_v2.mp4").write_bytes(b"cut")
    c = signed_in(tmp_path)
    jid = c.cookies.get(H.JUDGE).partition(".")[0]
    r = c.post("/api/runs/r1/export/post", json={"v": 2})
    assert r.status_code == 200 and r.json()["run"] == f"r1-{jid}"
    assert calls[-1][3] == f"runs/r1-{jid}"  # post_v2.json lands in the copy, never in the sample
