"""Build a small SQLite with the old FrontierRadar schema to test the importer (synthetic rows)."""
import json, sqlite3, sys
path = sys.argv[1]
con = sqlite3.connect(path)
con.executescript("""
CREATE TABLE records (id TEXT PRIMARY KEY,doi TEXT,title_norm TEXT,published TEXT,date_precision TEXT,tier TEXT,score INTEGER,search TEXT,data TEXT NOT NULL,first_seen TEXT NOT NULL,last_seen TEXT NOT NULL,semantic_hash TEXT);
CREATE TABLE user_state (record_id TEXT PRIMARY KEY,read INTEGER DEFAULT 0,favorite INTEGER DEFAULT 0,note TEXT DEFAULT '',updated TEXT);
CREATE TABLE observations (record_id TEXT,source_id TEXT,data TEXT,seen TEXT,PRIMARY KEY(record_id,source_id));
""")
rows = [
 ("r1","10.1000/old.1","Promoter effects on Ru catalysts for gas-phase ammonia synthesis from N2 and H2","2024-03-15","day","direct","Crossref"),
 ("r2","10.1000/old.2","Joule-heated Ru/carbon catalysts for ammonia production","2025-11","month","cross","OpenAlex"),
 ("r3","10.1000/old.3","Atomic layer deposition overcoats stabilise Co catalysts for ammonia synthesis","2026-08-02","day","cross","Crossref"),
 ("r4","10.1000/old.4","Ammonia synthesis over Fe catalysts in 2023","2023-06-01","day","direct","Crossref"),
 ("r5","10.1000/old.5","Protein folding with neural networks","2026-07-10","day","exclude","Crossref"),
 ("r6","","Self-limiting ALD nucleation of ruthenium on functionalized carbon nanotubes","2026-09-01","day","transferable","arXiv"),
]
for rid, doi, t, pub, prec, tier, src in rows:
    data = {"title": t, "doi": doi, "url": f"https://doi.org/{doi}" if doi else f"https://arxiv.org/abs/{rid}", "abstract": "", "authors": ["A. Author", "Junling Lu"] if rid=="r3" else ["A. Author"],
            "published": pub, "date_precision": prec, "venue": "Journal of Catalysis" if rid != "r6" else ""}
    con.execute("INSERT INTO records VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (rid, doi, t.lower(), pub, prec, tier, 1, "", json.dumps(data), "2026-09-29T09:00:00+08:00", "2026-09-30T00:00:00Z", ""))
    con.execute("INSERT INTO observations VALUES (?,?,?,?)", (rid, src.lower(), json.dumps({"source_name": src}), "2026-09-29"))
con.execute("INSERT INTO user_state VALUES ('r1',1,1,'重点读：助剂 Ba 的作用','2026-09-29')")
con.execute("INSERT INTO user_state VALUES ('r3',0,1,'','2026-09-29')")
con.commit(); con.close(); print("ok", path)
