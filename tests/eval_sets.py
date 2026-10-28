"""Evaluate the merged classifier on: old gold cases (if available), real Crossref fixtures, old-site feed sample."""
import json, sys, pathlib, tomllib
ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radar.filters import Classifier, TIER_ZH
clf = Classifier(tomllib.loads((ROOT / "config.toml").read_text("utf-8")))
def show(t, a="", exp=None):
    r = clf.classify({"title": t, "abstract": a})
    tier = r["tier"] if r else "exclude"
    mark = "" if exp is None else ("✓" if tier == exp or (exp, tier) in {("uncertain","direct"),("uncertain","transferable")} else "✗")
    s = f"{mark}{'' if exp is None else exp[:5]+'→'}{TIER_ZH[tier]}{' '+str(r['score']) if r else ''} {'+'.join(r['topics']) if r else ''}"
    print(f"  {s:<34} {t[:88]}")
    return tier
gold = ROOT / "tests/fixtures/legacy_gold_cases.json"
if gold.exists():
    print("== 旧站 40 条判例（✓=一致或更积极地收入）")
    for x in json.loads(gold.read_text("utf-8")):
        show(x["title"], x.get("context",""), x["tier"])
print("== 今日真实 Crossref 样本")
for it in json.loads((ROOT/"tests/fixtures/crossref.json").read_text("utf-8"))["message"]["items"]:
    if it["type"] in ("journal-article",) and not it["DOI"].endswith(".s001"):
        show(it["title"][0])
