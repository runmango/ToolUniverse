"""Bounded live imports through existing ToolUniverse interfaces. No synthetic edges."""
import asyncio
import re
import uuid

from ..tools import logical_error
from .model import DOMAINS, domain_for, mentioned_domains, normalize
from ..medical_core.db import encoded, now

GENES = ('TP53','ASXL1','TET2','RUNX1','FLT3','NPM1','IDH1','IDH2','DNMT3A','SF3B1','SRSF2','BCL2')

def unwrap(result):
    error = logical_error(result)
    if error:
        raise RuntimeError(error)
    return result.get('data', result) if isinstance(result, dict) else result

class GraphImporter:
    def __init__(self, store, tools):
        self.store, self.tools = store, tools
        self.lock = asyncio.Lock()
        self.tasks = set()
        # An interrupted server must never leave a run looking active forever.
        with store.connect() as db:
            db.execute("UPDATE kg_sync_runs SET status='interrupted',finished_at=? WHERE status='running'", (now(),))

    async def close(self):
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)

    def start(self, query, limit=5, molecular=True):
        if self.tasks:
            raise ValueError('已有图谱同步正在进行，请等待完成')
        run_id = uuid.uuid4().hex
        with self.store.connect() as db:
            db.execute('INSERT INTO kg_sync_runs VALUES (?,?,?,?,?,?)', (run_id, query, 'running', now(), None, '{}'))
        task = asyncio.create_task(self.sync(run_id, query, limit, molecular))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return self.run(run_id)

    def run(self, run_id):
        import json
        with self.store.connect() as db:
            row = db.execute('SELECT * FROM kg_sync_runs WHERE id=?', (run_id,)).fetchone()
            if not row:
                raise KeyError('同步记录不存在')
            return {**dict(row), 'report': json.loads(row['report'])}

    async def call(self, name, args):
        # Loading registers tool definitions; no LLM is needed for ingestion.
        return unwrap(await self.tools.run(name, args))

    async def sync(self, run_id, query, limit, molecular):
        report = {'imported': {'trials': 0, 'publications': 0, 'proteins': 0, 'drugs': 0}, 'errors': [],
                  'limits': {'search_results_per_source': limit, 'linked_publications': 12, 'molecular_entities': 3},
                  'data_mode': 'live', 'note': '有界检索，非穷尽数据。未成功获取的来源不会用演示关系替代。'}
        seen_papers, drug_names, gene_names = set(), set(), set()

        async def attempt(label, action):
            report['current_source'] = label
            try:
                return await asyncio.wait_for(action(), timeout=100)
            except Exception as exc:
                report['errors'].append({'source': label, 'message': str(exc)[:600]})
                return None
            finally:
                with self.store.connect() as db:
                    db.execute('UPDATE kg_sync_runs SET report=? WHERE id=?', (encoded(report), run_id))

        async def publication(pmid):
            pmid = str(pmid)
            if pmid in seen_papers or not re.fullmatch(r'\d+', pmid):
                return
            seen_papers.add(pmid)
            data = await self.call('PubMed_get_article', {'pmid': pmid})
            if isinstance(data, list):
                data = next((x for x in data if str(x.get('pmid')) == pmid), {})
            if not data.get('title') or str(data.get('pmid')) != pmid:
                raise ValueError('PubMed 未返回匹配的论文记录')
            gene_names.update(self.import_publication(data))
            report['imported']['publications'] += 1

        try:
            async with self.lock:
                await self.tools.load()
                domain = domain_for(query)
                phrase = domain['english'] if domain else query.strip()
                if re.fullmatch(r'NCT\d{8}', query, re.I):
                    ids = [query.upper()]
                elif re.fullmatch(r'(?:PMID\s*:?\s*)?\d+', query, re.I):
                    ids = []
                    await attempt('PubMed', lambda: publication(re.sub(r'\D', '', query)))
                else:
                    field = 'query_cond' if domain and domain['type'] in {'Disease','ClinicalEvent'} else 'query_term'
                    result = await attempt('ClinicalTrials.gov search', lambda: self.call('ClinicalTrials_search_studies', {field: '(' + phrase + ')', 'page_size': limit}))
                    studies = result.get('studies', []) if isinstance(result, dict) else []
                    ids = [s['nct_id'] for s in studies if re.fullmatch(r'NCT\d{8}', str(s.get('nct_id','')))]
                    report['trial_search'] = {'returned': len(ids), 'total': result.get('total_count', result.get('totalCount')) if isinstance(result, dict) else None,
                                              'bounded': True}
                    papers = await attempt('PubMed search', lambda: self.call('PubMed_search_articles', {'query': phrase, 'limit': limit, 'sort': 'relevance'}))
                    articles = papers if isinstance(papers, list) else papers.get('articles', []) if isinstance(papers, dict) else []
                    report['publication_search'] = {'returned': len(articles), 'bounded': True}
                    for paper in articles[:limit]:
                        if paper.get('pmid'):
                            await attempt('PubMed ' + str(paper['pmid']), lambda p=paper['pmid']: publication(p))
                linked_count = 0
                for nct in ids[:limit]:
                    study = await attempt(nct, lambda n=nct: self.call('ClinicalTrials_get_study', {'nct_id': n}))
                    if not isinstance(study, dict) or study.get('nct_id') != nct:
                        continue
                    try:
                        drugs, pmids = self.import_trial(study)
                        drug_names.update(drugs)
                        report['imported']['trials'] += 1
                        report.setdefault('imported_trial_ids', []).append(nct)
                    except Exception as exc:
                        report['errors'].append({'source': nct, 'message': str(exc)[:600]})
                        continue
                    for pmid in pmids:
                        if linked_count >= 12:
                            break
                        if pmid not in seen_papers:
                            linked_count += 1
                            await attempt('PubMed ' + pmid, lambda p=pmid: publication(p))
                if molecular:
                    for name in sorted(drug_names)[:3]:
                        result = await attempt('ChEMBL ' + name, lambda n=name: self.call('ChEMBL_search_molecules', {'query': n, 'limit': 10}))
                        if isinstance(result, dict):
                            report['imported']['drugs'] += self.import_chembl(name, result)
                    for gene in sorted(gene_names)[:3]:
                        result = await attempt('UniProt ' + gene, lambda g=gene: self.call('UniProt_search', {'query': 'gene_exact:' + g + ' AND taxonomy_id:9606 AND reviewed:true', 'limit': 2, 'fields': ['accession','gene_names','protein_name','organism_id']}))
                        entries = result.get('results', []) if isinstance(result, dict) else []
                        for entry in entries[:1]:
                            accession = entry.get('primaryAccession')
                            if accession:
                                protein = await attempt('UniProt ' + accession, lambda a=accession: self.call('UniProt_get_entry_by_accession', {'accession': a, 'compact': False, 'include_publications': False}))
                                if isinstance(protein, dict):
                                    report['imported']['proteins'] += self.import_uniprot(protein, gene)
                status = 'partial' if report['errors'] else 'completed'
                if not sum(report['imported'].values()):
                    status = 'failed' if report['errors'] else 'empty'
        except asyncio.CancelledError:
            status = 'interrupted'
        except Exception as exc:
            status = 'failed'
            report['errors'].append({'source': 'ToolUniverse', 'message': str(exc)[:600]})
        with self.store.connect() as db:
            db.execute('UPDATE kg_sync_runs SET status=?,finished_at=?,report=? WHERE id=?', (status, now(), encoded(report), run_id))
        return report

    def import_trial(self, data):
        nct = data.get('nct_id', '')
        if not re.fullmatch(r'NCT\d{8}', nct):
            raise ValueError('无效 NCT ID')
        drugs, pmids = [], []
        with self.store.connect() as db:
            source = self.store.source(db, 'ClinicalTrials.gov', nct, 'https://clinicaltrials.gov/study/' + nct, data, data.get('last_update_posted'))
            trial = self.store.entity(db, 'ClinicalTrial', data.get('brief_title') or nct, aliases=[nct], identifiers={'NCT': nct}, source=source,
                                      properties={**{k:data.get(k) for k in ('status','phases','enrollment','brief_summary','study_type')}, 'metadata_pending':False})
            if data.get('brief_title'):
                db.execute('UPDATE kg_entities SET standard_name=?,normalized_name=? WHERE id=?', (data['brief_title'], normalize(data['brief_title']), trial))
            conditions = []
            for index, name in enumerate(data.get('conditions') or []):
                codes = { 'MeSH': m['id'] for m in data.get('condition_meshes', []) if normalize(m.get('term','')) == normalize(name) and re.fullmatch(r'D\d+', str(m.get('id',''))) }
                entity = self.store.entity(db, 'Disease', name, identifiers=codes, source=source)
                self.store.edge(db, 'HAS_TRIAL', entity, trial, source, f'conditions[{index}]', name)
                if (domain_for(name) or {}).get('type', 'Disease') == 'Disease':
                    conditions.append(entity)
            for index, item in enumerate(data.get('interventions') or []):
                name = item.get('name')
                if not name:
                    continue
                kind = 'Drug' if item.get('type') in {'DRUG','BIOLOGICAL'} else 'Intervention'
                if (domain_for(name) or {}).get('type') == 'Intervention':
                    kind = 'Intervention'
                intervention = self.store.entity(db, kind, name, aliases=item.get('other_names') or [], source=source,
                                                properties={'registered_type': item.get('type')})
                field = f'interventions[{index}]'
                self.store.edge(db, 'INVESTIGATED_IN', intervention, trial, source, field, encoded(item))
                if kind in {'Drug', 'Intervention'}:
                    if kind == 'Drug':
                        drugs.append(name)
                    for disease in conditions:
                        self.store.edge(db, 'STUDIED_DRUG' if kind == 'Drug' else 'STUDIED_INTERVENTION', disease, intervention, source, field, encoded({'conditions': data.get('conditions'), 'intervention': item}),
                                        qualifiers={'trial': nct, 'scope': '同一试验登记的疾病与干预；不证明获益或适应症获批。'})
            for index, item in enumerate(data.get('primary_outcomes') or []):
                if not item.get('measure'):
                    continue
                outcome = self.store.entity(db, 'Outcome', item['measure'], aliases=[], source=source,
                                            properties={'outcome_kind':'planned_endpoint'})
                self.store.edge(db, 'PLANS_OUTCOME', trial, outcome, source, f'primary_outcomes[{index}]', encoded(item))
            for index, ref in enumerate(data.get('references') or []):
                pmid = str(ref.get('pmid') or '')
                if not re.fullmatch(r'\d+', pmid):
                    continue
                pub = self.store.entity(db, 'Publication', 'PMID ' + pmid, identifiers={'PMID': pmid}, source=source,
                                        properties={'citation': ref.get('citation'), 'metadata_pending': True})
                kind = ref.get('type')
                relation = {'RESULT':'HAS_RESULT_PUBLICATION','DERIVED':'HAS_DERIVED_PUBLICATION'}.get(kind, 'REFERENCES_PUBLICATION')
                self.store.edge(db, relation, trial, pub, source, f'references[{index}]', encoded(ref), qualifiers={'reference_type': kind or 'UNKNOWN'})
                if kind in {'RESULT','DERIVED'}:
                    pmids.append(pmid)
        return drugs, pmids

    def import_publication(self, data):
        pmid = str(data.get('pmid') or '')
        if not re.fullmatch(r'\d+', pmid):
            raise ValueError('无效 PMID')
        title, abstract = data.get('title') or 'PMID ' + pmid, data.get('abstract') or ''
        text = title + '\n' + abstract
        genes = set()
        with self.store.connect() as db:
            source = self.store.source(db, 'PubMed', pmid, 'https://pubmed.ncbi.nlm.nih.gov/' + pmid + '/', data, data.get('date_revised'))
            ids = {'PMID': pmid}
            if data.get('doi'):
                ids['DOI'] = data['doi'].strip().lower()
            pub = self.store.entity(db, 'Publication', title, english=title, identifiers=ids, aliases=['PMID ' + pmid], source=source,
                                    properties={**data, 'metadata_pending': False})
            # Replace stub label after identifier-based resolution; preserve the original alias.
            db.execute('UPDATE kg_entities SET standard_name=?,normalized_name=? WHERE id=?', (title, normalize(title), pub))
            entities = []
            for domain in mentioned_domains(text):
                entity = self.store.entity(db, domain['type'], domain['name'], source=source)
                self.store.edge(db, 'MENTIONS', pub, entity, source, 'title/abstract:' + domain['key'], text,
                                qualifiers={'evidence_type':'text_mention', 'scope':'提及不等于诊断、因果或治疗有效'})
                entities.append((entity, domain['type']))
            for heading in data.get('mesh_headings') or []:
                domain = domain_for(heading.get('name', ''))
                code = heading.get('id', '')
                if domain and re.fullmatch(r'D\d+', code):
                    self.store.entity(db, domain['type'], domain['name'], identifiers={'MeSH': code}, source=source)
            # Conservative candidates: same-sentence co-mentions, always marked inferred.
            for sentence in re.split(r'(?<=[.!?。；;])\s+|\n', text):
                diseases = [d for d in mentioned_domains(sentence) if d['type']=='Disease']
                for gene in GENES:
                    if not re.search(r'\b' + gene + r'\b', sentence, re.I):
                        continue
                    genes.add(gene)
                    gene_id = self.store.entity(db, 'Gene', gene, source=source)
                    self.store.edge(db, 'MENTIONS', pub, gene_id, source, 'abstract:gene:' + gene, sentence, qualifiers={'evidence_type':'text_mention'})
                    for disease in diseases:
                        disease_id = self.store.entity(db, 'Disease', disease['name'], source=source)
                        self.store.edge(db, 'ASSOCIATED_GENE', disease_id, gene_id, source, 'abstract:co-mention:' + gene, sentence,
                                        status='inferred', confidence=.4, qualifiers={'rule':'same_sentence', 'scope':'共现候选，未核验致病或预测意义'})
                    if re.search(r'\b' + gene + r'[-\s]+(?:mutant|mutated|mutation[s]?)\b', sentence, re.I):
                        marker = self.store.entity(db, 'Biomarker', gene + ' mutation (unspecified)', source=source)
                        self.store.edge(db, 'HAS_BIOMARKER', gene_id, marker, source, 'abstract:mutation:' + gene, sentence, status='inferred', confidence=.55,
                                        qualifiers={'scope':'未指定变异位点，未确认临床用途'})
                for event in (d for d in mentioned_domains(sentence) if d['type']=='ClinicalEvent'):
                    event_id = self.store.entity(db, 'ClinicalEvent', event['name'], source=source)
                    for disease in diseases:
                        disease_id = self.store.entity(db, 'Disease', disease['name'], source=source)
                        self.store.edge(db, 'ASSOCIATED_EVENT', disease_id, event_id, source, 'abstract:event:' + event['key'], sentence, status='inferred', confidence=.35,
                                        qualifiers={'scope':'同句提及，不认定因果、进展或发生率'})
            for index, section in enumerate(data.get('abstract_sections') or []):
                if (section.get('label') or '').upper() not in {'RESULTS','RESULT','CONCLUSIONS','CONCLUSION','FINDINGS'} or not section.get('text'):
                    continue
                outcome = self.store.entity(db, 'Outcome', 'PMID ' + pmid + ' · ' + section['label'], source=source,
                                            properties={'outcome_kind':'reported_abstract','text':section['text']})
                self.store.edge(db, 'REPORTS_OUTCOME', pub, outcome, source, f'abstract_sections[{index}]', section['text'],
                                qualifiers={'evidence_type':'abstract_reporting','scope':'原文摘要段落，未人工核验研究质量或全文'})
            ncts = set(re.findall(r'\bNCT\d{8}\b', text, re.I))
            for bank in data.get('databanks') or []:
                if 'clinicaltrial' in normalize(bank.get('name', '')):
                    ncts.update(a.upper() for a in bank.get('accessions', []) if re.fullmatch(r'NCT\d{8}', a, re.I))
            for nct in ncts:
                trial = self.store.entity(db, 'ClinicalTrial', nct.upper(), identifiers={'NCT':nct.upper()}, source=source, properties={'metadata_pending':True})
                self.store.edge(db, 'MENTIONS_TRIAL', pub, trial, source, 'explicit_registration:' + nct, text,
                                qualifiers={'evidence_type':'explicit_identifier', 'scope':'明确提及注册号，不能仅据此判定为结果论文'})
        return genes

    def import_chembl(self, query, data):
        matches = [m for m in data.get('molecules', []) if normalize(m.get('pref_name') or '') == normalize(query)]
        if len(matches) != 1:
            return 0  # Never assign a code from the first fuzzy match.
        item = matches[0]
        accession = item.get('molecule_chembl_id', '')
        if not re.fullmatch(r'CHEMBL\d+', accession):
            return 0
        with self.store.connect() as db:
            source = self.store.source(db, 'ChEMBL', accession, 'https://www.ebi.ac.uk/chembl/explore/compound/' + accession, item)
            self.store.entity(db, 'Drug', query, english=item['pref_name'], identifiers={'ChEMBL':accession}, source=source,
                              properties={'molecule_type':item.get('molecule_type')})
        return 1

    def import_uniprot(self, data, expected_gene):
        if 'entry' in data:
            data = data['entry']
        accession = data.get('primaryAccession')
        symbols = [g.get('geneName', {}).get('value') for g in data.get('genes', [])]
        if not accession or expected_gene not in symbols or data.get('organism', {}).get('taxonId') != 9606:
            return 0
        name = data.get('proteinDescription', {}).get('recommendedName', {}).get('fullName', {}).get('value') or accession
        with self.store.connect() as db:
            source = self.store.source(db, 'UniProt', accession, 'https://www.uniprot.org/uniprotkb/' + accession, data, data.get('entryAudit', {}).get('lastAnnotationUpdateDate'))
            protein = self.store.entity(db, 'Protein', name, identifiers={'UniProt':accession}, source=source)
            identifiers = {}
            gene_refs = [r for r in data.get('uniProtKBCrossReferences', []) if r.get('database')=='GeneID']
            if len(gene_refs)==1:
                identifiers['NCBIGene'] = gene_refs[0]['id']
            gene = self.store.entity(db, 'Gene', expected_gene, identifiers=identifiers, source=source)
            self.store.edge(db, 'ENCODES', gene, protein, source, 'genes', encoded(data.get('genes', [])))
        return 1
