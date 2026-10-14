"""work/pipeline_state/*.json → PostgreSQL(public.pipeline_state) 로 옮기고, 파일과 DB 가 같은지 대조한다.

    backend\\.venv\\Scripts\\python.exe -X utf8 tools\\state_to_pg.py            # 대조만
    backend\\.venv\\Scripts\\python.exe -X utf8 tools\\state_to_pg.py --load     # 옮긴 뒤 대조(DB 쪽을 파일로 덮는다)

API(8180)를 멈춘 상태에서 돌린다. 파일은 지우지 않는다.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.database import init_db  # noqa: E402
from app.dbconf import is_pg  # noqa: E402
from app.pipeline import state as S  # noqa: E402

if not is_pg():
    raise SystemExit("TWN_PG_URL 이 없습니다 (backend/.env)")

init_db()
if "--load" in sys.argv:
    print("옮김:", S.import_from_files(overwrite=True))

diff = 0
files = sorted(p for p in S._DIR.glob("*.json") if p.name != S._ALIAS.name)
for f in files:
    want = json.loads(f.read_text(encoding="utf-8"))
    got = S.get_state(f.stem if not f.stem.isdigit() else int(f.stem))
    if got != want:
        diff += 1
        keys = sorted(k for k in set(want) | set(got) if want.get(k) != got.get(k))
        print(f"✘ {f.stem}: 다른 키 {keys[:8]}")
alias = json.loads(S._ALIAS.read_text(encoding="utf-8")) if S._ALIAS.exists() else {}
for k, v in alias.items():
    if str(S.canonical_id(int(k) if k.isdigit() else k)) != str(v):
        diff += 1
        print(f"✘ 별칭 {k} → {v} 가 DB 에서 {S.canonical_id(k)}")
print(f"상태 {len(files)}개 · 별칭 {len(alias)}개 대조 — " + ("모두 같음" if not diff else f"다름 {diff}건"))
sys.exit(1 if diff else 0)
