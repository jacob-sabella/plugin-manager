#!/usr/bin/env python3
"""Refresh tests/catalog-snapshot.json, the frozen extract of the live catalog the offline tests browse (tests/run.sh).

  tests/snapshot_catalog.py [catalog.json or URL]     default: https://open-mpc.github.io/catalog.json

Run by hand when the catalog's shape changes; the tests never fetch it. Kept: the newest entries of each kind, every entry a fake
device has installed, the catalog's own top-level fields. Dropped: all but the newest version of each entry and the bulky per-version
fields nothing on the device reads (notes, flags, warnings). tests/fixture.py points every download at a stand-in zip."""
import json, os, sys, urllib.request

here = os.path.dirname(os.path.abspath(__file__))
src = sys.argv[1] if len(sys.argv) > 1 else "https://open-mpc.github.io/catalog.json"
c = json.load(urllib.request.urlopen(src) if "://" in src else open(src))
KEEP = {"mpc-plaits", "acid", "plugin-manager-g2"}          # what tests/devices/*.sh and tests/run.sh name
PER_KIND = {"instrument": 14, "effect": 14, "addin": 3}
DROP = ("notes", "flags", "warnings", "os_compat_why", "tested", "hooks", "chains", "downloads", "source_url")
plugins = sorted((p for p in c["plugins"] if p.get("versions")), key=lambda p: p["versions"][0].get("date", ""), reverse=True)
out, n = [], {}
for p in plugins:
    k = p.get("kind", "instrument")
    if p["id"] not in KEEP and n.get(k, 0) >= PER_KIND.get(k, 0): continue
    n[k] = n.get(k, 0) + 1
    p = dict(p, versions=[{f: v for f, v in p["versions"][0].items() if f not in DROP}])
    p.pop("screenshot", None)
    out.append(p)
c["plugins"] = out
c["collections"] = []
json.dump(c, open(os.path.join(here, "catalog-snapshot.json"), "w"), indent=1, sort_keys=True)
print("catalog-snapshot.json: %d entries (%s) from %s, generated %s" % (len(out), ", ".join("%d %s" % (v, k) for k, v in sorted(n.items())), src, c.get("generated")))
