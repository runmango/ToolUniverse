"""Frozen graph retrieval constrained to the same source documents as baselines."""
from ..medical_core.service import match_entities
from ..evidence_chain.service import retrieve_graph

class GraphRetriever:
    def __init__(self,release,corpus,config):
        from .retrieval import Unavailable
        if not config.get('enabled',True): raise Unavailable('图谱检索配置尚未启用')
        if type(config.get('max_hops',3)) is not int or not 1<=config.get('max_hops',3)<=6: raise ValueError('max_hops 必须为1–6')
        self.release=release;self.config=config
        allowed={d['document_id'] for d in corpus};texts={d['document_id']:d['text'] for d in corpus};payload=release['payload']
        self.lookup={}
        for node in payload['nodes']:
            for item in node.get('identifiers',[]):
                key=('PMID:'+item['value']) if item['scheme']=='PMID' else item['value'] if item['scheme']=='NCT' else 'DOI:'+item['value'] if item['scheme']=='DOI' else ''
                if key in allowed: self.lookup[node['id']]=key
        sources={}
        for key,value in payload['sources'].items():
            doc_id='PMID:'+value['source_id'] if value['provider']=='PubMed' else value['source_id'] if value['provider']=='ClinicalTrials.gov' else None
            raw=value['payload']
            if value['provider']=='PubMed' and doc_id in allowed and (raw.get('title','')+'\n'+(raw.get('abstract') or ''))==texts[doc_id]: sources[key]=value
            # Trial records are allowed only when their serialized public text exactly matches.
            if value['provider']=='ClinicalTrials.gov' and doc_id in allowed and (raw.get('brief_title','')+'\n'+(raw.get('brief_summary') or ''))==texts[doc_id]: sources[key]=value
        edges=[e for e in payload['edges'] if e['evidence_source'] in sources]
        nodes={e[k] for e in edges for k in ('source_entity','target_entity')}|set(self.lookup)
        self.payload={**payload,'sources':sources,'edges':edges,'nodes':[n for n in payload['nodes'] if n['id'] in nodes]}
        self.runtime={'graph_release_id':release['id'],'graph_release_hash':release['content_hash'],
                      'scope':'fixed_corpus_only','allowed_source_count':len(sources),'embedding_dimension':None}
    def rank(self,query,top_k,replay_expansion=None):
        result=retrieve_graph(self.payload,query,int(self.config.get('max_hops',3)),self.config.get('include_inferred',False))
        paths={p['target']:p['relation_ids'] for p in result['paths']}
        ranking=[{'document_id':self.lookup[k],'score':1/(1+len(path))} for k,path in paths.items() if k in self.lookup]
        return sorted(ranking,key=lambda x:(-x['score'],x['document_id']))[:top_k],{'graph':result,**self.runtime}
    def close(self): pass
