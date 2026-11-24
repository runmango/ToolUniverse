"""Source adapters must preserve evidence needed by the graph (offline fixtures)."""
from unittest.mock import MagicMock, patch
import pytest

from tooluniverse.clinicaltrials_tool import CTGovAPITool
from tooluniverse.pubmed_tool import PubMedRESTTool

pytestmark = pytest.mark.unit


def test_trial_keeps_reference_kind_aliases_mesh_and_source_date():
    """Preserve fields that distinguish result reports from background references."""
    payload = {
        'protocolSection': {
            'identificationModule': {'nctId': 'NCT99999991'},
            'statusModule': {'lastUpdatePostDateStruct': {'date': '2026-01-02'}},
            'armsInterventionsModule': {'interventions': [
                {'type': 'DRUG', 'name': 'TEST Drug', 'otherNames': ['TEST Alias']}]},
            'referencesModule': {'references': [
                {'pmid': '99999991', 'type': 'RESULT'},
                {'pmid': '99999992', 'type': 'BACKGROUND'}]},
        },
        'derivedSection': {'conditionBrowseModule': {'meshes': [
            {'id': 'D999999', 'term': 'TEST Condition'}]}},
    }
    response = MagicMock()
    response.json.return_value = payload
    tool = CTGovAPITool({'name': 'ClinicalTrials_get_study', 'fields': {'operation': 'get_study'}})
    with patch('tooluniverse.clinicaltrials_tool.requests.get', return_value=response):
        data = tool.run({'nct_id': 'NCT99999991'})['data']
    assert [ref['type'] for ref in data['references']] == ['RESULT', 'BACKGROUND']
    assert data['interventions'][0]['other_names'] == ['TEST Alias']
    assert data['condition_meshes'][0]['id'] == 'D999999'
    assert data['last_update_posted'] == '2026-01-02'


def test_pubmed_preserves_mesh_nct_outcome_sections_and_revision():
    """Do not discard identifiers, source timestamps, or structured outcome text."""
    response = MagicMock()
    response.url = 'https://example.test/efetch'
    response.text = '''<PubmedArticleSet><PubmedArticle><MedlineCitation>
      <PMID>99999991</PMID><DateRevised><Year>2026</Year><Month>01</Month><Day>02</Day></DateRevised>
      <Article><ArticleTitle>TEST article</ArticleTitle><Abstract>
      <AbstractText Label="RESULTS">TEST <i>formatted</i> result.</AbstractText></Abstract>
      <DataBankList><DataBank><DataBankName>ClinicalTrials.gov</DataBankName>
      <AccessionNumberList><AccessionNumber>NCT99999991</AccessionNumber></AccessionNumberList>
      </DataBank></DataBankList></Article>
      <MeshHeadingList><MeshHeading><DescriptorName UI="D999999">TEST condition</DescriptorName></MeshHeading></MeshHeadingList>
      </MedlineCitation></PubmedArticle></PubmedArticleSet>'''
    tool = PubMedRESTTool({'name': 'PubMed_get_article', 'fields': {}})
    data = tool._parse_efetch_xml(response)['data']
    assert data['mesh_headings'] == [{'id': 'D999999', 'name': 'TEST condition'}]
    assert data['databanks'][0]['accessions'] == ['NCT99999991']
    assert data['date_revised'] == '2026-01-02'
    assert data['abstract_sections'] == [{'label': 'RESULTS', 'text': 'TEST formatted result.'}]
