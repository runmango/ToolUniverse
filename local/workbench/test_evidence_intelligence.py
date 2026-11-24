"""Explicit TEST ONLY evidence fixtures. Never write clinical Gold into the workbench."""
import asyncio
import copy
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from workbench.knowledge_graph.store import GraphStore
from workbench.knowledge_graph.ingest import GraphImporter
from workbench.medical_core.service import ResearchService
from workbench.medical_core.http import install,versioned
from workbench.benchmark.store import BenchmarkStore
from workbench.evidence_intelligence.models import AnalyzeRequest
from workbench.evidence_intelligence import pico,extraction,quality
from workbench.evidence_intelligence.grounding import validate_extraction,numbers,field,direction
from workbench.evidence_intelligence.service import IntelligenceService
from workbench.evidence_intelligence.evaluation import evaluate
from workbench.evidence_intelligence.api import router

def paper(pmid='99999991',second=False):
    return {'pmid':pmid,'title':'TEST ONLY MDS '+('retrospective cohort' if second else 'randomized controlled trial'),
            'doi':'10.0000/test.'+pmid,'journal':'TEST ONLY journal','pub_date':'2020',
            'abstract':('METHODS: In a retrospective cohort, n=70 patients with MDS received venetoclax plus azacitidine versus azacitidine. Median follow-up was 12 months. RESULTS: No significant improvement in overall survival was observed (HR 1.05; 95% CI 0.85-1.20; P=0.65).' if second else
                        'METHODS: In a randomized controlled trial, 100 patients with MDS received venetoclax plus azacitidine versus azacitidine. The primary outcome was overall survival. Median follow-up was 24 months. RESULTS: Overall survival significantly improved (HR 0.70; 95% CI 0.55-0.89; P=0.004). CONCLUSIONS: Overall survival improved in this TEST ONLY population.'),
            'publication_types':['Journal Article'],'abstract_sections':[]}

class GroundingTests(unittest.TestCase):
    def test_pico_missing_comparator_outcome_are_not_invented(self):
        q='MDS患者接受Venetoclax联合Azacitidine治疗的效果如何？'
        p=pico.validate(q,pico.rules(q));self.assertIsNone(p['comparison']['value']);self.assertIsNone(p['outcome']['value'])
        self.assertEqual(p['intervention']['value'],'Venetoclax联合Azacitidine')
    def test_pico_generated_conditions_are_rejected(self):
        with self.assertRaises(ValueError): pico.validate('MDS therapy?',{'comparison':{'value':'placebo','quote':'placebo'}})
    def test_generic_efficacy_not_a_specific_pico_outcome(self):
        p=pico.validate('MDS疗效如何',{'outcome':{'value':'疗效','quote':'疗效'}})
        self.assertIsNone(p['outcome']['value']);self.assertEqual(p['outcome']['raw_candidate'],'疗效')
    def test_middle_dot_decimal_scientific_p_and_standalone_ci(self):
        n=numbers('HR 0·70 (95% CI 0·55–0·89); P=1e-4. Duration: 95% CI 10-NR.')
        self.assertEqual(n['ratios'][0]['value'],.7);self.assertEqual(n['p_values'][0]['value'],.0001)
        self.assertIsNone(n['confidence_intervals'][-1]['upper'])
    def test_quotes_and_values_must_be_exact(self):
        with self.assertRaises(ValueError): field('n=100',{'value':'200','quote':'n=100'})
        with self.assertRaises(ValueError): field('n=100',{'value':'200','quote':'n=200'})
    def test_missing_fields_not_reported(self):
        p=validate_extraction({},'An abstract.');self.assertTrue(all(f['status']=='not_reported' for f in p['fields'].values()))
    def test_numerics_preserve_units_operators_and_arm_counts(self):
        n=numbers('OS: HR 0.70 (95% CI 0.55–0.89); P < 0.001; n=182 vs n=181.')
        self.assertEqual(n['ratios'][0]['ci95']['upper'],.89)
        self.assertEqual([r['value'] for r in n['sample_counts']],[182,181])
        self.assertEqual(n['p_values'][0]['operator'],'<')
        self.assertEqual(numbers('n=1.5')['sample_counts'],[])
    def test_invalid_numeric_bounds_flagged(self):
        n=numbers('HR 2.0 (95% CI 0.2-0.9); P=2')
        self.assertTrue(n['ratios'][0]['validation_issues']);self.assertFalse(n['p_values'][0]['valid'])
    def test_ratio_not_confused_with_or_and_unrelated_interval(self):
        self.assertEqual(numbers('0.5 mg or 1 mg')['ratios'],[])
        n=numbers('OS: HR 0.7 was reported. Response: 95% CI 0.5-0.8.')
        self.assertIsNone(n['ratios'][0]['ci95'])
        with self.assertRaises(ValueError): numbers('P=1e999')
    def test_nonsignificant_does_not_become_benefit(self):
        q='No significant improvement in overall survival was observed (HR 1.05; P=0.65).'
        p=validate_extraction({'findings':[{'quote':q,'outcome':'OS','direction':'benefit'}]},q)
        self.assertEqual(p['findings'][0]['direction'],'no_significant_benefit')
    def test_planned_endpoint_not_observed_effect(self):
        q='This ongoing trial will evaluate overall survival.'
        with self.assertRaises(ValueError): validate_extraction({'findings':[{'quote':q,'outcome':'OS'}]},q)
    def test_unlabeled_endpoint_not_promoted_to_primary(self):
        q='Overall survival was 20 months.'
        with self.assertRaises(ValueError): validate_extraction({'fields':{'primary_outcomes':{'value':q,'quote':q}}},q)
    def test_nonrandomized_not_randomized(self):
        q='A non-randomized cohort study.'
        with self.assertRaises(ValueError): validate_extraction({'design':'randomized_controlled_trial','design_quote':'randomized'},q)
    def test_rule_baseline_real_extraction_has_spans(self):
        raw=paper();p=validate_extraction(extraction.baseline(raw),raw['abstract'],raw['title'])
        self.assertEqual(p['design']['type'],'randomized_controlled_trial');self.assertTrue(p['findings'])
        for f in p['findings']: self.assertEqual(raw['abstract'][f['span']['start']:f['span']['end']],f['statement'])
    def test_model_cannot_supply_unvalidated_identifiers_or_effect_values(self):
        with self.assertRaises(ValueError): validate_extraction({'pmid':'123','HR':0.1},'TEST ONLY')

class IntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp=tempfile.TemporaryDirectory();path=Path(self.tmp.name)/'TEST-only.sqlite'
        self.graph=GraphStore(path);self.importer=GraphImporter(self.graph,None)
        self.importer.import_publication(paper());self.importer.import_publication(paper('99999992',True))
        self.research=ResearchService(path);self.benchmark=BenchmarkStore(path)
        self.service=IntelligenceService(self.research,self.importer,self.benchmark,None)
        self.release=self.research.create_release('TEST ONLY')
        self.cfg=AnalyzeRequest(question='MDS venetoclax overall survival',live_search=False,release_id=self.release['id'],limit=2).model_dump()
    async def asyncTearDown(self):
        await self.service.close();await self.importer.close();self.tmp.cleanup()
    async def run_analysis(self):
        run=self.service.start(self.cfg);await self.service.tasks[run['id']];return self.service.get(run['id'])
    async def test_full_pipeline_and_shared_entities(self):
        before=self.graph.stats()['counts'];run=await self.run_analysis()
        self.assertEqual(run['status'],'completed');self.assertEqual(len(run['extractions']),2)
        self.assertEqual(before,self.graph.stats()['counts']);self.assertTrue(run['current_comparisons'])
        self.assertTrue(any(c['status']=='direction_difference' for c in run['current_comparisons']))
        self.assertTrue(all(x['payload']['source_scope']=='abstract' for x in run['extractions']))
    async def test_semantic_failures_are_explicit_rule_fallback(self):
        self.cfg.update(mode='account',model='TEST MODEL')
        with patch('workbench.evidence_intelligence.llm.SemanticExtractor.generate',side_effect=ValueError('TEST invalid model')):
            run=await self.run_analysis()
        self.assertEqual(run['status'],'partial');self.assertTrue(run['payload']['errors'])
        self.assertTrue(all(x['payload']['generation']['fallback_from']=='account' for x in run['extractions']))
    async def test_extraction_immutable_and_reviews_append(self):
        run=await self.run_analysis();row=run['extractions'][0]
        self.research.review_revision(row['revision_id'],'TEST REVIEWER','rejected','TEST evidence mismatch')
        self.assertEqual(self.service.extraction(row['id'])['review_status'],'rejected')
        with self.research.connect() as db:
            with self.assertRaises(sqlite3.IntegrityError): db.execute('DELETE FROM ei_extractions WHERE id=?',(row['id'],))
        self.assertEqual(self.service.get(run['id'])['current_comparisons'],[])
    async def test_quality_requires_human_and_applicable_tool(self):
        run=await self.run_analysis();row=next(x for x in run['extractions'] if x['payload']['design']['type']=='randomized_controlled_trial')
        base={'actor':'TEST REVIEWER','tool':'rob2_2019','target':'OS','judgments':{},'overall':None,'rationale':'TEST','confirm_human_review':False}
        with self.assertRaises(ValueError): self.service.appraisal(row['id'],base)
        base['confirm_human_review']=True;base['overall']='low'
        with self.assertRaises(ValueError): self.service.appraisal(row['id'],base)
        base['overall']=None;record=self.service.appraisal(row['id'],base);self.assertEqual(record['status'],'incomplete')
        base['tool']='amstar2_2017'
        with self.assertRaises(ValueError): self.service.appraisal(row['id'],base)
    async def test_multi_paper_claim_requires_real_source_quotes(self):
        run=await self.run_analysis();row=run['extractions'][0]
        body={'text':'TEST ONLY comparison','actor':'TEST REVIEWER','reason':'TEST manual judgement','confirm_human_review':True,
              'citations':[{'extraction_id':row['id'],'quote':'fake invented sentence','relationship':'supports'}]}
        with self.assertRaises(ValueError): self.service.statement(run['id'],body)
        body['citations']=[{'extraction_id':r['id'],'quote':r['payload']['findings'][0]['statement'],'relationship':'supports' if i==0 else 'contradicts'} for i,r in enumerate(run['extractions'])]
        claim=self.service.statement(run['id'],body);self.assertEqual(len(claim['citations']),2)
    async def test_benchmark_gold_is_independent_and_auditable(self):
        run=await self.run_analysis();row=run['extractions'][0]
        base={'analysis_id':run['id'],'actor':'TEST REVIEWER','confirm_human_review':True,'labels':[]}
        self.assertIsNone(evaluate(self.service,base)['metrics']['FieldExactMatch'])
        base['labels']=[{'extraction_id':row['id'],'field':'limitations','expected':None,'reason':'TEST not present'}]
        self.assertEqual(evaluate(self.service,base)['metrics']['FieldExactMatch'],1)
    async def test_versioned_api_and_report_are_real(self):
        run=await self.run_analysis();app=FastAPI();app.state.intelligence=self.service
        app.include_router(router);app.include_router(versioned(router));install(app)
        with TestClient(app) as client:
            result=client.get('/api/v1/evidence-intelligence/analyses/'+run['id']+'/export').json()
            self.assertEqual(result['release']['id'],self.release['id']);self.assertEqual(len(result['extractions']),2)
            self.assertEqual(client.get('/api/evidence-intelligence/analyses/missing').status_code,404)
            response=client.post('/api/v1/evidence-intelligence/analyses',json={'question':'a','limit':99})
            self.assertEqual(response.status_code,422);self.assertIn('error',response.json())
    async def test_no_matching_corpus_remains_empty(self):
        self.cfg.update(search_query='xyznonexistentterm')
        run=await self.run_analysis();self.assertEqual(run['status'],'empty');self.assertEqual(run['extractions'],[])

if __name__=='__main__': unittest.main(verbosity=2)
