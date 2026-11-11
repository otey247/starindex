"""Developer helper: POST a JSON body to a StarIndex streaming endpoint and print the events compactly.

    python scripts/sse.py /api/research '{"question": "..."}'
"""
import json
import sys
import urllib.request

base = "http://127.0.0.1:8765"
req = urllib.request.Request(base + sys.argv[1], data=sys.argv[2].encode(), headers={"content-type": "application/json"}, method="POST")
event = None
with urllib.request.urlopen(req, timeout=600) as resp:
    for raw in resp:
        line = raw.decode().rstrip("\n")
        if line.startswith("event:"):
            event = line[7:]
        elif line.startswith("data:"):
            d = json.loads(line[5:])
            if event == "evidence":
                print("evidence +%d" % len(d["evidence"]))
            elif event == "delta":
                pass
            elif event == "step":
                print(f"step {d['index']} [{d['kind']}] {d['title']} ok={d['ok']} repos={d['repo_names'][:3]} refs={d['evidence_refs'][:4]}\n      {d['summary'][:160]!r}")
            elif event == "done":
                r = d.get("report") or d
                print("DONE", r.get("status"), r.get("model"))
                print(r.get("answer_md", r))
                print("usage", r.get("usage"), "\nwarnings", r.get("warnings"), "\nunsupported", r.get("unsupported"), "\ndata", {k: v for k, v in (r.get("data") or {}).items() if k != "comparison"})
            else:
                print(event, json.dumps(d)[:400])
