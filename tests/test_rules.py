"""Quick rule checks (synthetic titles). Run:  python tests/test_rules.py"""
import pathlib
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from radar.filters import Classifier  # noqa: E402
from radar import sources, net  # noqa: E402

cfg = tomllib.loads((ROOT / "config.toml").read_text("utf-8"))
clf = Classifier(cfg)


def topics(title, abstract="", authors=(), source=""):
    c = clf.classify({"title": title, "abstract": abstract, "authors": list(authors), "source": source})
    return c


CASES = [
    # (title, expected topic set or None)
    ("Atomic layer deposition of FeOx overcoats on Pt nanoparticles for selective hydrogenation", {"ald"}),
    ("Area-selective ALD of Al2O3 gate dielectric on MoS2 transistors", None),
    ("Atomic Layer Deposition of Ta2O5 on Nickel-rich Cathodes for Lithium-ion Battery", None),
    ("ALD-derived Ru single-atom catalyst for ammonia synthesis under mild conditions", {"ald", "nh3"}),
    ("Electrified Joule heating enables dynamic ammonia synthesis on Ru catalysts", {"joule", "nh3"}),
    ("Carbothermal shock synthesis of high-entropy alloy nanoparticles", {"joule"}),
    ("MHD Casson nanofluid flow with Joule heating over a stretching sheet", None),
    ("Ammonia combustion characteristics in a swirl burner", None),
    ("Effect of manure management on soil ammonia emission", None),
    ("Emerging strategies for catalytic ammonia synthesis: regulation of catalyst electronic structure", {"nh3"}),
    ("Ceria support enhances activity of CoNi catalysts for ammonia synthesis under magnetic heating", {"nh3", "joule"}),
    ("Thermal shock resistance of alumina refractories", None),
]

fails = 0
for title, want in CASES:
    c = topics(title)
    got = set(c["topics"]) if c else None
    ok = got == want
    fails += not ok
    print(("PASS" if ok else "FAIL"), sorted(got) if got else None, "|", title)

# tiers
c = topics("Emerging strategies for catalytic ammonia synthesis: regulation of catalyst electronic structure")
assert c["tier"] == "direct", ("electronic structure must not count as electrocatalysis", c)
c = topics("Electrocatalytic nitrate-to-ammonia conversion on Cu")
assert c["tier"] == "background", c
assert topics("Joule-heated Ru/carbon catalysts for ammonia production")["tier"] == "cross"
assert topics("Electrified dry reforming of methane via direct Joule heating of Ni/Al2O3 washcoated SiSiC foams")["tier"] == "transferable"
assert topics("导电泡沫直接电阻加热驱动氨裂解制氢")["tier"] == "transferable"
assert topics("程序升温与快速淬冷调制Fe表面N₂/H₂气相合成氨的微观动力学")["tier"] == "cross"
assert topics("Engineering Mass Transport for Scalable Acidic Nitrate‐to‐Ammonia Electrolysis")["tier"] == "background"
assert topics("An electroplastic discrete dislocation dynamics model incorporating Joule-heating-induced temperature fields for HCP alloys") is None
assert topics("High-Performance Ce-Doped HfO2 Ferroelectric Thin Films by Atomic Layer Deposition") is None

# watch authors: needs a topic hit first, then tags
c = topics("Atomic layer deposition overcoat stabilises Pd catalysts", authors=["Junling Lu", "A. Other"])
assert c and c["watch"] == ["Junling Lu"], c
c = topics("Flash Joule heating converts plastic waste to graphene", authors=["James Tour"])
assert c and c["watch"] == ["James M. Tour"], c
assert topics("A study of polymers", authors=["Junling Lu"]) is None

# top journal detection
assert topics("Ammonia synthesis on Ru", source="Nature Catalysis")["top"]
assert not topics("Ammonia synthesis on Ru", source="Nature Environment and Pollution Technology")["top"]
assert topics("Ammonia synthesis on Ru", source="JACS (ASAP)")["top"]

# RSS 1.0 / RDF parsing (Nature style) + RSS 2.0 with DOI in link
RDF = b"""<?xml version="1.0"?><rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#" xmlns="http://purl.org/rss/1.0/"
 xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:prism="http://prismstandard.org/namespaces/basic/2.0/" xmlns:content="http://purl.org/rss/1.0/modules/content/">
<item rdf:about="https://www.nature.com/articles/s41929-026-00001-x"><title>Pulsed Joule heating boosts ammonia synthesis</title>
<link>https://www.nature.com/articles/s41929-026-00001-x</link><content:encoded>&lt;p&gt;Abstract text about Ru catalysts&lt;/p&gt;</content:encoded>
<dc:creator>A. Author</dc:creator><dc:identifier>doi:10.1038/s41929-026-00001-x</dc:identifier><dc:date>2026-10-27</dc:date><prism:doi>10.1038/s41929-026-00001-x</prism:doi></item></rdf:RDF>"""
RSS2 = b"""<?xml version="1.0"?><rss version="2.0"><channel><item><title>ALD overcoated catalysts</title>
<link>https://pubs.acs.org/doi/10.1021/jacs.6b00001?af=R</link><description>Catalyst stability</description><pubDate>Tue, 27 Oct 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
orig = net.get
try:
    net.get = lambda url, **k: RDF
    a = sources.rss("Nature Catalysis", "x")[0]
    assert a["doi"] == "10.1038/s41929-026-00001-x" and a["date"] == "2026-10-27" and "Ru catalysts" in a["abstract"], a
    net.get = lambda url, **k: RSS2
    b = sources.rss("JACS (ASAP)", "x")[0]
    assert b["doi"] == "10.1021/jacs.6b00001" and b["date"] == "2026-10-27", b
finally:
    net.get = orig

print(f"\n{len(CASES) - fails}/{len(CASES)} title cases passed; assertion checks passed")
sys.exit(1 if fails else 0)
