"""Local domain vocabulary: names only; standard codes must come from source records."""
import re
import unicodedata

ENTITY_TYPES = {
    'Disease': '疾病', 'Drug': '药物', 'Gene': '基因', 'Protein': '蛋白质',
    'ClinicalTrial': '临床试验', 'Publication': '论文', 'Intervention': '治疗干预',
    'Outcome': '研究结局', 'Biomarker': '生物标志物', 'ClinicalEvent': '临床事件',
}
DOMAINS = [
    dict(key='MDS', type='Disease', name='骨髓增生异常综合征', english='Myelodysplastic Syndromes',
         aliases=['MDS', 'Myelodysplastic Syndrome', 'Myelodysplastic Syndromes', 'Myelodysplastic Neoplasms', '骨髓增生异常肿瘤']),
    dict(key='AML', type='Disease', name='急性髓系白血病', english='Acute Myeloid Leukemia',
         aliases=['AML', 'Acute Myeloid Leukemia', 'Acute Myelogenous Leukemia', 'Leukemia, Myeloid, Acute']),
    dict(key='HSCT', type='Intervention', name='造血干细胞移植', english='Hematopoietic Stem Cell Transplantation',
         aliases=['HSCT', 'Hematopoietic Stem Cell Transplantation', 'Hematopoietic Stem Cell Transplant', 'Haematopoietic Stem Cell Transplantation']),
    dict(key='DLI', type='Intervention', name='供者淋巴细胞输注', english='Donor Lymphocyte Infusion',
         aliases=['DLI', 'Donor Lymphocyte Infusion', 'Donor Lymphocyte Infusions']),
    dict(key='GVHD', type='ClinicalEvent', name='移植物抗宿主病', english='Graft-versus-Host Disease',
         aliases=['GVHD', 'Graft-versus-Host Disease', 'Graft versus Host Disease', 'Graft vs Host Disease']),
]

# Confidence describes extraction confidence, never a probability of clinical efficacy.
RELATIONS = {
    'HAS_TRIAL': ('相关试验', {'Disease', 'Intervention', 'ClinicalEvent'}, {'ClinicalTrial'}),
    'STUDIED_DRUG': ('研究用药（非疗效结论）', {'Disease'}, {'Drug'}),
    'STUDIED_INTERVENTION': ('研究干预（非疗效结论）', {'Disease'}, {'Intervention'}),
    'INVESTIGATED_IN': ('作为干预参与试验', {'Drug', 'Intervention'}, {'ClinicalTrial'}),
    'HAS_RESULT_PUBLICATION': ('登记的结果论文', {'ClinicalTrial'}, {'Publication'}),
    'HAS_DERIVED_PUBLICATION': ('登记的衍生论文', {'ClinicalTrial'}, {'Publication'}),
    'REFERENCES_PUBLICATION': ('登记参考文献（非结果确认）', {'ClinicalTrial'}, {'Publication'}),
    'REPORTS_OUTCOME': ('摘要报告结局', {'Publication'}, {'Outcome'}),
    'PLANS_OUTCOME': ('预设研究终点（非结果）', {'ClinicalTrial'}, {'Outcome'}),
    'MENTIONS': ('提及', {'Publication'}, set(ENTITY_TYPES)),
    'MENTIONS_TRIAL': ('论文提及注册号', {'Publication'}, {'ClinicalTrial'}),
    'ASSOCIATED_GENE': ('基因关联候选', {'Disease'}, {'Gene'}),
    'HAS_BIOMARKER': ('标志物候选', {'Gene'}, {'Biomarker'}),
    'ASSOCIATED_EVENT': ('临床事件关联候选', {'Disease'}, {'ClinicalEvent'}),
    'ENCODES': ('编码', {'Gene'}, {'Protein'}),
}

def normalize(value):
    value = unicodedata.normalize('NFKC', str(value)).casefold().strip()
    return re.sub(r'[\s\-‐‑–—_]+', ' ', value)

def domain_for(value):
    value = normalize(value)
    for domain in DOMAINS:
        without_acronym = re.sub(r'\s*\(' + domain['key'].lower() + r'\)$', '', value)
        if without_acronym in {normalize(n) for n in [domain['name'], domain['english'], *domain['aliases']]}:
            return domain
    return None

def mentioned_domains(text):
    for domain in DOMAINS:
        for alias in [domain['name'], *domain['aliases']]:
            if re.search(r'(?<![A-Za-z0-9])' + re.escape(alias) + r'(?![A-Za-z0-9])', text, re.I):
                yield domain
                break
