"""Read-only reproduction of product-identification saved results; no model calls.

Rebuilds the task's queries from raw product records using archived dataset code
with in-memory path substitution, independently checks answers and scores,
and bootstraps question/product/frozen-family clusters.
"""
import collections
import importlib.util
import json
import sys
import types
from pathlib import Path

import numpy as np

ROOT = Path('/workspace/scratch/ai-task-autotuner-testdata-20261019')
WS = ROOT / 'iteration-1/eval-2-au-to-rakuten-product-identification/with_skill/outputs/ws'
SKILL = Path('/workspace/scratch/ai-task-autotuner-review-20261019/skill/ai-task-autotuner')
OUT = Path('/workspace/scratch/raw-review-product-evidence.json')
RAW = ROOT / 'inputs/sku-normal-v1/inputs/products.jsonl'
sys.dont_write_bytecode = True
sys.path.insert(0, str(SKILL / 'scripts'))
import common

def jread(p):
    return json.loads(p.read_text())

def jl(p):
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]

charter = jread(WS / 'charter.json')
charter['data']['inputs'] = [str(RAW)]
charter['skill_dir'] = str(SKILL)
source = (WS / 'eval/dataset.py').read_text()
source = source.replace('CHARTER = json.loads((WS / "charter.json").read_text(encoding="utf-8"))', 'CHARTER = ' + repr(charter))
ds = types.ModuleType('raw_review_product_dataset')
ds.__file__ = str(WS / 'eval/dataset.py')
exec(compile(source, ds.__file__, 'exec'), ds.__dict__)
built = ds._build()
exported = {q['id']: q for q in jl(WS / 'data_built/queries.jsonl')}
assert len(built['units']) == len(exported) == 304
assert all(q['input'] == built['units'][uid]['input'] and q['answer'] == built['answers'][uid]
           and q['group'] == built['meta'][uid]['group'] for uid, q in exported.items())
raw = {r['product_pair_id']: r for r in jl(RAW)}
splits = jread(WS / 'eval/splits.json')
split_of = {uid: s for s in ('dev', 'holdout') for uid in splits[s]}

# Independent reference check, distinct from dataset.answer_of.
for uid, unit in built['units'].items():
    pid, variant = uid.split('|')
    url = raw[pid]['rakuten']['url']
    hits = [c['cid'] for c in unit['input']['candidates'] if c['url'] == url]
    assert len(hits) == (1 if variant == 'present' else 0)
    assert built['answers'][uid] == (hits[0] if hits else 'none')

results = {}
for path in sorted((WS / 'runs').glob('*holdout*')):
    meta = jread(path / 'meta.json')
    run = meta['candidate']
    records = jl(path / 'outputs.jsonl')
    pred = {r['id']: r['output'].get('choice') if isinstance(r['output'], dict) else None for r in records}
    assert len(pred) == len(records) == len(splits['holdout'])
    items, excluded = [], []
    for uid in splits['holdout']:
        p = pred[uid]
        p = ('none' if p.strip().lower() == 'none' else p.strip()) if isinstance(p, str) else None
        ref = built['answers'][uid]
        rec = {'id': uid, 'unit': uid, 'score': int(p == ref), 'pred': p, 'ref': ref}
        (excluded if built['meta'][uid]['excluded'] else items).append(rec)
    saved = jread(path / 'score.json')
    saved_items = {i['id']: i for i in saved['items']}
    assert len(items) == len(saved_items)
    assert all(all(r[k] == saved_items[r['id']][k] for k in ('score','pred','ref')) for r in items)
    n = len(items)
    correct = sum(i['score'] for i in items)
    fa = sum(i['pred'] not in (None, 'none') and not i['score'] for i in items)
    results[run] = {'path': str(path), 'n': n, 'correct': correct, 'accuracy': correct/n,
                    'false_accepts': fa, 'false_accept_rate': fa/n,
                    'failed': sum(i['pred'] is None for i in items), 'items': items,
                    'excluded': excluded}

baseline = results['c000-baseline']['items']
final = results['c007-no-desc']['items']
base_map = {i['id']: i for i in baseline}
final_map = {i['id']: i for i in final}
ids = sorted(base_map)

def group_key(uid, level):
    return uid if level == 'question' else uid.split('|')[0] if level == 'product_pair' else built['meta'][uid]['group']

def cluster_stats(level, reps=200000, seed=20261019):
    groups = collections.defaultdict(list)
    for uid in ids:
        groups[group_key(uid, level)].append(uid)
    keys = sorted(groups)
    sizes = np.array([len(groups[g]) for g in keys])
    bsum = np.array([sum(base_map[uid]['score'] for uid in groups[g]) for g in keys])
    fsum = np.array([sum(final_map[uid]['score'] for uid in groups[g]) for g in keys])
    bfa = np.array([sum(base_map[uid]['pred'] not in (None, 'none') and not base_map[uid]['score'] for uid in groups[g]) for g in keys])
    ffa = np.array([sum(final_map[uid]['pred'] not in (None, 'none') and not final_map[uid]['score'] for uid in groups[g]) for g in keys])
    rng = np.random.default_rng(seed)
    draws = rng.integers(len(keys), size=(reps, len(keys)))
    den = sizes[draws].sum(axis=1)
    ci = lambda sums: np.quantile(sums[draws].sum(axis=1)/den, [0.025, 0.975]).tolist()
    a = [dict(i, unit=group_key(i['id'], level)) for i in baseline]
    b = [dict(i, unit=group_key(i['id'], level)) for i in final]
    return {'clusters': len(keys), 'size_distribution': dict(collections.Counter(map(int, sizes))),
            'accuracy_baseline_ci': ci(bsum), 'accuracy_final_ci': ci(fsum),
            'delta_ci': ci(fsum-bsum), 'false_accept_baseline_ci': ci(bfa),
            'false_accept_final_ci': ci(ffa), 'false_accept_delta_ci': ci(ffa-bfa),
            'common_paired_default_1000': common.paired_items(a, b),
            'nonzero_delta_clusters': [{'group': g, 'ids': groups[g], 'delta_sum': int(fsum[n]-bsum[n])}
                                       for n,g in enumerate(keys) if fsum[n] != bsum[n]]}

close = jl(WS / 'audit/close_read.jsonl')
overlaps = []
for r in close:
    pid = r['id']
    uids = [pid + '|' + v for v in ('present','removed')]
    overlaps.append({**r, 'split': split_of[uids[0]], 'excluded': built['meta'][uids[0]]['excluded'],
                     'baseline_correct': sum(base_map.get(uid,{}).get('score',0) for uid in uids) if uids[0] in base_map else None,
                     'final_correct': sum(final_map.get(uid,{}).get('score',0) for uid in uids) if uids[0] in final_map else None})

groups = collections.defaultdict(list)
for pid,r in raw.items():
    groups[built['meta'][pid+'|present']['group']].append(pid)
group_info = []
for g,pids in sorted(groups.items()):
    urls = sorted({raw[pid]['rakuten']['url'] for pid in pids})
    group_info.append({'group': g, 'pair_ids': pids, 'n_rakuten_listings': len(urls),
                       'rakuten_urls': urls, 'split': split_of[pids[0]+'|present'],
                       'max_pairwise_title_jaccard': max([ds._jac(ds._bigrams(raw[p]['rakuten']['title']),ds._bigrams(raw[q]['rakuten']['title']))
                                                        for p in pids for q in pids if p!=q] or [1.0])})

counts = collections.Counter((r['split'], r['excluded']) for r in overlaps)
dev_repeat_paths = sorted((WS / 'runs').glob('E001__dev__baseline*'))
dev_repeats = [jread(p / 'score.json')['items'] for p in dev_repeat_paths]
noise_grouped = {}
for level in ('question', 'product_pair', 'frozen_family'):
    a = [dict(i, unit=group_key(i['id'], level)) for i in dev_repeats[0]]
    b = [dict(i, unit=group_key(i['id'], level)) for i in dev_repeats[1]]
    noise_grouped[level] = common.paired_items(a, b, n_boot=20000)

overlap_sensitivity = {}
direct_pairs = {r['id'] for r in close}
viewed_families = {g['group'] for g in group_info if set(g['pair_ids']) & direct_pairs}
family_pairs = {pid for g in group_info if g['group'] in viewed_families for pid in g['pair_ids']}
for scope, pids in [('drop_directly_close_read_pairs', direct_pairs), ('drop_families_of_close_read_pairs', family_pairs)]:
    overlap_sensitivity[scope] = {}
    for run, res in results.items():
        kept = [i for i in res['items'] if i['id'].split('|')[0] not in pids]
        overlap_sensitivity[scope][run] = {'n':len(kept), 'correct':sum(i['score'] for i in kept), 'accuracy':sum(i['score'] for i in kept)/len(kept)}
payload = {'raw_source': str(RAW), 'ws': str(WS), 'query_rebuild_matches_exported': True,
    'independent_reference_and_saved_item_score_match': True,
    'bootstrap': {'method': 'Percentile paired cluster bootstrap, micro-average ratio, clusters sampled with replacement at fixed cluster count',
                  'repetitions': 200000, 'numpy_generator': 'default_rng', 'seed': 20261019},
    'runs': results, 'bootstrap_by_level': {level:cluster_stats(level) for level in ('question','product_pair','frozen_family')},
    'close_read_split_counts': {f'{s}:excluded={e}':n for (s,e),n in counts.items()},
    'close_read_overlaps': overlaps,
    'sensitivity_dropping_audited_inputs_not_a_fresh_holdout': overlap_sensitivity,
    'dev_repeated_baseline_noise_by_level': noise_grouped,
    'split_counts': {s: {'questions':len(splits[s]), 'product_pairs':len({uid.split('|')[0] for uid in splits[s]}),
                        'frozen_families':len({built['meta'][uid]['group'] for uid in splits[s]}),
                        'scored':sum(not built['meta'][uid]['excluded'] for uid in splits[s])} for s in ('dev','holdout')},
    'group_overlap_across_dev_holdout': sorted({built['meta'][uid]['group'] for uid in splits['dev']} & {built['meta'][uid]['group'] for uid in splits['holdout']}),
    'all_families':group_info}
OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({k:v for k,v in payload.items() if k not in ('runs','all_families','close_read_overlaps')}, ensure_ascii=False, indent=2))
print('SCORES', {k:{q:r[q] for q in ('n','correct','accuracy','false_accepts','false_accept_rate','failed')} for k,r in results.items()})
print('EVIDENCE', OUT)
