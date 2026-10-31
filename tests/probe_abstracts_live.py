"""Explicit bounded read-only probe: public DOI metadata, no secret/model/push calls."""
import json, os, pathlib, sys, time
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from radar import sources

if __name__ == '__main__':
    if len(sys.argv) < 2 or len(sys.argv) > 6:
        raise SystemExit('Supply 1-5 DOI values; no background or batch library scan')
    os.environ.pop('OPENALEX_API_KEY', None)
    progress={}
    found, stats=sources.find_abstracts(sys.argv[1:],deadline=time.time()+100,epmc_cap=5,crossref_cap=5,progress=progress)
    print(json.dumps({'services':stats,'progress':progress,
        'found':{doi:{'chars':len(ab),'excerpt':ab[:200]} for doi,ab in found.items()},
        'libraryChanged':False,'modelOrNotificationCalled':False},ensure_ascii=True,indent=2))
