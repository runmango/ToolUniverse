"""Isolated behavior tests; all synthetic records below are test fixtures only."""
import asyncio
from contextlib import asynccontextmanager
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from workbench.app import app
from workbench.knowledge_graph.store import GraphStore
from workbench.knowledge_graph.ingest import GraphImporter

def trial(nct='NCT99999991', drug='Fixture Drug'):
    return {'nct_id':nct,'brief_title':'TEST FIXTURE ' + nct,'conditions':['Myelodysplastic Syndromes'],
            'interventions':[{'name':drug,'type':'DRUG'}], 'primary_outcomes':[{'measure':'TEST overall survival','time_frame':'12 months'}],
            'references':[{'pmid':'99999991','type':'RESULT','citation':'TEST result paper'}, {'pmid':'99999992','type':'BACKGROUND','citation':'TEST background paper'}],
            'last_update_posted':'2026-01-01'}

def paper(pmid='99999991'):
    return {'pmid':pmid,'title':'TEST MDS research','abstract':'MDS with TP53 mutation and GVHD were discussed.',
            'doi':'10.0000/test-only-' + pmid, 'abstract_sections':[{'label':'RESULTS','text':'TEST result statement, not clinical evidence.'}],
            'databanks':[{'name':'ClinicalTrials.gov','accessions':['NCT99999991']}],
            'mesh_headings':[]}

class GraphTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.store=GraphStore(Path(self.directory.name)/'graph.sqlite')
        self.importer=GraphImporter(self.store, None)

    def tearDown(self):
        self.directory.cleanup()

    def test_domain_synonyms_resolve_to_same_entity_without_fabricated_codes(self):
        ids=[self.store.search(q)[0]['id'] for q in ['MDS','Myelodysplastic Syndrome','Myelodysplastic Syndromes','骨髓增生异常综合征']]
        self.assertEqual(len(set(ids)),1)
        self.assertEqual(self.store.search('MDS')[0]['identifiers'],[])
        self.assertEqual(self.store.stats()['edge_count'],0)

    def test_idempotent_import_keeps_sources_and_directions(self):
        self.importer.import_trial(trial())
        counts=self.store.stats()
        self.importer.import_trial(trial())
        self.assertEqual(self.store.stats()['edge_count'],counts['edge_count'])
        self.assertEqual(self.store.stats()['source_count'],1)
        center=self.store.search('MDS')[0]['id']
        graph=self.store.neighborhood(center,2)
        self.assertIn('Publication',{n['entity_type'] for n in graph['nodes']})
        for edge in graph['edges']:
            evidence=self.store.evidence(edge['id'])
            self.assertEqual(evidence['source_record']['source_id'],'NCT99999991')
            self.assertTrue(evidence['evidence_text'])

    def test_background_citation_is_not_a_trial_result(self):
        self.importer.import_trial(trial())
        center=self.store.search('NCT99999991')[0]['id']
        graph=self.store.neighborhood(center,1)
        refs=[e for e in graph['edges'] if e['relation_type'].endswith('PUBLICATION')]
        self.assertEqual({e['relation_type'] for e in refs},{'HAS_RESULT_PUBLICATION','REFERENCES_PUBLICATION'})

    def test_biological_dli_is_a_normalized_intervention_not_a_drug(self):
        data=trial(); data['interventions']=[{'name':'Donor Lymphocyte Infusion (DLI)','type':'BIOLOGICAL'}]
        self.importer.import_trial(data)
        graph=self.store.neighborhood(self.store.search('MDS')[0]['id'],2)
        self.assertIn('STUDIED_INTERVENTION',{e['relation_type'] for e in graph['edges']})
        nodes=[n for n in graph['nodes'] if n['standard_name']=='供者淋巴细胞输注']
        self.assertEqual(len(nodes),1); self.assertEqual(nodes[0]['entity_type'],'Intervention')
        self.assertEqual(self.store.stats()['counts'].get('Drug',0),0)

    def test_shared_drug_never_merges_trials_or_assigns_publications(self):
        first=trial(); second=trial('NCT99999993'); second['references']=[]
        self.importer.import_trial(first); self.importer.import_trial(second)
        second_id=self.store.search('NCT99999993')[0]['id']
        edges=self.store.neighborhood(second_id,1)['edges']
        self.assertFalse(any(e['relation_type'].endswith('PUBLICATION') for e in edges))
        self.assertEqual(self.store.stats()['counts']['ClinicalTrial'],2)

    def test_pubmed_mentions_are_not_causal_and_outcome_is_not_trial_endpoint(self):
        self.importer.import_trial(trial()); self.importer.import_publication(paper())
        graph=self.store.neighborhood(self.store.search('MDS')[0]['id'],2)
        inferred=[e for e in graph['edges'] if e['relation_type'] in {'ASSOCIATED_GENE','ASSOCIATED_EVENT','HAS_BIOMARKER'}]
        self.assertTrue(inferred)
        self.assertTrue(all(e['verification_status']=='inferred' for e in inferred))
        strong=self.store.neighborhood(self.store.search('MDS')[0]['id'],2,include_inferred=False)
        self.assertTrue(all(e['verification_status']=='source_asserted' for e in strong['edges']))
        self.assertIn('REPORTS_OUTCOME',{e['relation_type'] for e in graph['edges']})
        self.assertIn('PLANS_OUTCOME',{e['relation_type'] for e in graph['edges']})

    def test_paths_respect_inferred_filter(self):
        self.importer.import_publication(paper())
        start=self.store.search('MDS')[0]['id']; end=self.store.search('TP53',types=('Gene',))[0]['id']
        # Explicit mention paths may exist, but a one-hop association is inferred.
        self.assertFalse(self.store.path(start,end,1,False)['found'])
        self.assertTrue(self.store.path(start,end,1,True)['found'])

    def test_limits_and_type_filters_are_explicit(self):
        self.importer.import_trial(trial())
        center=self.store.search('MDS')[0]['id']
        graph=self.store.neighborhood(center,2,limit=2)
        self.assertTrue(graph['truncated']); self.assertLessEqual(len(graph['nodes']),2)
        filtered=self.store.neighborhood(center,1,types=('Drug',))
        self.assertEqual({n['entity_type'] for n in filtered['nodes']},{'Disease','Drug'})

    def test_source_update_retires_old_claims_and_preserves_snapshot(self):
        data=trial(); self.importer.import_trial(data)
        data['interventions']=[]; data['last_update_posted']='2026-02-01'; self.importer.import_trial(data)
        graph=self.store.neighborhood(self.store.search('MDS')[0]['id'],1)
        self.assertNotIn('STUDIED_DRUG',{e['relation_type'] for e in graph['edges']})
        self.assertEqual(self.store.stats()['source_count'],2)

    def test_restart_preserves_sourced_domain_state_and_metadata(self):
        self.importer.import_trial(trial())
        self.importer.import_publication(paper())
        reopened=GraphStore(self.store.db_path)
        self.assertNotIn('vocabulary_only', reopened.search('MDS')[0]['properties'])
        self.assertFalse(reopened.search('NCT99999991')[0]['properties']['metadata_pending'])
        self.assertFalse(reopened.search('PMID 99999991')[0]['properties']['metadata_pending'])

    def test_uniprot_requires_matching_human_gene_and_preserves_provenance(self):
        data={'primaryAccession':'TEST_ACCESSION','genes':[{'geneName':{'value':'TP53'}}],
              'organism':{'taxonId':10090},'entryAudit':{'lastAnnotationUpdateDate':'2026-01-01'}}
        self.assertEqual(self.importer.import_uniprot(data,'TP53'),0)
        data['organism']['taxonId']=9606
        self.assertEqual(self.importer.import_uniprot(data,'TET2'),0)
        self.assertEqual(self.importer.import_uniprot(data,'TP53'),1)
        graph=self.store.neighborhood(self.store.search('TP53')[0]['id'])
        self.assertEqual(graph['edges'][0]['relation_type'],'ENCODES')
        self.assertEqual(self.store.evidence(graph['edges'][0]['id'])['source_record']['source_updated_at'],'2026-01-01')

    def test_same_title_different_pmids_are_distinct(self):
        self.importer.import_publication(paper()); self.importer.import_publication(paper('99999994'))
        self.assertEqual(self.store.stats()['counts']['Publication'],2)

    def test_chembl_ambiguous_or_fuzzy_match_does_not_assign_code(self):
        data={'molecules':[{'pref_name':'Drug Extended','molecule_chembl_id':'CHEMBL999999'}]}
        self.assertEqual(self.importer.import_chembl('Drug',data),0)
        item={'pref_name':'Drug','molecule_chembl_id':'CHEMBL999999'}
        self.assertEqual(self.importer.import_chembl('Drug',{'molecules':[item,item]}),0)

    def test_csv_has_both_nodes_and_edges_and_neutralizes_formulas(self):
        self.importer.import_trial(trial(drug='=TEST()'))
        result=self.store.csv_export(self.store.neighborhood(self.store.search('MDS')[0]['id'],2))
        self.assertIn('relation',result); self.assertIn('entity',result); self.assertIn("'=TEST()",result)

    def test_search_is_parameterized_and_missing_nodes_fail(self):
        self.assertEqual(self.store.search("' OR 1=1 --"),[])
        self.assertEqual(self.store.search('%'),[])
        with self.assertRaises(KeyError): self.store.get('missing')
        with self.assertRaises(ValueError): self.store.neighborhood(self.store.search('MDS')[0]['id'],3)

class GraphAPITests(unittest.TestCase):
    def setUp(self):
        GraphTests.setUp(self)
        self.previous=app.router.lifespan_context
        @asynccontextmanager
        async def lifespan(application):
            application.state.graph=self.store
            application.state.graph_importer=self.importer
            yield
        app.router.lifespan_context=lifespan
        self.client=TestClient(app,base_url='http://127.0.0.1:8090')
        self.client.__enter__()

    def tearDown(self):
        self.client.__exit__(None,None,None)
        app.router.lifespan_context=self.previous
        GraphTests.tearDown(self)

    def test_api_lookup_graph_evidence_export_and_boundary(self):
        self.importer.import_trial(trial())
        result=self.client.get('/api/graph/entities?q=MDS')
        self.assertEqual(result.status_code,200)
        center=result.json()['entities'][0]['id']
        graph=self.client.get('/api/graph/neighborhood',params={'center':center,'depth':2}).json()
        self.assertEqual(self.client.get('/api/graph/edges/'+graph['edges'][0]['id']).status_code,200)
        data=self.client.get('/api/graph/export',params={'center':center}).json()
        self.assertTrue(data['evidence'])
        self.assertIn('text/csv',self.client.get('/api/graph/export',params={'center':center,'format':'csv'}).headers['content-type'])
        self.assertEqual(self.client.get('/api/graph/entities/missing').status_code,404)
        self.assertEqual(self.client.post('/api/graph/sync',json={'query':'MDS'}).status_code,403)
        self.assertEqual(self.client.post('/api/graph/sync',headers={'X-Workbench-Client':'1'},json={'query':'MDS','limit':500}).status_code,422)

class OfflineTests(unittest.IsolatedAsyncioTestCase):
    async def test_provider_failure_is_reported_without_demo_data(self):
        class Offline:
            async def load(self): raise RuntimeError('TEST upstream unavailable')
        with tempfile.TemporaryDirectory() as directory:
            store=GraphStore(Path(directory)/'g.sqlite')
            importer=GraphImporter(store,Offline())
            run=importer.start('MDS')
            await asyncio.gather(*list(importer.tasks))
            result=importer.run(run['id'])
            self.assertEqual(result['status'],'failed')
            self.assertIn('unavailable',result['report']['errors'][0]['message'])
            self.assertEqual(store.stats()['edge_count'],0)

if __name__=='__main__': unittest.main(verbosity=2)
