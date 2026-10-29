"""End-to-end offline run on a copy of the repo: fixtures for every network call, fake LLM key, dry-run push.
   python tests/test_pipeline.py"""
import json, os, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parent.parent
tmp = pathlib.Path(tempfile.mkdtemp(prefix="radar-"))
for name in ("radar", "config.toml", "data", "docs"):
    src = ROOT / name
    (shutil.copytree if src.is_dir() else shutil.copy)(src, tmp / name)
env = dict(os.environ, RADAR_FIXTURES=str(ROOT / "tests/fixtures"), RADAR_FIXTURE_ENRICH="1", RADAR_DRY_RUN="1",
           RADAR_ROOT=str(tmp), ZHIPU_API_KEY="test-key", RADAR_NOW="2026-10-29T08:00:00Z", MAINTAIN_MAX_MINUTES="1",
           LOOKBACK_DAYS="40")
env.pop("LLM_API_KEY", None)
state = json.loads((tmp / "data/state.json").read_text("utf-8"))
state.pop("last_push_at", None)
(tmp / "data/state.json").write_text(json.dumps(state), "utf-8")


def run(*args):
    r = subprocess.run([sys.executable, "-m", "radar", *args], cwd=tmp, env=env, capture_output=True, text=True, timeout=600)
    print(r.stdout[-3000:], r.stderr[-2000:])
    assert r.returncode == 0, r.stderr
    return r.stdout


out = run()
assert "[dry-run push]" in out, "expected a push (new papers or heartbeat)"
if "新增" in out:
    assert "> " in out and ("测试中文标题" in out or "摘要：" in out), "push lines must carry a brief"
out = run("--maintain")
idx = (tmp / "docs/data/index.js").read_text("utf-8")
data = json.loads(idx[idx.index("=") + 1:].rstrip().rstrip(";"))
assert data["briefs"] > 0, "maintenance should have written some briefs"
assert data["status"].get("sources"), "footer must keep the last daily source table"
assert not any(k.startswith("10.1002/ange.") for f in (tmp / "data/papers").glob("*.json")
               for k in json.loads(f.read_text("utf-8"))), "German-edition DOIs should be merged"
print("pipeline OK:", tmp, "briefs", data["briefs"], "key", data["briefKey"], "/", data["keyTotal"])
if not os.environ.get("KEEP"): shutil.rmtree(tmp)
