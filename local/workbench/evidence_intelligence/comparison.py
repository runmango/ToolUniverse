"""Detect differences, not which study is correct. Never pool effects automatically."""
from itertools import combinations
from ..medical_core.model import normalize

def compare(extractions):
    rows=[]
    for left,right in combinations(extractions,2):
        a,b=left['payload'],right['payload']
        for fa in a['findings']:
            for fb in b['findings']:
                if not (set(fa['endpoint_keys'])&set(fb['endpoint_keys'])) and normalize(fa['outcome'])!=normalize(fb['outcome']): continue
                differences={key:{'left':a['fields'][key]['value'],'right':b['fields'][key]['value'],
                                  'left_context':(a['fields'][key]['span'] or {}).get('quote'),
                                  'right_context':(b['fields'][key]['span'] or {}).get('quote')} for key in ('population','intervention','comparison','sample_size','follow_up','primary_outcomes')}
                context_complete=all(v['left'] and v['right'] for key,v in differences.items() if key in ('population','intervention','comparison','follow_up'))
                context_same=context_complete and all(normalize(v['left'])==normalize(v['right']) for key,v in differences.items() if key in ('population','intervention','comparison','follow_up'))
                da,db=fa['direction'],fb['direction']
                mixed=len({da,db})>1 and 'unclear' not in (da,db)
                pairs=[]
                for ra in fa['numbers']['ratios']:
                    for rb in fb['numbers']['ratios']:
                        if ra['metric']==rb['metric']:
                            pairs.append({'metric':ra['metric'],'left':ra,'right':rb,'pooled':False})
                shared_trial=set(a.get('trial_ids',[]))&set(b.get('trial_ids',[]))
                rows.append({'left_extraction_id':left['id'],'right_extraction_id':right['id'],
                             'left_finding_id':fa['id'],'right_finding_id':fb['id'],
                             'outcome':fa['outcome'],'status':'potential_conflict' if mixed and context_same else 'direction_difference' if mixed else 'comparison',
                             'context_status':'same_recorded_context' if context_same else 'different_or_incomplete_context',
                             'differences':differences,'designs':[a['design'],b['design']],
                             'directions':[da,db],'effects':pairs,'shared_trial_ids':sorted(shared_trial),
                             'independence':'possibly_overlapping_participants' if shared_trial else 'not_established',
                             'note':'显著与不显著并不证明两研究效应显著不同。需核验人群、干预、对照、结局定义、时间点及研究重叠；不判定论文错误，不自动合并效应量。'})
    return rows
