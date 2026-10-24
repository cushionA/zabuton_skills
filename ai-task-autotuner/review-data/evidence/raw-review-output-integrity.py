import json,pathlib,collections,re
root=pathlib.Path('/workspace/scratch/ai-task-autotuner-testdata-20261019')
products={r['product_pair_id']:r for r in map(json.loads,(root/'inputs/sku-normal-v1/inputs/products.jsonl').open())}
evidence={'raw_calls':{},'outputs':{},'examples':[]}
def referr(rec,ref):
    if not isinstance(ref,str): return 'not_string'
    for side,pre in [('rakuten','rd'),('au','ad')]:
        if ref.startswith(pre) and ref[2:].isdigit():
            return None if 1<=int(ref[2:])<=len(rec[side]['description']['lines']) else 'short_desc_oob'
        if ref in {x['evidence_id'] for x in rec[side]['description']['lines']}:return None
    if re.fullmatch('[RA][0-9]+(?::.*)?',ref):
        rid,_,name=ref.partition(':');side='rakuten' if rid[0]=='R' else 'au'
        i=int(rid[1:])
        if not 0<=i<len(rec[side]['skus']):return 'short_row_oob'
        if name and name not in {x['name'] for x in rec[side]['skus'][i]['conditions']}|{x['name'] for x in rec[side]['skus'][i].get('auxiliary_attributes',[])}:return 'short_axis_unknown'
        return None
    for side in ['rakuten','au']:
        for row in rec[side]['skus']:
            prefix=side+'/'+row['sku_key']
            if ref==prefix:return None
            if ref.startswith(prefix+'/'):
                name=ref[len(prefix)+1:]
                return None if name in {x['name'] for x in row['conditions']}|{x['name'] for x in row.get('auxiliary_attributes',[])} else 'expanded_axis_unknown'
    return 'unknown_ref'
for ws in sorted(root.glob('iteration-1/eval-*/with_skill/outputs/ws')):
    task=ws.parts[-4];print('TASK',task)
    for rd in sorted((ws/'runs').iterdir()):
        if not (rd/'meta.json').exists():continue
        meta=json.load((rd/'meta.json').open());candidate=meta['candidate']
        if candidate.startswith('ref'):continue
        label=task+'/'+candidate+'/'+meta['purpose']+'/'+str(meta.get('split'))
        stats=evidence['raw_calls'].setdefault(label,collections.Counter())
        calls=rd/'calls.jsonl'
        if calls.exists():
            for lineno,line in enumerate(calls.open(),1):
                c=json.loads(line);stats['calls']+=1
                js=c.get('json'); uid=c.get('unit_id')
                if not isinstance(js,dict):
                    stats['json_nondict']+=1;continue
                if 'choice' in js:
                    stats['choice_seen']+=1
                    if not isinstance(js['choice'],str):stats['choice_notstring']+=1
                rec=products.get(uid)
                if not rec:continue
                rr=js.get('results')
                if not isinstance(rr,list):stats['bad_results']+=1;continue
                seen=set()
                for it in rr:
                    if not isinstance(it,dict):stats['bad_result']+=1;continue
                    stats['rows']+=1
                    r=it.get('r')
                    if r in seen:stats['dup_r']+=1
                    seen.add(r)
                    for k in ['x','u','e']:
                        if it.get(k) is not None and not isinstance(it.get(k),list):stats['type_bad_'+k]+=1
                    refs=list(it.get('e') or [])
                    for x in it.get('x') or []:
                        if not isinstance(x,dict):stats['x_nondict']+=1;continue
                        if x.get('r_ref'):refs+=[x['r_ref']]
                        refs+=x.get('e') or []
                    for ref in refs:
                        err=referr(rec,ref)
                        if err:
                            stats[err]+=1
                            evidence['examples'].append({'kind':'call_ref','error':err,'path':str(calls),'line':lineno,'unit':uid,'r':r,'ref':ref,'candidate':candidate})
        out=rd/'outputs.jsonl'
        if not out.exists():continue
        stats=evidence['outputs'].setdefault(label,collections.Counter())
        for lineno,line in enumerate(out.open(),1):
            d=json.loads(line);stats['units']+=1;rec=products.get(d['id']);output=d.get('output')
            if d['status']!='ok':stats['notok']+=1
            if not rec or not isinstance(output,dict):continue
            for it in output.get('results') or []:
                stats['rows']+=1
                dec=it['decision'];stats[dec]+=1
                if dec=='conditional':
                    for key in ['required_extra_options','evidence_refs','reason']:
                        if not it.get(key):stats['cond_missing_'+key]+=1
                refs=list(it.get('evidence_refs') or [])
                for x in it.get('required_extra_options') or []:
                    stats['extra_options']+=1
                    refs+=x.get('evidence_refs') or []
                    if not x.get('feature'):stats['option_feature_empty']+=1
                    if x.get('required_state') not in ['include','exclude']:
                        stats['option_bad_state']+=1
                        evidence['examples'].append({'kind':'option_bad_state','path':str(out),'line':lineno,'unit':d['id'],'r':it['rakuten_sku_key'],'decision':dec,'option':x})
                for ref in refs:
                    err=referr(rec,ref)
                    if err:
                        stats[err]+=1
                        evidence['examples'].append({'kind':'output_ref','error':err,'path':str(out),'line':lineno,'unit':d['id'],'r':it['rakuten_sku_key'],'decision':dec,'ref':ref,'candidate':candidate})
print(json.dumps({'raw_calls':evidence['raw_calls'],'outputs':evidence['outputs']},ensure_ascii=False,indent=2))
print('EXAMPLES',len(evidence['examples']))
print(json.dumps(evidence['examples'][:30],ensure_ascii=False,indent=2))
out=pathlib.Path('/workspace/scratch/raw-review-output-integrity.json');out.write_text(json.dumps(evidence,ensure_ascii=False,indent=2))
