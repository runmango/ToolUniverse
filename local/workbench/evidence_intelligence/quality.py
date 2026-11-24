"""Versioned manual appraisal records; no LLM score or journal-impact heuristic."""
TOOLS={
    'rob2_2019':{'name':'RoB 2','version':'2019-08-22 / parallel individual RCT','url':'https://www.riskofbias.info/welcome/rob-2-0-tool',
        'designs':['randomized_controlled_trial'],
        'domains':{'randomization':'随机分配过程','deviations':'偏离预期干预','missing':'结局数据缺失','measurement':'结局测量','selection':'结果选择性报告'},
        'choices':['low','some_concerns','high','no_information'], 'overall':['low','some_concerns','high'],
        'note':'按具体结局人工记录。集群或交叉设计需改用对应官方版本。'},
    'robins_i_2025':{'name':'ROBINS-I V2','version':'2025-11-20 draft / follow-up cohort','url':'https://www.riskofbias.info/welcome/robins-i-v2',
        'designs':['prospective_cohort','retrospective_cohort','nonrandomized_intervention'],
        'domains':{'confounding':'混杂','classification':'干预分类','selection':'入选研究','missing':'缺失数据','measurement':'结局测量','reporting':'结果选择性报告'},
        'choices':['low','moderate','serious','critical','no_information'], 'overall':['low','moderate','serious','critical','no_information'],
        'note':'该版本为草案；仅针对非随机干预研究的特定结果，不能用于所有暴露队列。'},
    'amstar2_2017':{'name':'AMSTAR 2','version':'2017','url':'https://pmc.ncbi.nlm.nih.gov/articles/PMC5833365/',
        'designs':['systematic_review'],
        'domains':dict(zip([str(n) for n in range(1,17)],['PICO 问题','预设方法','研究设计选择','重复筛选','重复提取','检索覆盖','排除研究说明','研究描述','偏倚评价','原研究资助','合并方法','偏倚对合并的影响','解释时考虑偏倚','异质性解释','发表偏倚','综述利益冲突'])),
        'choices':['yes','partial_yes','no','not_applicable','no_information'], 'overall':['high','moderate','low','critically_low'],
        'note':'记录人工逐项判断，不把条目相加为总分；总体信心须另写关键薄弱环节依据。'},
    'quadas2_2011':{'name':'QUADAS-2','version':'2011 / historical','url':'https://www.bristol.ac.uk/population-health-sciences/projects/quadas/',
        'designs':['diagnostic_accuracy'],
        'domains':{'selection_bias':'人群选择偏倚','index_bias':'待评价试验偏倚','reference_bias':'参考标准偏倚','flow_bias':'流程与时间偏倚',
                   'selection_applicability':'人群适用性','index_applicability':'待评价试验适用性','reference_applicability':'参考标准适用性'},
        'choices':['low','high','unclear'], 'overall':[],
        'note':'按用户要求保留 QUADAS-2；官方现推荐 QUADAS-3。偏倚与适用性分开，未设置总分。'},
}

def suggestions(extraction):
    return [key for key,t in TOOLS.items() if extraction['design']['type'] in t['designs']]

def validate(extraction, body):
    if not body.get('confirm_human_review') or not body.get('actor','').strip(): raise ValueError('需要人工审核确认与审核人')
    key=body['tool']
    if key not in TOOLS: raise ValueError('未知评价工具版本')
    tool=TOOLS[key]
    if key not in suggestions(extraction): raise ValueError('工具与本次研究设计不匹配，请先核验设计后重新提取')
    judgments=body.get('judgments',{})
    if set(judgments)-set(tool['domains']): raise ValueError('评价域不属于该工具版本')
    for domain,item in judgments.items():
        if not isinstance(item,dict) or item.get('judgment') not in tool['choices'] or not str(item.get('reason','')).strip(): raise ValueError('每个评价域需要合法判断及依据')
    overall=body.get('overall')
    if overall and (overall not in tool['overall'] or set(judgments)!=set(tool['domains']) or not body.get('rationale','').strip()):
        raise ValueError('总体人工判断需要完整评价域、合法类别及明确依据')
    return {**body,'tool_version':tool['version'],'tool_url':tool['url'],
            'status':'human_recorded' if set(judgments)==set(tool['domains']) else 'incomplete',
            'source_scope':'abstract_only','grade_status':'not_assessed',
            'note':'人工判断记录表；不替代官方完整信号问题工作表，不运行自动总分算法。摘要缺失信息应标记无信息。'}
