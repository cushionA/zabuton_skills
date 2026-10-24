import contextlib, io, json, shutil, sys, tempfile, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock
SKILL=Path('/workspace/scratch/ai-task-autotuner-review-20261019/skill/ai-task-autotuner')
sys.path.insert(0,str(SKILL/'tests')); sys.path.insert(0,str(SKILL/'scripts'))
import test_flow as tf
import common, ledger, llm_call, quality_gate, run_experiment, workspace
OUT={}

def quiet(fn,*args,**kw):
    s=io.StringIO()
    with contextlib.redirect_stdout(s): r=fn(*args,**kw)
    return r,s.getvalue()

def setup():
    t=tf.FlowTest();t.setUp();t.run_py('quality_gate.py',str(t.ws));return t

# Missing reference entries and an entirely absent holdout reference still validate.
t=setup()
try:
    ref=t.ws/'eval/reference/dev.jsonl'
    rows=common.read_jsonl(ref);ref.write_text(''.join(json.dumps(r)+'\n' for r in rows[:9]))
    (t.ws/'eval/reference/holdout.jsonl').unlink()
    a,_=quiet(run_experiment.run,t.ws,'c001-rule','dev','baseline',tag='a')
    b,_=quiet(run_experiment.run,t.ws,'c001-rule','dev','baseline',tag='b')
    v=t.run_py('validate_eval.py',str(t.ws),'--runs',str(a),str(b),'--spotcheck','all available failing items checked carefully')
    OUT['missing_reference']={'passed':'EVAL VALIDATION PASSED' in v.stdout,'reference':[c for c in common.read_json(t.ws/'eval/validation.json')['checks'] if c['check']=='reference'],'holdout_reference_exists':False}
    _,sc=workspace.load_eval(t.ws);ds,_=workspace.load_eval(t.ws);u=ds.load_units('dev')[:1]
    try: sc.score(u,{u[0]['id']:{'items':None}},'dev')
    except Exception as e: OUT['items_null']={'exception':str(type(e).__name__)+': '+str(e)}
    oracle=sc.oracle(u,'dev'); oracle[u[0]['id']]['items'].insert(0,{'key':'good-0','label':'wrong'})
    s=sc.score(u,oracle,'dev');OUT['duplicate_keys']={'primary':s['primary'],'gate_failures':s['gate_failures']}
finally:t.tearDown()

# Waivers and data identity checks.
t=setup()
try:
    common.append_jsonl(t.ws/'audit/close_read.jsonl',{'id':'u01','verdict':'defect','evidence':'issue A'})
    t.run_py('quality_gate.py',str(t.ws),ok=(2,));t.run_py('quality_gate.py',str(t.ws),'--accept-review','user accepted A')
    common.append_jsonl(t.ws/'audit/close_read.jsonl',{'id':'u02','verdict':'defect','evidence':'new issue B'})
    r=t.run_py('quality_gate.py',str(t.ws),ok=(2,));g=common.read_json(t.ws/'audit/quality_gate.json')
    OUT['waiver_reuse']={'allows':quality_gate.gate_allows_runs(t.ws),'affected':[f['affected'] for f in g['findings'] if f['id']=='close-read-defects'],'waiver':g['waiver']}
    ch=common.read_json(t.ws/'charter.json');source=Path(ch['data']['inputs'][0]);replacement=t.tmp/'replacement.jsonl';replacement.write_text('{}\n');ch['data']['inputs']=[str(replacement)];common.write_json(t.ws/'charter.json',ch)
    OUT['replaced_input']={'changed':quality_gate.verify_hashes(t.ws),'allows':quality_gate.gate_allows_runs(t.ws)}
    d=t.tmp/'inputs';d.mkdir();shutil.copy(source,d/'a.jsonl');ch['data']['inputs']=[str(d)];common.write_json(t.ws/'charter.json',ch);(t.ws/'audit/checks.py').write_text('def run_checks(ctx): return []\n')
    common.write_json(t.ws/'audit/quality_gate.json',quality_gate.evaluate(t.ws));(d/'b.jsonl').write_text('{}\n')
    OUT['added_input']={'changed':quality_gate.verify_hashes(t.ws)}
finally:t.tearDown()

# Unit metadata drops during pooling.
a=[{'id':f'{u}:{i}','unit':u,'score':0.0} for u in ('A','B') for i in range(10)]
b=[dict(x,score=1.0 if x['unit']=='A' else 0.0) for x in a]
def pool(x):return ledger._pool([{'run_id':'r','items':x,'summary':{'primary':0.5,'cost_usd_per_unit':1}}],'cost_usd_per_unit')['items']
OUT['pooling']={'before':{k:v for k,v in common.paired_items(a,b).items() if k in ('units','ci','band')},'after':{k:v for k,v in common.paired_items(pool(a),pool(b)).items() if k in ('units','ci','band')}}

# Zero-cost candidate decisions. Use synthetic metadata only, no model.
t=setup()
try:
    ch=common.read_json(t.ws/'charter.json');ch['phase']='efficiency';ch['quality_floor']=0.5;ch['efficiency_metric']='cost_usd_per_unit';common.write_json(t.ws/'charter.json',ch)
    for exp,candidate,cost in [('E001','c001-rule',1.0),('E002','c002-llm',0.0)]:
        common.append_jsonl(t.ws/'ledger.jsonl',{'event':'propose','exp':exp,'candidate':candidate,'lever':'baseline' if exp=='E001' else 'deterministic.pre'})
        d=t.ws/'runs'/exp;d.mkdir();common.write_json(d/'meta.json',{'run_id':exp,'exp':exp,'candidate':candidate,'split':'dev','purpose':'baseline','eval_hash':workspace.eval_hash(t.ws)})
        common.write_json(d/'summary.json',{'primary':1.0,'cost_usd_per_unit':cost,'harness_error_rate':0,'gate_failures':0,'ai_free':cost==0})
        common.write_json(d/'score.json',{'items':[{'id':'i','unit':'u','score':1.0}]})
        quiet(ledger.decide,t.ws,exp)
    OUT['zero_cost_decision']=common.read_jsonl(t.ws/'ledger.jsonl')[-1]
finally:t.tearDown()

# Unknown model pricing returns fully known zero cost.
rec={'ok':True,'usage':{'input':100,'output':10},'cost_usd':None,'model_served':'unknown-model','stop_reason':'end_turn','text':'{}'}
with mock.patch.object(llm_call,'_cli_attempt',return_value=rec): r=llm_call.call('x',retries=0)
s=run_experiment._summarize([{'id':'u'}],[{'id':'u','status':'ok'}],[r],{'primary':1},0.1,1)
OUT['unknown_cost']={'call_cost_usd':r['cost_usd'],'summary_cost_usd':s['cost_usd'],'cost_complete':s['cost_complete']}

# Holdout cap checks purpose instead of split.
t=setup()
try:
    ch=common.read_json(t.ws/'charter.json');ch['loop']['max_holdout_evals']=0;common.write_json(t.ws/'charter.json',ch)
    with mock.patch.object(llm_call,'call',side_effect=tf.fake_call) as m:
        d,_=quiet(run_experiment.run,t.ws,'c002-llm','holdout','baseline')
    OUT['holdout_purpose_bypass']={'call_count':m.call_count,'meta_split':common.read_json(d/'meta.json')['split'],'meta_purpose':common.read_json(d/'meta.json')['purpose'],'cap':0}
finally:t.tearDown()

# A saturated semaphore allows queued calls through after quota is detected.
with tempfile.TemporaryDirectory() as td:
    log=run_experiment.RunLog(Path(td));sem=threading.BoundedSemaphore(1);start=threading.Barrier(6)
    def quota(*a,**kw):time.sleep(.03);return tf.quota_call(*a,**kw)
    def worker(i):
        ctx=run_experiment.Ctx(Path(td),{},log,sem,str(i));start.wait();return ctx.llm('x')
    with mock.patch.object(llm_call,'call',side_effect=quota) as m:
        with ThreadPoolExecutor(max_workers=6) as ex:list(ex.map(worker,range(6)))
    OUT['quota_semaphore']={'call_count':m.call_count,'max_parallel_calls':1,'quota_hit':log.quota_hit.is_set()}

# Setup is outside the measured wall time. Export misses the required quality gate.
t=setup()
try:
    p=t.ws/'candidates/c001-rule/pipeline.py';p.write_text('import time\ndef setup(ctx): time.sleep(.3)\n'+tf.RULE_PIPELINE)
    ts=time.perf_counter();d,_=quiet(run_experiment.run,t.ws,'c001-rule','dev','baseline');elapsed=time.perf_counter()-ts
    OUT['setup_timing']={'elapsed_s':round(elapsed,4),'run_wall_s':common.read_json(d/'summary.json')['run_wall_s']}
    t.run_py('export_deliverables.py',str(t.ws),'--candidate','c001-rule');out=t.ws/'deliverables'
    OUT['export_gate']={'exists':(out/'audit/quality_gate.json').exists(),'allows':quality_gate.gate_allows_runs(out)}
finally:t.tearDown()
print(json.dumps(OUT,indent=2,ensure_ascii=False))
Path('/workspace/scratch/snapshot-harness-review-evidence.json').write_text(json.dumps(OUT,indent=2,ensure_ascii=False)+'\n')
