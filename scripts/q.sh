#!/usr/bin/env bash
# Developer helper: run one search against a local StarIndex and print a compact result list.
B=${STARINDEX_URL:-http://127.0.0.1:8765}/api
curl -s -X POST "$B/search" -H 'content-type: application/json' -d "$1" | python3 -c "
import sys, json
d = json.load(sys.stdin)
if 'error' in d:
    print(d); sys.exit()
print(f\"[{d['mode']}] total={d['total']} {d['took_ms']}ms reranked={d['reranked']} notices={d['notices']} corr={d['parsed']['corrections']}\")
for r in d['results'][:8]:
    m = r['matches'][0] if r['matches'] else None
    print('  ', r['repo']['full_name'], round(r['score'], 4), (m['path'] + ':' + str(m['start_line'])) if m else '')
"
