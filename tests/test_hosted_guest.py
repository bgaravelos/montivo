"""Guest entry on the hosted demo (MONTIVO_GUEST, 4/11): the submission form has no private field for a password, so
judges come in as guests: a judge id the server signs, with a judge's own copies, budget share and upload quota."""
from fastapi.testclient import TestClient

import montivo.server.app as A
from montivo.server import hosted as H
from tests.test_hosted import fake_jobs, hosted_env, ledger  # noqa: F401 (fixture)
from tests.test_ui import _run_folder


def guest_host(tmp_path, hosted_env, password="pw"):
    d = _run_folder(tmp_path)
    ledger(d, 0.36)
    if password:
        hosted_env.setenv("MONTIVO_PASSWORD", password)
    hosted_env.setenv("MONTIVO_GUEST", "1")
    hosted_env.setenv("MONTIVO_BUDGET_USD", "40")
    return d


def guest(tmp_path, headers=None):
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/guest", headers=headers or {}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    return c, r


def test_guest_entry_is_off_unless_switched_on(tmp_path, hosted_env):
    _run_folder(tmp_path)
    hosted_env.setenv("MONTIVO_PASSWORD", "pw")
    c = TestClient(A.create_app(tmp_path))
    assert "Enter as guest" not in c.get("/login").text
    r = c.post("/api/guest", follow_redirects=False)
    assert r.status_code == 404 and not r.cookies.get(H.JUDGE)
    assert c.get("/api/runs").status_code == 401


def test_a_guest_gets_a_signed_judge_id_and_no_password(tmp_path, hosted_env):
    guest_host(tmp_path, hosted_env)
    c = TestClient(A.create_app(tmp_path))
    page = c.get("/login").text
    assert "Enter as guest" in page and "Sign in" in page and "password is in the submission" not in page
    assert c.get("/api/runs").status_code == 401  # no cookie yet: still the sign-in page
    c, r = guest(tmp_path)
    jid, _, _ = (r.cookies.get(H.JUDGE) or "").partition(".")
    assert len(jid) == 8 and r.cookies.get(H.JUDGE) == H.judge_cookie(H.judge_key(tmp_path), jid)
    assert not r.cookies.get(H.COOKIE)  # never the password cookie
    assert c.get("/api/runs").status_code == 200
    # the same browser keeps its id (its copies stay its own)
    again = c.post("/api/guest", follow_redirects=False)
    assert again.cookies.get(H.JUDGE).partition(".")[0] == jid
    # a made-up or forged judge cookie is not a guest
    f = TestClient(A.create_app(tmp_path))
    f.cookies.set(H.JUDGE, f"{jid}.0000000000000000000a")
    assert f.get("/api/runs").status_code == 401
    f.cookies.set(H.JUDGE, jid)
    assert f.get("/api/runs").status_code == 401


def test_two_guests_edit_their_own_copies(tmp_path, hosted_env):
    guest_host(tmp_path, hosted_env)
    fake_jobs(hosted_env)
    a, ra = guest(tmp_path)
    b, rb = guest(tmp_path)
    ja, jb = (r.cookies.get(H.JUDGE).partition(".")[0] for r in (ra, rb))
    assert ja != jb
    assert a.post("/api/runs/r1/drop", json={"span": "s1"}).status_code == 200
    assert (tmp_path / "runs" / f"r1-{ja}").is_dir() and not (tmp_path / "runs" / f"r1-{jb}").exists()
    ids_b = [r["id"] for r in b.get("/api/runs").json()["runs"]]
    assert f"r1-{ja}" not in ids_b and "r1" in ids_b


def test_guest_ids_are_rate_limited_per_address(tmp_path, hosted_env):
    guest_host(tmp_path, hosted_env)
    app = A.create_app(tmp_path)
    hdr = {"x-forwarded-for": "203.0.113.9"}
    for _ in range(H.GUESTS_PER_HOUR):
        r = TestClient(app).post("/api/guest", headers=hdr, follow_redirects=False)
        assert r.headers["location"] == "/" and r.cookies.get(H.JUDGE)
    r = TestClient(app).post("/api/guest", headers=hdr, follow_redirects=False)
    assert r.headers["location"] == "/login?error=2" and not r.cookies.get(H.JUDGE)
    assert "Too many guests" in TestClient(app).get("/login?error=2").text
    # a forged first hop is not a new address: the proxy's last hop counts
    r = TestClient(app).post("/api/guest", headers={"x-forwarded-for": "1.2.3.4, 203.0.113.9"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=2"
    # another address still gets in
    r = TestClient(app).post("/api/guest", headers={"x-forwarded-for": "198.51.100.7"}, follow_redirects=False)
    assert r.headers["location"] == "/"


def test_guest_entry_from_another_site_is_refused(tmp_path, hosted_env):
    guest_host(tmp_path, hosted_env)
    c = TestClient(A.create_app(tmp_path))
    r = c.post("/api/guest", headers={"origin": "https://evil--app.modal.run"}, follow_redirects=False)
    assert r.status_code == 403 and not r.cookies.get(H.JUDGE)


def test_a_guest_has_a_judges_budget_share(tmp_path, hosted_env):
    guest_host(tmp_path, hosted_env)
    hosted_env.setenv("MONTIVO_JUDGE_BUDGET_USD", "3")
    c, _ = guest(tmp_path)
    b = c.get("/api/runs").json()["budget"]
    assert b["cap"] == 40 and b["judge"] == {"cap": 3.0, "spent": 0.0, "left": 3.0}


def test_guests_without_a_password_and_the_password_still_works(tmp_path, hosted_env):
    guest_host(tmp_path, hosted_env, password=None)
    c, _ = guest(tmp_path)
    assert c.get("/api/runs").status_code == 200
    r = TestClient(A.create_app(tmp_path)).post("/api/login", data={"password": "anything"}, follow_redirects=False)
    assert r.headers["location"] == "/login?error=1"
    hosted_env.setenv("MONTIVO_PASSWORD", "pw")  # with a password too: the password sign-in is unchanged
    s = TestClient(A.create_app(tmp_path))
    assert s.post("/api/login", data={"password": "pw"}, follow_redirects=False).cookies.get(H.COOKIE)
    assert s.get("/api/runs").status_code == 200
