import ast,json,importlib.util,random,hashlib,re,unicodedata,collections,sys
sys.dont_write_bytecode=True
from pathlib import Path
ROOT=Path('/workspace/scratch/ai-task-autotuner-testdata-20261019')
PACKET=Path('/workspace/scratch/ai-task-autotuner-review-20261019/task_structures')
RAW=ROOT/'inputs/sku-normal-v1/inputs/products.jsonl'
W1=ROOT/'iteration-1/eval-1-sku-matching-no-labels/with_skill/outputs/ws'
W2=ROOT/'iteration-1/eval-2-au-to-rakuten-product-identification/with_skill/outputs/ws'
def imp(name,path):
 s=importlib.util.spec_from_file_location(name,path); m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
p1=imp('p1',PACKET/'01_sku-matching/pipeline/pipeline.py');p2=imp('p2',PACKET/'02_au-rakuten-product-identification/pipeline/pipeline.py')
recs=[json.loads(l) for l in RAW.read_text().splitlines() if l.strip()]
refs={sp:{x['key']:x for l in (W1/f'eval/reference/{sp}.jsonl').read_text().splitlines() if (x:=json.loads(l))} for sp in ('dev','holdout')}
ref={k:v for d in refs.values() for k,v in d.items()}
split1=json.loads((W1/'eval/splits.json').read_text()); split2=json.loads((W2/'eval/splits.json').read_text())
summary={'task1':{},'task2':{}}; cases=[]
for rec in recs:
 out=p1.exact_pair(rec)
 if out is None:continue
 pid=rec['product_pair_id']; rlookup={s['sku_key']:s for s in rec['rakuten']['skus']}; alookup={s['sku_key']:s for s in rec['au']['skus']}
 case={'pair':pid,'split':next((sp for sp in ('dev','holdout','rest') if pid in split1[sp]),'none'),'rows':len(out['results']),'reference_rows':0,'mismatches':[],'titles':{s:rec[s]['title'] for s in ('rakuten','au')},'axes':{s:rec[s]['sku_axes'] for s in ('rakuten','au')},'options':rec['au']['non_sku_options'],'permuted_normalized_values':[]}
 for row in out['results']:
  k=f"{pid}|{row['rakuten_sku_key']}"; r=ref.get(k)
  if r:
   case['reference_rows']+=1
   label=f"matched:{row['au_sku_key']}"
   if r['label']!=label:case['mismatches'].append({'key':k,'reference':r['label'],'actual':label})
  arow=alookup[row['au_sku_key']];rrow=rlookup[row['rakuten_sku_key']]
  rv=[p1._norm(c['value']) for c in rrow['conditions']];av=[p1._norm(c['value']) for c in arow['conditions']]
  if rv!=av:case['permuted_normalized_values'].append({'rakuten':rrow['conditions'],'au':arow['conditions']})
 cases.append(case)
summary['task1']={'pairs':len(cases),'rows':sum(c['rows'] for c in cases),'reference_rows':sum(c['reference_rows'] for c in cases),'mismatches':sum(len(c['mismatches']) for c in cases),'permuted_pairs':sum(bool(c['permuted_normalized_values']) for c in cases),'by_split':{sp:{'pairs':sum(c['split']==sp for c in cases),'rows':sum(c['rows'] for c in cases if c['split']==sp),'reference_rows':sum(c['reference_rows'] for c in cases if c['split']==sp)} for sp in ('dev','holdout','rest')},'all_reference_rows':{sp:len(r) for sp,r in refs.items()}}
# Run only dataset pure builder with injected local paths; leave packaged charter intact.
dsrc=ast.parse((PACKET/'02_au-rakuten-product-identification/eval_kit/dataset.py').read_text())
denv=dict(random=random,json=json,hashlib=hashlib,re=re,unicodedata=unicodedata,CHARTER={'data':{'inputs':[str(RAW)]}},_CACHE={},K=8,VARIANTS=('present','removed'),EXCLUDED_PAIRS={'raw-7f04455a83ac52c8','raw-7fa1c1f5f6ac2ec6','raw-affeea04e64fe893'},DOUBTFUL_PAIRS={'raw-12f3057cc81c6d73'})
for node in dsrc.body:
 if isinstance(node,ast.FunctionDef):exec(compile(ast.Module([node],type_ignores=[]),'<dataset pure funcs>','exec'),denv)
data=denv['_build'](); sc=[]; top4=[]
for uid,u in data['units'].items():
 ans=data['answers'][uid];meta=data['meta'][uid]
 chosen=p2.shortcut(u)
 if chosen:
  au=u['input']['au'];c=next(c for c in u['input']['candidates'] if c['cid']==chosen);a=p2._bg(p2._rep(au));b=p2._bg(p2._rep(c))
  sc.append({'id':uid,'split':next((sp for sp in ('dev','holdout','rest') if uid in split2[sp]),'none'),'actual':chosen,'answer':ans,'matches':chosen==ans,'excluded':meta['excluded'],'au':au,'chosen':c,'similarity':len(a&b)/max(1,len(a|b))})
 if ans!='none' and ans not in {c['cid'] for c in p2.prune(u)}:top4.append(uid)
summary['task2']={'total_queries':len(data['units']),'decided':len(sc),'mismatches':[c['id'] for c in sc if not c['matches']],'excluded_decided':[c['id'] for c in sc if c['excluded']],'by_split':{sp:{'queries':sum(c['split']==sp for c in sc)} for sp in ('dev','holdout','rest')},'missed_by_top4':top4}
personalized={
 'raw-7a959106e36027f2':'1行目の刻印内容をご入力下さい。（刻印可能文字数：英数字なら13文字、日本語・記号は5文字）',
 'raw-85500c8c79045555':'フォントを下記からお選び下さい',
 'raw-2fc336c20bf4ad86':'■イニシャル刺繍',
 'raw-bb3db28ec31a79b6':'■イニシャル選択',
 'raw-4e401d32a52ad330':'■イニシャル選択'}
examples=[]
for line,rec in enumerate(recs,1):
 if rec['product_pair_id'] not in personalized:continue
 pid=rec['product_pair_id'];case=next(c for c in cases if c['pair']==pid);out=p1.exact_pair(rec)
 assert personalized[pid] in rec['au']['non_sku_options']['observed_unselected_text']
 assert all(not x['required_extra_options'] and not x['unresolved_conditions'] for x in out['results'])
 examples.append({'pair':pid,'products_jsonl_line':line,'split':case['split'],'rows':case['rows'],'reference_rows':case['reference_rows'],'titles':case['titles'],'observed_option_quote':personalized[pid],'all_observed_options':rec['au']['non_sku_options']['observed_unselected_text'],'current_selected_values':None,'output_extra_options':[],'output_unresolved_conditions':[]})
observations={'explicit_personalization_examples':examples,'explicit_personalization_example_pairs':len(examples),'explicit_personalization_example_rows':sum(e['rows'] for e in examples),'meaning':'All explicit examples are accepted by exact_pair with no additional/unresolved conditions, despite customization prompts and null selection state. This demonstrates discarded purchase-condition information, not a human-verified wrong SKU row. This does not assert all free options are required.'}
Path('/workspace/scratch/raw-review-shortcuts-evidence.json').write_text(json.dumps({'summary':summary,'task1_cases':cases,'task2_cases':sc,'observations':observations},ensure_ascii=False,indent=2))
print(json.dumps(summary,ensure_ascii=False,indent=2))
