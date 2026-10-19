"""Read-only independent review of supplied SKU snapshot; no network or model calls.
Usage: python /workspace/scratch/raw-review-sku-recompute.py
"""
from pathlib import Path
import json, math, random, collections, importlib.util
BASE=Path('/workspace/scratch/ai-task-autotuner-testdata-20261019')
WS=BASE/'iteration-1/eval-1-sku-matching-no-labels/with_skill/outputs/ws'
DATA=BASE/'inputs/sku-normal-v1/inputs/products.jsonl'
RESULT=Path('/workspace/scratch/raw-review-sku-recomputed.json')

def read(p):return json.loads(p.read_text())
def lines(p):return [json.loads(l) for l in p.read_text().splitlines() if l.strip()] if p.exists() else []
records={r['product_pair_id']:r for r in lines(DATA)}
splits=read(WS/'eval/splits.json')
refs={}
for split in ('dev','holdout'):
 ref={}
 for ext in ('.jsonl','.adjudicated.jsonl'):
  for row in lines(WS/'eval/reference'/f'{split}{ext}'):ref[row['key']]=row
 refs[split]=ref

def label(r):
 d=r.get('decision');return f"{d}:{r.get('au_sku_key')}" if d in ('matched','conditional') else d

def link(l):return l.split(':',1)[1] if isinstance(l,str) and l.split(':',1)[0] in ('matched','conditional') and ':'in l else None

def independent_score(outputs, ids, split):
 items=[]; excluded=missing_ref=0
 for uid in ids:
  rec=records[uid]
  out=outputs.get(uid)
  preds={}
  if isinstance(out,dict):
   for r in out.get('results',[]) or []:
    if isinstance(r,dict) and r.get('rakuten_sku_key') is not None:preds.setdefault(r['rakuten_sku_key'],label(r))
  for row in rec['rakuten']['skus']:
   k=f"{uid}|{row['sku_key']}";e=refs[split].get(k)
   if e is None:missing_ref+=1;continue
   if e.get('exclude'):excluded+=1;continue
   acc=e.get('acceptable',[e.get('label')]);p=preds.get(row['sku_key']);ok=p in acc
   pl=link(p);rl={link(x) for x in acc if link(x)}
   wrong=bool(not ok and pl and 'review' not in acc and pl not in rl)
   overclaim=bool(not ok and pl and 'review' in acc and not rl)
   typ=bool(not ok and pl and pl in rl)
   missed=bool(not ok and not pl and rl)
   items.append({'id':k,'unit':uid,'pred':p,'ref':acc,'score':int(ok),'wrong_link':int(wrong),'overclaim':int(overclaim),'link_type':int(typ),'missed_link':int(missed),'missing_or_failed':int(out is None or row['sku_key'] not in preds)})
 n=len(items)
 metrics={'n_items':n,'correct':sum(i['score'] for i in items),'agreement':sum(i['score'] for i in items)/n if n else None,'n_excluded':excluded,'missing_reference':missing_ref}
 for metric in ('wrong_link','overclaim','link_type','missed_link','missing_or_failed'):
  c=sum(i[metric] for i in items);metrics[metric+'_count']=c;metrics[metric+'_rate']=c/n if n else None
 return metrics,items

all_runs={}
for r in sorted((WS/'runs').iterdir()):
 if not (r/'meta.json').exists():continue
 meta=read(r/'meta.json');summary=read(r/'summary.json')
 output_rows=lines(r/'outputs.jsonl');outputs={x['id']:x['output'] for x in output_rows if x.get('status')=='ok'}
 split=meta['split'];ids=meta.get('unit_ids') or [x['id'] for x in output_rows]
 # meta uses units list in some snapshots; output is authoritative set actually run.
 if not isinstance(ids,list) or any(x not in records for x in ids):ids=[x['id'] for x in output_rows]
 metrics,items=independent_score(outputs,ids,split)
 stored=read(r/'score.json');stored_items={x['id']:x for x in stored['items']}
 diffs=[i['id'] for i in items if i['score']!=stored_items.get(i['id'],{}).get('score')]
 calls=lines(r/'calls.jsonl');tokens=collections.Counter()
 for c in calls:tokens.update({k:v or 0 for k,v in (c.get('usage') or {}).items()})
 # Given snapshot list prices, USD per million; output includes thinking in usage.
 uncached=0;priced=0
 for c in calls:
  price={'claude-haiku-5-5':(.10,.50),'claude-sonnet-5-5':(2.,10.),'claude-opus-5-5':(4.,20.)}.get(c.get('model_served'))
  if price is None or c.get('usage') is None:continue
  u=c['usage'];uncached+=((u.get('input',0)+u.get('cache_read',0)+u.get('cache_write',0))*price[0]+u.get('output',0)*price[1])/1e6;priced+=1
 all_runs[r.name]={'metrics':metrics,'items':items,'stored_score_differences':diffs,'calls':len(calls),'cost_usd_sum':sum(c.get('cost_usd') or 0 for c in calls),'uncached_usd_from_usage':uncached,'priced_calls':priced,'summary_uncached_usd':summary.get('cost_uncached_list_usd'),'tokens':dict(tokens),'output_status':dict(collections.Counter(x.get('status') for x in output_rows))}

def percentile(a,p):
 a=sorted(a);idx=(len(a)-1)*p;lo=int(idx);hi=math.ceil(idx);return a[lo]+(a[hi]-a[lo])*(idx-lo)

def boot(diffs,unit_of,n_boot=20000):
 sums=collections.defaultdict(float);counts=collections.Counter()
 for k,v in sorted(diffs.items()):sums[unit_of(k)]+=v;counts[unit_of(k)]+=1
 keys=list(sums);rng=random.Random(0);boots=[]
 for _ in range(n_boot):
  pick=rng.choices(keys,k=len(keys));boots.append(sum(sums[k] for k in pick)/sum(counts[k] for k in pick))
 return {'units':len(keys),'delta':sum(diffs.values())/len(diffs),'ci_95':[percentile(boots,.025),percentile(boots,.975)],'band':2*math.sqrt(sum(v*v for v in sums.values()))/len(diffs),'bootstrap_replicates':n_boot,'seed':0}

def pool(prefix):
 rows=[v['items'] for k,v in all_runs.items() if k.startswith(prefix)];acc=collections.defaultdict(list)
 for rr in rows:
  for i in rr:acc[i['id']].append(i['score'])
 return {k:sum(v)/len(v) for k,v in acc.items()}
baseline=pool('E001__');c002=pool('E003__')
diff={k:c002[k]-baseline[k] for k in baseline.keys()&c002.keys()}
pair=lambda k:k.split('|',1)[0]
url=lambda k:records[pair(k)]['rakuten']['url']
comparison={name:boot(diff,f) for name,f in [('item',lambda k:k),('product_pair',pair),('rakuten_url',url)]}
baseruns=[v['items'] for k,v in all_runs.items() if k.startswith('E001__')]
a,b=({x['id']:x for x in rr} for rr in baseruns)
repeatdiff={k:b[k]['score']-a[k]['score'] for k in a.keys()&b.keys()}
repeatgroups={}
for k in a:
 if a[k]['pred']!=b[k]['pred']:
  u=pair(k);repeatgroups.setdefault(u,{'prediction_changed':0,'score_changed':0,'r1_correct':0,'r2_correct':0,'n_items':0})
for u,c in repeatgroups.items():
 its=[k for k in a if pair(k)==u]
 c.update({'prediction_changed':sum(a[k]['pred']!=b[k]['pred'] for k in its),'score_changed':sum(a[k]['score']!=b[k]['score'] for k in its),'r1_correct':sum(a[k]['score'] for k in its),'r2_correct':sum(b[k]['score'] for k in its),'n_items':len(its),'title':records[u]['rakuten']['title']})
# Execute only the local pure deterministic exact_pair function on all inputs.
spec=importlib.util.spec_from_file_location('review_pipeline',Path('/workspace/scratch/ai-task-autotuner-review-20261019/task_structures/01_sku-matching/pipeline/pipeline.py'));mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
rule={uid:mod.exact_pair(rec) for uid,rec in records.items()};rule={k:v for k,v in rule.items() if v is not None}
rule_info={'pairs':len(rule),'items':sum(len(o['results']) for o in rule.values()),'splits':{}}
for split in ('dev','holdout'):
 ids=[x for x in splits[split] if x in rule];m,_=independent_score(rule,ids,split);rule_info['splits'][split]={'pairs':len(ids),**m}
coverage={s:{'pairs':len(splits[s]),'expected_items':sum(len(records[x]['rakuten']['skus']) for x in splits[s]),'reference_items':len(refs[s]),'expected_without_reference':sum(f"{x}|{row['sku_key']}" not in refs[s] for x in splits[s] for row in records[x]['rakuten']['skus'])} for s in refs}
# Sensitivity reproduces only the exact ten-row acceptable-set change described in report v3.
# It is not a replacement for the missing v3 reference artifact.
sens_pools={}
for prefix in ('E001__','E003__'):
 acc=collections.defaultdict(list)
 for k,run in all_runs.items():
  if not k.startswith(prefix):continue
  for i in run['items']:
   v=i['score']
   if i['unit']=='raw-2fc336c20bf4ad86' and link(i['pred']) and link(i['pred']) in {link(z) for z in i['ref']}:
    v=1
   acc[i['id']].append(v)
 sens_pools[prefix]={k:sum(v)/len(v) for k,v in acc.items()}
sens_diffs={k:sens_pools['E003__'][k]-v for k,v in sens_pools['E001__'].items()}
sensitivity={'note':'Sensitivity only: simulate report-described v3 accepting matched/conditional on embroidery stole 10 rows; v3 reference itself is absent.',
             'baseline_primary':sum(sens_pools['E001__'].values())/len(sens_diffs),
             'c002_primary':sum(sens_pools['E003__'].values())/len(sens_diffs),
             'product_pair':boot(sens_diffs,pair)}
data_totals={'pairs':len(records),'rakuten_slots':sum(len(x['rakuten']['skus']) for x in records.values()),
             'unique_rakuten_skus':len({s['sku_key'] for r in records.values() for s in r['rakuten']['skus']}),
             'au_rows':sum(len(r['au']['skus']) for r in records.values()),
             'cross_candidates':sum(len(r['rakuten']['skus'])*len(r['au']['skus']) for r in records.values()),
             'unique_pages':len({r['rakuten']['url'] for r in records.values()})}
result={'source':str(WS),'raw_snapshot_note':'Task 1 snapshot ends after E006 / Sonnet probe; E007–E013, candidate holdout/full results, and eval v3 are absent. Do not compare v2 raw scores as a contradiction to report explicitly stating v3 rescoring.','coverage':coverage,'data_totals':data_totals,'v3_reference_sensitivity_only':sensitivity,'recomputed_runs':all_runs,'comparison_c002_minus_c000_two_run_averages':comparison,'baseline_repeat':{'product_pair':boot(repeatdiff,pair),'independent_item':boot(repeatdiff,lambda k:k),'changed_groups':repeatgroups},'deterministic_final_rule':rule_info}
RESULT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'coverage':coverage,'run_summary':{k:{**v['metrics'],'calls':v['calls'],'cost_uncached':v['uncached_usd_from_usage'],'stored_score_differences':len(v['stored_score_differences'])} for k,v in all_runs.items() if k.startswith('E') or k.startswith('p001')},'comparison':comparison,'repeat':result['baseline_repeat'],'rule':rule_info,'saved':str(RESULT)},ensure_ascii=False,indent=2))
