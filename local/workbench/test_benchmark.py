"""Offline fixtures are isolated in temporary databases and never become real Gold."""
import asyncio
from contextlib import asynccontextmanager
import copy
import json
import math
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from workbench.app import app
from workbench.benchmark.store import BenchmarkStore
from workbench.benchmark.metrics import evaluate_query,aggregate,evaluate_entities,evaluate_assessment,gold_labels
from workbench.benchmark.retrieval import BM25,normalize_vectors,public_corpus,Retriever,Unavailable,Dense,Reranker,Elasticsearch,local_fingerprint
from workbench.benchmark.experiments import ExperimentRunner,mine_negatives,review_negative,save_assessment,report
from workbench.benchmark.spec import query_record

HUMAN={'annotation_source':'human','verification_status':'verified','annotator':'TEST reviewer'}

def query():
    return {'query_id':'TEST_001','query':'MDS therapy trial','domain':'MDS','query_type':'clinical_research','entities':[],
            'relevant_documents':[{'document_id':'A','relevance':3,**HUMAN},{'document_id':'B','relevance':2,**HUMAN},{'document_id':'C','relevance':0,**HUMAN}],
            'review_complete':True,**HUMAN}

DOCS=[{'document_id':'A','title':'MDS therapy trial','text':'TEST evidence statement.'},
      {'document_id':'B','title':'MDS transplant','text':'TEST donor lymphocyte infusion.'},
      {'document_id':'C','title':'Other population','text':'TEST unrelated topic.'}]


class MetricTests(unittest.TestCase):
    def test_hand_calculated_ranking_metrics_and_graded_gain(self):
        m=evaluate_query(query(),['C','B','A'])
        self.assertAlmostEqual(m['MRR'],.5)
        self.assertAlmostEqual(m['MAP'],(.5+2/3)/2)
        self.assertAlmostEqual(m['at_k']['5']['Precision'],.4)
        self.assertEqual(m['at_k']['10']['Recall'],1)
        self.assertEqual(m['at_k']['20']['HitRate'],1)
        self.assertAlmostEqual(m['at_k']['5']['NDCG'],(3/math.log2(3)+7/2)/(7+3/math.log2(3)))

    def test_missing_results_are_zero_and_missing_gold_is_null(self):
        self.assertEqual(evaluate_query(query(),[])['MAP'],0)
        q=query();q['relevant_documents']=[];q['review_complete']=False
        self.assertIsNone(evaluate_query(q,['A'])['MRR'])
        self.assertIsNone(aggregate([evaluate_query(q,[])],20)['MAP'])

    def test_reviewed_no_relevant_counts_zero_but_partial_negative_does_not(self):
        q=query()
        for label in q['relevant_documents']: label['relevance']=0
        self.assertEqual(evaluate_query(q,['A'])['MRR'],0)
        q['review_complete']=False
        self.assertIsNone(evaluate_query(q,['A'])['MRR'])

    def test_ai_labels_never_enter_gold(self):
        q=query();q['annotation_source']='ai';q['review_complete']=False
        for label in q['relevant_documents']: label['annotation_source']='ai'
        self.assertIsNone(evaluate_query(q,['A'])['MAP'])
        imported=query_record(q,attest=True,actor='TEST human')
        self.assertEqual(gold_labels(imported),{})

    def test_provenance_case_cannot_promote_ai_and_invalid_types_rejected(self):
        q=query()
        for label in q['relevant_documents']: label['annotation_source']=' AI '
        self.assertEqual(gold_labels(query_record(q,attest=True,actor='TEST human')),{})
        q['annotation_source']=[]
        with self.assertRaises(ValueError): query_record(q,attest=True,actor='TEST human')

    def test_precision_denominator_is_k_duplicates_do_not_add_hits(self):
        m=evaluate_query(query(),['A','A'])
        self.assertEqual(m['returned_count'],1)
        self.assertEqual(m['at_k']['5']['Precision'],.2)
        self.assertEqual(m['at_k']['5']['Recall'],.5)

    def test_entity_metrics_require_separate_human_annotations(self):
        q=query();q['medical_gold']={'entities':[{'name':'MDS','type':'Disease'}],**HUMAN}
        d={**DOCS[0],'entities':[{'name':'MDS','type':'Disease'}],'entity_annotation':HUMAN}
        self.assertEqual(evaluate_entities(q,[d])['at_k']['5']['DiseaseEntityRecall'],1)
        d['entity_annotation']={}
        self.assertIsNone(evaluate_entities(q,[d])['at_k']['5']['DiseaseEntityRecall'])
        self.assertEqual(evaluate_entities(q,[])['at_k']['5']['DiseaseEntityRecall'],0)

    def test_generation_denominators_are_separate_with_coverage(self):
        values=evaluate_assessment({'trial_matches':[{'correct':True},{'correct':False}],
                                   'citations':[{'exists':True},{'exists':None}], 'claims':[{'supported':False}]})
        self.assertEqual(values['ClinicalTrialMatchingAccuracy']['value'],.5)
        self.assertEqual(values['CitationAccuracy']['value'],1)
        self.assertEqual(values['CitationAccuracy']['coverage'],.5)
        self.assertEqual(values['EvidenceSupportRate']['value'],0)

    def test_weak_relevance_has_graded_gain_but_not_binary_recall(self):
        q=query()
        for label in q['relevant_documents']: label['relevance']=1 if label['document_id']=='A' else 0
        values=evaluate_query(q,['A'])
        self.assertEqual(values['at_k']['5']['NDCG'],1)
        self.assertEqual(values['at_k']['5']['Recall'],0)

    def test_disease_synonyms_share_entity_recall_key(self):
        q=query();q['medical_gold']={'entities':[{'name':'MDS','type':'Disease'}],**HUMAN}
        doc={**DOCS[0],'entities':[{'name':'Myelodysplastic Syndromes','type':'Disease'}],'entity_annotation':HUMAN}
        self.assertEqual(evaluate_entities(q,[doc])['at_k']['5']['DiseaseEntityRecall'],1)


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.store=BenchmarkStore(Path(self.temp.name)/'bench.sqlite')
        self.dataset=self.store.create_dataset({'name':'TEST fixture','domain':'MDS'})
        identity=self.dataset['id']
        self.store.import_jsonl(identity,'\n'.join(json.dumps(d) for d in DOCS),'documents')
        self.dataset=self.store.import_jsonl(identity,json.dumps(query()),'queries',attest=True,actor='TEST reviewer')

    def tearDown(self): self.temp.cleanup()

    def test_initial_domains_have_no_gold_and_no_corpus(self):
        defaults=[d for d in self.store.datasets() if d['id']!=self.dataset['id']]
        self.assertEqual(len(defaults),5)
        self.assertTrue(all(d['annotated_query_count']==0 and d['document_count']==0 for d in defaults))

    def test_versions_are_immutable_and_corpus_hash_ignores_labels(self):
        old=self.dataset['snapshot'];q=copy.deepcopy(old['payload']['queries'][0]);q['relevant_documents'][0]['relevance']=2
        new=self.store.save_query(self.dataset['id'],q,'TEST editor')['snapshot']
        self.assertNotEqual(old['content_hash'],new['content_hash'])
        self.assertEqual(old['corpus_hash'],new['corpus_hash'])
        self.assertEqual(self.store.snapshot(old['id'])['payload']['queries'][0]['relevant_documents'][0]['relevance'],3)

    def test_import_is_atomic_and_rejects_duplicate_ids(self):
        original=self.store.dataset(self.dataset['id'])['current_version']
        with self.assertRaises(ValueError): self.store.import_jsonl(self.dataset['id'],json.dumps(DOCS[0])+'\n{broken','documents')
        with self.assertRaises(ValueError): self.store.import_jsonl(self.dataset['id'],json.dumps(DOCS[0])+'\n'+json.dumps(DOCS[0]),'documents')
        self.assertEqual(original,self.store.dataset(self.dataset['id'])['current_version'])

    def test_verified_import_without_attestation_stays_candidate(self):
        other=self.store.create_dataset({'name':'TEST import','domain':'MDS'})
        data=self.store.import_jsonl(other['id'],json.dumps(query()),'queries')
        self.assertEqual(gold_labels(data['snapshot']['payload']['queries'][0]),{})

    def test_changed_document_invalidates_its_judgment(self):
        modified={**DOCS[0],'text':'TEST changed evidence'}
        data=self.store.save_document(self.dataset['id'],modified,'TEST editor')
        q=data['snapshot']['payload']['queries'][0]
        self.assertNotIn('A',gold_labels(q));self.assertFalse(q['review_complete'])

    def test_changed_question_cannot_reuse_medical_entity_gold(self):
        q=query();q['medical_gold']={'entities':[{'name':'MDS','type':'Disease'}],**HUMAN}
        self.store.save_query(self.dataset['id'],q,'TEST reviewer')
        q['query']='Changed TEST question';q['review_complete']=False
        for label in q['relevant_documents']: label['verification_status']='candidate'
        with self.assertRaises(ValueError): self.store.save_query(self.dataset['id'],q,'TEST reviewer')
        q['medical_gold']['verification_status']='candidate'
        saved=self.store.save_query(self.dataset['id'],q,'TEST reviewer')
        self.assertEqual(saved['snapshot']['payload']['queries'][0]['medical_gold']['verification_status'],'candidate')

    def test_complete_review_requires_judgments_for_entire_pool(self):
        q=query();q['relevant_documents']=q['relevant_documents'][:1]
        with self.assertRaises(ValueError): self.store.save_query(self.dataset['id'],q,'TEST reviewer')

    def test_models_do_not_store_literal_keys_or_credential_urls(self):
        base={'name':'TEST','version':'v1','method':'embedding','config':{'api_key':'SECRET TEST'}}
        with self.assertRaises(ValueError): self.store.add_model(base)
        base['config']={'embedding':{'api_url':'https://name:password@example.test'}}
        with self.assertRaises(ValueError): self.store.add_model(base)

    def test_random_and_in_batch_negatives_never_assume_unknown_is_negative(self):
        q=query();q['review_complete']=False;q['relevant_documents']=q['relevant_documents'][:1]
        current=self.store.save_query(self.dataset['id'],q,'TEST reviewer')
        rows=mine_negatives(self.store,current['snapshot']['id'],'TEST_001','random')
        self.assertTrue(rows);self.assertTrue(all(r['status']=='candidate' for r in rows))
        self.assertTrue(all(r['document_id']!='A' for r in rows))
        current=review_negative(self.store,rows[0]['id'],'TEST reviewer',True,'different_population','TEST verified unrelated')
        self.assertEqual(gold_labels(current['snapshot']['payload']['queries'][0])[rows[0]['document_id']],0)
        self.assertEqual(mine_negatives(self.store,current['snapshot']['id'],'TEST_001','in_batch'),[])


class RetrievalTests(unittest.TestCase):
    def test_bm25_uses_terms_and_zero_match_is_empty(self):
        engine=BM25(public_corpus([{**d,'entities':[]} for d in DOCS]))
        self.assertEqual(engine.rank('MDS therapy')[0]['document_id'],'A')
        self.assertEqual(engine.rank('NO_MATCH_XYZ'),[])

    def test_public_corpus_cannot_leak_labels_or_gold_entities(self):
        docs=[{**DOCS[0],'relevance':3,'entities':[{'name':'secret gold'}]}]
        self.assertEqual(set(public_corpus(docs)[0]),{'document_id','text'})

    def test_vectors_validate_shape_dimension_finiteness_and_norm(self):
        self.assertAlmostEqual(float(normalize_vectors([[3,4]],1)[0,0]),.6)
        for bad,count,dim in [([[0,0]],1,None),([[float('nan'),1]],1,None),([[1,2]],2,None),([[1,2]],1,3)]:
            with self.assertRaises(ValueError): normalize_vectors(bad,count,dim)

    def test_unconfigured_model_is_explicitly_unavailable(self):
        with self.assertRaises(Unavailable): Retriever('embedding',{'enabled':False},public_corpus(DOCS),Path('.'))

    def test_hybrid_rrf_weight_and_reranker_reorder_candidates(self):
        class FixtureDense:
            def __init__(self,*args): pass
            def rank(self,query): return [{'document_id':'C','score':.9},{'document_id':'A','score':.8},{'document_id':'B','score':.7}]
        class FixtureReranker:
            def __init__(self,*args): pass
            def score(self,q,texts):
                assert 'TEST unrelated topic.' in texts[0]
                return [.1,.2,.9]
        with patch('workbench.benchmark.retrieval.Dense',FixtureDense),patch('workbench.benchmark.retrieval.Reranker',FixtureReranker):
            hybrid=Retriever('hybrid',{'hybrid':{'dense_weight':1}},public_corpus(DOCS),Path('.'))
            self.assertEqual(hybrid.rank('MDS',20)[0][0]['document_id'],'C')
            reranker=Retriever('embedding_reranker',{'recall_depth':20},public_corpus(DOCS),Path('.'))
            self.assertEqual(reranker.rank('MDS',20)[0][0]['document_id'],'B')

    def test_custom_embedding_restores_response_order_and_rejects_bad_indices(self):
        cfg={'provider':'custom_api','api_url':'http://example.test/embeddings','model_name':'TEST fixture','model_version':'test-v1'}
        response=MagicMock();response.json.return_value={'data':[{'index':1,'embedding':[0,1]},{'index':0,'embedding':[1,0]}]}
        with tempfile.TemporaryDirectory() as directory,patch('workbench.benchmark.retrieval.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=response
            dense=Dense(public_corpus(DOCS[:2]),cfg,Path(directory),{})
            self.assertEqual(dense.matrix.tolist(),[[1,0],[0,1]])
            response.json.return_value={'data':[{'index':0,'embedding':[1,0]},{'index':0,'embedding':[0,1]}]}
            with self.assertRaises(ValueError): dense.encode(['A','B'],False)

    def test_custom_reranker_requires_all_unique_candidate_indices(self):
        cfg={'provider':'custom_api','api_url':'http://example.test/rerank','model_name':'TEST','model_version':'test-v1'}
        response=MagicMock();response.json.return_value={'results':[{'index':1,'relevance_score':.8},{'index':0,'relevance_score':.2}]}
        with patch('workbench.benchmark.retrieval.httpx.Client') as client:
            client.return_value.__enter__.return_value.post.return_value=response
            engine=Reranker(cfg);self.assertEqual(engine.score('q',['a','b']),[.2,.8])
            response.json.return_value={'results':[{'index':9,'relevance_score':.2}]}
            with self.assertRaises(ValueError): engine.score('q',['a','b'])

    def test_llm_expansion_receives_query_only_and_can_replay(self):
        cfg={'llm':{'api_url':'http://example.test/chat','model_name':'TEST','model_version':'test-v1'},'base_method':'bm25'}
        response=MagicMock();response.json.return_value={'choices':[{'message':{'content':'{"expanded_query":"MDS therapy","entities":[]}'}}]}
        with patch('workbench.benchmark.retrieval.httpx.Client') as client:
            call=client.return_value.__enter__.return_value.post;call.return_value=response
            engine=Retriever('llm_assisted',cfg,public_corpus(DOCS),Path('.'))
            ranks,trace=engine.rank('medical question',20)
            self.assertEqual(call.call_args.kwargs['json']['messages'][1]['content'],'medical question')
            self.assertNotIn('relevance',json.dumps(call.call_args.kwargs['json']))
            call.reset_mock();replayed,_=engine.rank('medical question',20,replay_expansion=trace['expansion'])
            call.assert_not_called();self.assertEqual(ranks,replayed)

    def test_elasticsearch_uses_dedicated_frozen_corpus_and_id_filter(self):
        from workbench.benchmark.store import digest
        docs=public_corpus(DOCS); client=MagicMock()
        def get(url):
            response=MagicMock()
            if url.endswith('/_count'): response.json.return_value={'count':len(docs)}
            elif url.endswith('/_mapping'):
                index=url.split('/')[-2];response.json.return_value={index:{'mappings':{'_meta':{'corpus_hash':digest(docs)}}}}
            else: response.json.return_value={'version':{'number':'TEST server version'}}
            return response
        client.get.side_effect=get;client.head.return_value.status_code=200
        client.post.return_value.json.return_value={'hits':{'hits':[{'_id':'A','_score':1.0}]}}
        with patch('workbench.benchmark.retrieval.httpx.Client',return_value=client):
            engine=Elasticsearch(docs,{'url':'http://example.test'},{});engine.rank('MDS')
            sent=client.post.call_args.kwargs['json']
            self.assertEqual(sent['query']['bool']['filter'][0]['ids']['values'],['A','B','C'])
            self.assertTrue(engine.index.startswith('medbench-'))
            client.get.side_effect=lambda url:MagicMock(json=lambda:{'count':99}) if url.endswith('/_count') else get(url)
            with self.assertRaises(ValueError): Elasticsearch(docs,{'url':'http://example.test'},{})

    def test_changed_local_checkpoint_is_not_silently_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory,'config.json').write_text('{}')
            with self.assertRaises(Unavailable): local_fingerprint({'model_path':directory,'weight_manifest':{'config.json':'not-the-hash'}})
        self.assertEqual(local_fingerprint({}),{})


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        StoreTests.setUp(self);self.runner=ExperimentRunner(self.store)

    async def asyncTearDown(self):
        await self.runner.close();StoreTests.tearDown(self)

    async def run_fixture(self,models=None):
        models=models or [next(m for m in self.store.models() if m['method']=='bm25')['id']]
        run=self.runner.start(self.dataset['snapshot']['id'],models)
        await asyncio.gather(*list(self.runner.tasks))
        return self.store.experiment(run['experiment_ids'][0])

    async def test_real_bm25_saves_metrics_and_exact_replay(self):
        item=await self.run_fixture()
        self.assertEqual(item['status'],'completed')
        self.assertEqual(item['results'][0]['ranking'][0]['document_id'],'A')
        self.assertTrue(self.runner.replay(item['id'])['matches_saved'])
        self.assertIn('dataset_snapshot',json.loads(report(self.store,item['id'],'json')))
        self.assertIn('result_document_ids',report(self.store,item['id'],'csv'))
        self.assertIn('医学检索实验报告',report(self.store,item['id'],'markdown'))

    async def test_missing_model_is_not_run_and_not_fake_score(self):
        model=next(m for m in self.store.models() if m['method']=='embedding')
        item=await self.run_fixture([model['id']])
        self.assertEqual(item['status'],'not_run');self.assertIsNone(item['metrics'])
        self.assertEqual(item['results'],[])

    async def test_out_of_pool_gold_blocks_experiment(self):
        q=query();q['review_complete']=False;q['relevant_documents'][0]['document_id']='OUTSIDE'
        data=self.store.save_query(self.dataset['id'],q,'TEST reviewer')
        with self.assertRaises(ValueError): self.runner.start(data['snapshot']['id'],[self.store.models()[0]['id']])

    async def test_retriever_interface_receives_no_gold(self):
        captured=[]
        class Spy:
            def __init__(self,method,config,corpus,cache): captured.extend(corpus);self.runtime={}
            def rank(self,text,top_k,replay_expansion=None):
                assert text=='MDS therapy trial';return [{'document_id':'A','score':1.0}],{}
            def close(self): pass
        with patch('workbench.benchmark.experiments.Retriever',Spy): await self.run_fixture()
        self.assertTrue(all(set(d)=={'document_id','text'} for d in captured))

    async def test_generation_support_requires_real_quote_and_reviewed_citation(self):
        item=await self.run_fixture()
        payload={'citations':[{'citation_id':'c1','document_id':'A','exists':True,'verification_reference':'TEST reviewed source'}],
                 'claims':[{'claim_id':'x','claim_text':'TEST conclusion','citation_ids':['c1'],'supported':True,'evidence_document_id':'A','evidence_quote':'fabricated quotation'}]}
        with self.assertRaises(ValueError): save_assessment(self.store,item['id'],'TEST_001',payload,'TEST reviewer')
        payload['claims'][0]['evidence_quote']='TEST evidence statement.'
        result=save_assessment(self.store,item['id'],'TEST_001',payload,'TEST reviewer')
        self.assertEqual(result['metrics']['EvidenceSupportRate']['value'],1)
        self.assertIsNone(result['metrics']['ClinicalTrialMatchingAccuracy']['value'])

    async def test_malformed_assessments_rejected_before_storage(self):
        item=await self.run_fixture()
        for payload in [{'citations':['invalid']},{'claims':[{'claim_id':'x','claim_text':'TEST','citation_ids':{}}]}, {'value':float('nan')}]:
            with self.assertRaises(ValueError): save_assessment(self.store,item['id'],'TEST_001',payload,'TEST reviewer')
        self.assertEqual(self.store.experiment(item['id'])['assessments'],[])

    async def test_hard_negatives_use_completed_semantic_run_and_exclude_positives(self):
        model=self.store.add_model({'name':'TEST semantic adapter','version':'fixture','method':'embedding','config':{'enabled':True}})
        class FixtureRetriever:
            def __init__(self,*args): self.runtime={}
            def rank(self,*args,**kwargs): return [{'document_id':'A','score':.9},{'document_id':'C','score':.8},{'document_id':'B','score':.7}],{}
            def close(self): pass
        with patch('workbench.benchmark.experiments.Retriever',FixtureRetriever): item=await self.run_fixture([model['id']])
        rows=mine_negatives(self.store,self.dataset['snapshot']['id'],'TEST_001','hard',experiment_id=item['id'])
        self.assertEqual([r['document_id'] for r in rows],['C'])
        self.assertEqual(rows[0]['status'],'accepted')
        changed=self.store.save_document(self.dataset['id'],{**DOCS[0],'text':'Changed TEST corpus'},'TEST editor')
        with self.assertRaises(ValueError): mine_negatives(self.store,changed['snapshot']['id'],'TEST_001','hard',experiment_id=item['id'])

    async def test_compare_rejects_different_gold_versions(self):
        from workbench.benchmark.api import compare
        first=await self.run_fixture()
        q=query();q['relevant_documents'][0]['relevance']=0
        self.dataset=self.store.save_query(self.dataset['id'],q,'TEST reviewer')
        second=await self.run_fixture()
        request=SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(benchmark=self.store)))
        self.assertFalse(compare(request,first['id']+','+second['id'],'TEST_001')['comparable'])


class APITests(unittest.TestCase):
    def setUp(self):
        StoreTests.setUp(self);self.previous=app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            application.state.benchmark=self.store;application.state.benchmark_runner=ExperimentRunner(self.store)
            yield
            await application.state.benchmark_runner.close()
        app.router.lifespan_context=lifespan
        self.client=TestClient(app,base_url='http://127.0.0.1:8090');self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None);app.router.lifespan_context=self.previous;StoreTests.tearDown(self)

    def test_api_stats_versions_import_validation_and_boundary(self):
        self.assertEqual(self.client.get('/api/benchmark/stats').status_code,200)
        self.assertEqual(self.client.get('/api/benchmark/datasets/missing').status_code,404)
        self.assertEqual(self.client.post('/api/benchmark/datasets',json={'name':'TEST'}).status_code,403)
        headers={'X-Workbench-Client':'1'}
        result=self.client.post('/api/benchmark/datasets',headers=headers,json={'name':'TEST API dataset','domain':'AML'})
        self.assertEqual(result.status_code,201)
        self.assertEqual(self.client.post('/api/benchmark/experiments',headers=headers,json={'dataset_version_id':'x','model_ids':['y'],'top_k':1}).status_code,422)
        self.assertEqual(self.client.post('/api/benchmark/datasets/'+self.dataset['id']+'/queries',headers=headers,json={'record':query(),'actor':'TEST','confirm_human_review':False}).status_code,422)
        exported=self.client.get('/api/benchmark/datasets/'+self.dataset['id']+'/export')
        self.assertEqual(json.loads(exported.text)['query_id'],'TEST_001')


if __name__=='__main__': unittest.main(verbosity=2)
