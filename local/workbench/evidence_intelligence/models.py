from typing import Literal
from pydantic import BaseModel, ConfigDict, Field

VERSION = 'evidence-abstract-v1'
FIELDS = {
    'population':'研究人群', 'sample_size':'样本量（保留分组口径）',
    'inclusion_criteria':'纳入标准', 'exclusion_criteria':'排除标准',
    'intervention':'干预措施', 'comparison':'对照措施',
    'primary_outcomes':'主要研究终点', 'secondary_outcomes':'次要研究终点',
    'follow_up':'随访时间', 'adverse_events':'不良事件', 'limitations':'研究局限性',
}
DESIGNS = {
    'randomized_controlled_trial':'随机对照试验', 'prospective_cohort':'前瞻性队列',
    'retrospective_cohort':'回顾性队列', 'case_control':'病例对照研究',
    'case_report':'病例报告', 'systematic_review':'系统评价 / Meta 分析',
    'diagnostic_accuracy':'诊断准确性研究', 'nonrandomized_intervention':'非随机干预研究',
    'not_reported':'Not Reported',
}

class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')

class QuotedField(Strict):
    value: str | None = Field(default=None, max_length=6000)
    quote: str | None = Field(default=None, max_length=10000)

class PICOProposal(Strict):
    population: QuotedField = Field(default_factory=QuotedField)
    intervention: QuotedField = Field(default_factory=QuotedField)
    comparison: QuotedField = Field(default_factory=QuotedField)
    outcome: QuotedField = Field(default_factory=QuotedField)

class FindingProposal(Strict):
    quote: str = Field(min_length=1, max_length=10000)
    outcome: str = Field(min_length=1, max_length=250)
    direction: Literal['benefit','no_significant_benefit','harm','mixed','unclear'] = 'unclear'

class ExtractionProposal(Strict):
    design: str = 'not_reported'
    design_quote: str | None = Field(default=None, max_length=6000)
    fields: dict[str, QuotedField] = Field(default_factory=dict)
    findings: list[FindingProposal] = Field(default_factory=list, max_length=30)

class AnalyzeRequest(Strict):
    question: str = Field(min_length=3, max_length=3000)
    search_query: str = Field(default='', max_length=800)
    live_search: bool = True
    limit: int = Field(default=3, ge=1, le=8)
    mode: Literal['rules','account','api'] = 'rules'
    model: str = Field(default='', max_length=160)
    model_profile_id: str | None = None
    release_id: str | None = None

class ReviewRequest(Strict):
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=2000)
    decision: Literal['accepted','rejected','needs_review']
    confirm_human_review: bool = False

class AppraisalRequest(Strict):
    actor: str = Field(min_length=1, max_length=120)
    tool: str
    target: str = Field(min_length=1, max_length=1000)
    judgments: dict = Field(default_factory=dict)
    overall: str | None = None
    rationale: str = Field(default='', max_length=4000)
    confirm_human_review: bool = False

class StatementCitation(Strict):
    extraction_id: str
    quote: str = Field(min_length=1, max_length=10000)
    relationship: Literal['supports','contradicts','context']

class StatementRequest(Strict):
    text: str = Field(min_length=1, max_length=3000)
    citations: list[StatementCitation] = Field(min_length=1, max_length=16)
    actor: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=3000)
    confirm_human_review: bool = False
