"""Cross-module tests use temporary databases and explicit TEST ONLY fixtures."""
import asyncio
import ast
from contextlib import asynccontextmanager, closing
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0,str(Path(__file__).parents[1]))
from fastapi.testclient import TestClient
from workbench.app import app
from workbench.medical_core.identifiers import identifier,document_id
from workbench.medical_core.contracts import document_record
from workbench.medical_core.provenance import generation
from workbench.medical_core.service import ResearchService,eligible_edges
from workbench.medical_core.sources import MedicalSources
from workbench.medical_core.migration import migrate,rows,TABLES
from workbench.knowledge_graph.store import GraphStore
from workbench.knowledge_graph.ingest import GraphImporter
from workbench.evidence_chain.service import EvidenceService,build_chain
from workbench.benchmark.store import BenchmarkStore
from workbench.benchmark.experiments import ExperimentRunner
from workbench.benchmark.graph_retrieval import GraphRetriever
from workbench.benchmark.research_evaluation import evaluate

def paper(): return {'pmid':'99999991','title':'TEST ONLY MDS research','abstract':'TEST MDS with TP53 mutation.','doi':'10.0000/test-only','mesh_headings':[]}
def trial(): return {'nct_id':'NCT99999991','brief_title':'TEST ONLY trial','conditions':['MDS'],'interventions':[{'name':'TEST ONLY drug','type':'DRUG'}],'references':[{'pmid':'99999991','type':'RESULT'}]}

class ContractTests(unittest.TestCase):
    def test_identifier_normalization_and_conflict(self):
        self.assertEqual(identifier('doi','https://doi.org/10.1234/TEST'),('DOI','10.1234/test'))
        self.assertEqual(document_id({'nct_id':'nct12345678'}),'NCT12345678')
        self.assertEqual(document_id({'doi':'doi:10.1234/TEST'}),'DOI:10.1234/test')
        with self.assertRaises(ValueError): document_id({'document_id':'PMID:123','pmid':'456'})
        with self.assertRaises(ValueError): document_id({'pmid':'123','identifiers':{'PMID':'456'}})
    def test_original_identifier_preserved(self):
        d=document_record({'doi':'https://doi.org/10.1234/TEST','title':'TEST'})
        self.assertEqual(d['identifiers']['DOI'],'10.1234/test')
        self.assertEqual(d['identifier_inputs']['doi'],'https://doi.org/10.1234/TEST')
    def test_missing_ai_version_is_explicit_not_fabricated(self):
        item=generation({'annotation_source':'ai'})['generation']
        self.assertEqual(item['model_version'],'unknown');self.assertEqual(item['metadata_status'],'incomplete')
        known=generation({'annotation_source':'ai','generation':{'model_name':'TEST MODEL','model_version':'TEST REVISION'}})
        self.assertEqual(known['generation']['model_version'],'TEST REVISION')
    def test_dependency_direction_and_single_entity_definition(self):
        root=Path(__file__).parent
        for folder in ('benchmark','knowledge_graph','evidence_chain'):
            for file in (root/folder).glob('*.py'):
                tree=ast.parse(file.read_text(encoding='utf-8'))
                for node in ast.walk(tree):
                    if isinstance(node,ast.Assign): self.assertFalse(any(isinstance(t,ast.Name) and t.id=='ENTITY_TYPES' for t in node.targets),str(file))
        for file in (root/'medical_core').glob('*.py'):
            for node in ast.walk(ast.parse(file.read_text(encoding='utf-8'))):
                if isinstance(node,ast.ImportFrom): self.assertFalse(any(x in (node.module or '').split('.') for x in ('benchmark','knowledge_graph','evidence_chain')),str(file))

class PlatformTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'knowledge-graph.sqlite'
        self.graph=GraphStore(self.path);self.importer=GraphImporter(self.graph,None)
        self.importer.import_trial(trial());self.importer.import_publication(paper())
        self.research=ResearchService(self.path);self.evidence=EvidenceService(self.research)
        self.release=self.research.create_release('TEST ONLY baseline')
        self.start=self.graph.search('MDS',types=('Disease',))[0]['id']
        self.pub=self.graph.search('99999991',types=('Publication',))[0]['id']
    def tearDown(self): self.tmp.cleanup()
    def test_release_and_relation_history_survive_source_update(self):
        before=self.release['payload'];old=next(e for e in before['edges'] if e['relation_type']=='HAS_RESULT_PUBLICATION')
        changed=trial();changed['references']=[];self.importer.import_trial(changed)
        after=self.research.create_release('TEST change')
        self.assertEqual(self.research.release(self.release['id'])['payload'],before)
        self.assertNotIn(old['id'],{e['id'] for e in after['payload']['edges']})
        with self.research.connect() as db:
            self.assertGreater(db.execute('SELECT COUNT(*) FROM research_revisions WHERE object_id=?',(old['id'],)).fetchone()[0],1)
            with self.assertRaises(sqlite3.IntegrityError): db.execute('UPDATE research_releases SET note=? WHERE id=?',('overwrite',self.release['id']))
    def test_chain_uses_same_entity_and_relation_revisions(self):
        chain=self.evidence.create(self.release['id'],'TEST MDS',self.start,self.pub)
        self.assertTrue(chain['payload']['steps'])
        versions={e['revision_id'] for e in self.release['payload']['edges']}
        self.assertTrue(all(s['relation_revision_id'] in versions for s in chain['payload']['steps']))
        with self.research.connect() as db: self.assertEqual(db.execute('SELECT COUNT(*) FROM kg_entities').fetchone()[0],len(self.release['payload']['nodes']))
    def test_disconnected_chain_never_invents_links(self):
        aml=self.graph.search('AML',types=('Disease',))[0]['id']
        result=build_chain(self.release['payload'],aml,self.pub)
        self.assertEqual(result['status'],'insufficient_evidence');self.assertEqual(result['steps'],[])
    def test_direction_and_inferred_policy_are_explicit(self):
        directed=build_chain(self.release['payload'],self.pub,self.start,directed=True)
        self.assertTrue(all(s['source_entity']==self.pub for s in directed['steps']))
        inferred={e['id'] for e in self.release['payload']['edges'] if e['verification_status']=='inferred'}
        self.assertTrue(inferred)
        default=build_chain(self.release['payload'],self.start)
        self.assertFalse(inferred & {s['relation_id'] for s in default['steps']})
    def test_review_append_only_and_frozen_release_is_unchanged(self):
        edge=next(e for e in self.release['payload']['edges'] if e['relation_type']=='HAS_TRIAL')
        self.research.review_revision(edge['revision_id'],'TEST reviewer','rejected','TEST invalid relation')
        later=self.research.create_release('TEST reviewed')
        self.assertIn(edge['id'],{e['id'] for e in eligible_edges(self.release['payload'])})
        self.assertNotIn(edge['id'],{e['id'] for e in eligible_edges(later['payload'])})
        self.assertNotIn(edge['id'],{e['id'] for e in self.graph.neighborhood(self.start,2)['edges']})
        self.assertEqual(self.graph.evidence(edge['id'])['human_review_status'],'rejected')
        with self.research.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM research_reviews')
    def test_benchmark_document_binding_and_snapshot_immutability(self):
        bench=BenchmarkStore(self.path);dataset=bench.datasets()[0]
        saved=bench.import_graph(dataset['id'],self.graph)['snapshot']
        with self.research.connect() as db:
            binding=db.execute('SELECT * FROM research_bindings WHERE dataset_version_id=?',(saved['id'],)).fetchone()
            self.assertEqual(binding['entity_id'],self.pub)
        bench.save_document(dataset['id'],{'pmid':'99999991','title':'TEST changed text','text':'TEST'},'TEST editor')
        self.assertEqual(bench.snapshot(saved['id']),saved)
    def test_graph_retrieval_only_sees_frozen_corpus_source_text(self):
        doc={'document_id':'PMID:99999991','text':paper()['title']+'\n'+paper()['abstract']}
        engine=GraphRetriever(self.release,[doc],{})
        ranking,trace=engine.rank('MDS',20)
        self.assertEqual(ranking[0]['document_id'],doc['document_id'])
        self.assertTrue(all(s['provider']=='PubMed' for s in engine.payload['sources'].values()))
        self.assertEqual(GraphRetriever(self.release,[{**doc,'text':'changed'}],{}).payload['sources'],{})
    def test_graph_evaluation_missing_gold_null_and_invalid_paths_rejected(self):
        body={'actor':'TEST reviewer','confirm_human_review':True,'release_id':self.release['id'],'task_type':'graph','query':'MDS'}
        empty=evaluate(self.research,self.evidence,body)
        self.assertIsNone(empty['metrics']['EntityRecall']);self.assertEqual(empty['evaluation_status'],'awaiting_gold')
        valid=evaluate(self.research,self.evidence,{**body,'gold':{'entity_ids':[self.start]}})
        self.assertEqual(valid['metrics']['EntityRecall'],1)
        with self.assertRaises(ValueError): evaluate(self.research,self.evidence,{**body,'gold':{'entity_ids':['nonexistent']}})
    def test_chain_generation_metrics_are_separate_and_audited(self):
        chain=self.evidence.create(self.release['id'],'TEST',self.start,self.pub)
        step=chain['payload']['steps'][0]
        body={'actor':'TEST reviewer','confirm_human_review':True,'release_id':self.release['id'],'task_type':'evidence_chain','chain_id':chain['id'],
              'judgments':[{'relation_id':step['relation_id'],'citation_exists':True,'supports_statement':False,'reason':'TEST contradiction'}]}
        result=evaluate(self.research,self.evidence,body)
        self.assertEqual(result['metrics']['CitationAccuracy']['value'],1)
        self.assertEqual(result['metrics']['EvidenceSupportRate']['value'],0)
        self.assertIsNone(result['metrics']['ClinicalTrialMatchingAccuracy']['value'])
        self.assertTrue(self.research.reviews(result['id']))
        body['judgments'][0].update(citation_exists=False,supports_statement=True)
        with self.assertRaises(ValueError): evaluate(self.research,self.evidence,body)
    def test_migration_preserves_all_rows_and_backups(self):
        legacy=BenchmarkStore(Path(self.tmp.name)/'benchmark.sqlite')
        with legacy.connect() as db: before={t:rows(db,t) for t in TABLES}
        result=migrate(Path(self.tmp.name));self.assertEqual(result['status'],'applied')
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual({t:rows(db,t) for t in TABLES},before)
            self.assertEqual(db.execute('PRAGMA foreign_key_check').fetchall(),[])
        self.assertTrue((Path(result['backups'])/'benchmark.sqlite').exists())
        self.assertEqual(migrate(Path(self.tmp.name))['status'],'already_applied')
    def test_migration_conflict_rolls_back_without_overwriting(self):
        local=BenchmarkStore(self.path);BenchmarkStore(Path(self.tmp.name)/'benchmark.sqlite')
        with local.connect() as db: before={t:rows(db,t) for t in TABLES}
        with self.assertRaises(ValueError): migrate(Path(self.tmp.name))
        with local.connect() as db: self.assertEqual({t:rows(db,t) for t in TABLES},before)
    def test_source_adapter_preserves_success_and_failure(self):
        class Tools:
            async def run(self,name,args):
                if name=='fail': raise RuntimeError('TEST failure')
                return {'data':{'pmid':'99999991','TEST_ONLY':True}}
        adapter=MedicalSources(Tools(),self.research)
        self.assertIn('data',asyncio.run(adapter.run('TEST',{})))
        with self.assertRaises(RuntimeError): asyncio.run(adapter.run('fail',{}))
        with self.research.connect() as db:
            self.assertEqual({r[0] for r in db.execute('SELECT status FROM research_source_fetches')},{'completed','failed'})
    def test_versioned_routes_errors_and_legacy_compatibility(self):
        previous=app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            application.state.research=self.research;application.state.graph=self.graph;application.state.evidence=self.evidence;application.state.research_error=None
            yield
        app.router.lifespan_context=lifespan
        try:
            with TestClient(app,base_url='http://127.0.0.1:8090') as client:
                self.assertEqual(client.get('/api/graph/stats').json(),client.get('/api/v1/graph/stats').json())
                response=client.get('/api/v1/research/releases/missing')
                self.assertEqual(response.status_code,404);self.assertEqual(response.json()['error']['request_id'],response.headers['X-Request-ID'])
                blocked=client.post('/api/v1/evidence-chains',json={})
                self.assertEqual(blocked.status_code,403)
                self.assertIn('error',blocked.json())
                app.state.research_error='TEST unavailable'
                self.assertEqual(client.get('/api/v1/research/releases').status_code,503)
                self.assertEqual(client.get('/api/health').status_code,200)
        finally: app.router.lifespan_context=previous;app.state.research_error=None

if __name__=='__main__': unittest.main(verbosity=2)
