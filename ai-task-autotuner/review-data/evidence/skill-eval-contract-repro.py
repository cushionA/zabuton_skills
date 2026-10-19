import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from unittest import mock

SKILL = Path('/workspace/scratch/ai-task-autotuner-review-20261019/skill/ai-task-autotuner')
sys.path.insert(0, str(SKILL / 'scripts'))
spec = importlib.util.spec_from_file_location('flowtest', SKILL / 'tests/test_flow.py')
flow = importlib.util.module_from_spec(spec)
spec.loader.exec_module(flow)
import common, ledger, llm_call, run_experiment, workspace

def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)

out = {}
f = flow.FlowTest()
f.setUp()
try:
    f.run_py('quality_gate.py', str(f.ws))
    common.append_jsonl(f.ws / 'ledger.jsonl', {'event': 'propose', 'exp': 'E001', 'candidate': 'c001-rule', 'lever': 'baseline'})
    r1 = quiet(run_experiment.run, f.ws, 'c001-rule', 'dev', 'baseline', exp='E001')
    r2 = quiet(run_experiment.run, f.ws, 'c001-rule', 'dev', 'baseline', exp='E001', tag='r2')
    f.run_py('validate_eval.py', str(f.ws), '--runs', str(r1), str(r2), '--spotcheck', 'read all baseline errors and verified the grader')
    before_hash = workspace.eval_hash(f.ws)
    ch = common.read_json(f.ws / 'charter.json')
    data = Path(ch['data']['inputs'][0])
    rows = common.read_jsonl(data)
    for row in rows:
        if row['id'] < 'u06':
            row['item_keys'] = [k for k in row['item_keys'] if not k.startswith('fine-')]
    data.write_text(''.join(json.dumps(row) + '\n' for row in rows))
    f.run_py('quality_gate.py', str(f.ws))
    validation_ok = run_experiment._validation_ok(f.ws)
    previous = ledger._exp_runs(f.ws, 'E001', 'dev')
    r3 = quiet(run_experiment.run, f.ws, 'c001-rule', 'dev', 'confirm', tag='after-data-correction')
    out['data_correction'] = {
        'same_eval_hash': workspace.eval_hash(f.ws) == before_hash,
        'validation_ok_after_reaudit': validation_ok,
        'old_run_stale_flags': [r['stale'] for r in previous],
        'old_primary': common.read_json(r1 / 'summary.json')['primary'],
        'same_candidate_new_data_primary': common.read_json(r3 / 'summary.json')['primary'],
    }
finally:
    f.tearDown()

f = flow.FlowTest()
f.setUp()
try:
    f.run_py('quality_gate.py', str(f.ws))
    common.append_jsonl(f.ws / 'ledger.jsonl', {'event': 'propose', 'exp': 'E001', 'candidate': 'c002-llm', 'lever': 'baseline'})
    def fake(prompt, system=None, **kw):
        rec = flow.fake_call(prompt, system, **kw)
        rec['model_served'] = kw['model']
        if kw['model'] == 'haiku':
            for item in rec['json']['items']:
                if item['key'].startswith('fine-'):
                    item['label'] = 'neg'
        return rec
    with mock.patch.object(llm_call, 'call', side_effect=fake):
        r1 = quiet(run_experiment.run, f.ws, 'c002-llm', 'dev', 'baseline', exp='E001')
        ch = common.read_json(f.ws / 'charter.json')
        ch['backend']['model'] = 'sonnet'
        common.write_json(f.ws / 'charter.json', ch)
        r2 = quiet(run_experiment.run, f.ws, 'c002-llm', 'dev', 'baseline', exp='E001', tag='changed-global-model')
    runs = ledger._exp_runs(f.ws, 'E001', 'dev')
    m1, m2 = [common.read_json(r / 'meta.json') for r in (r1, r2)]
    pool = ledger._pool(runs, 'cost_usd_per_unit')
    out['effective_config_mixing'] = {
        'candidate_hash_equal': m1['candidate_hash'] == m2['candidate_hash'],
        'models_in_runs': [m1['config']['model'], m2['config']['model']],
        'primaries': [r['summary']['primary'] for r in runs],
        'run_stale_flags': [r['stale'] for r in runs],
        'pooled_primary': pool['primary'],
        'pooled_n_runs': pool['n_runs'],
    }
finally:
    f.tearDown()

path = Path('/workspace/scratch/skill-eval-contract-evidence.json')
path.write_text(json.dumps(out, indent=2) + '\n')
print(json.dumps(out, indent=2))
