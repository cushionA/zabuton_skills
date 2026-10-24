"""c000-baseline: the simplest reasonable design - one model call per unit, JSON out.

Candidates are immutable once measured: copy this directory to cNNN-<slug> and edit the copy.
Optional hooks: setup(ctx) runs once (fill ctx.state with dictionaries/indexes);
run_all(units, ctx) replaces run() when one call should serve several units.
"""
import json


def run(unit, ctx):
    system = ctx.prompt("system.txt")
    user = ctx.prompt("user.txt").replace("{{UNIT_JSON}}", json.dumps(unit["input"], ensure_ascii=False))
    r = ctx.llm(user, system, tag="main", expect_json=True)
    if not r["ok"]:
        raise RuntimeError(f"{r['error_kind']}: {r['error']}")
    if r["json"] is None:
        raise ValueError("unparseable model output")
    return r["json"]
