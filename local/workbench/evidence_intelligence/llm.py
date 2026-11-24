"""Use the configured workbench account or existing Benchmark LLM API profile."""
import asyncio
import json
import re
from contextlib import suppress
import httpx
from ..medical_core.db import digest
from ..benchmark.retrieval import endpoint, headers
from .extraction import prompt
from .models import VERSION

def parse(raw):
    if not isinstance(raw,str) or len(raw)>160_000: raise ValueError('模型输出为空或超出结构化抽取上限')
    return json.loads(re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip()))

class SemanticExtractor:
    def __init__(self, bridge, config): self.bridge,self.config=bridge,config

    async def generate(self,task,data):
        instructions,schema=prompt(task)
        payload=instructions+'\nJSON schema:\n'+json.dumps(schema.model_json_schema(),ensure_ascii=False)+'\nSOURCE DATA:\n'+json.dumps(data,ensure_ascii=False)
        cfg=self.config
        if cfg['mode']=='account':
            if not cfg.get('model'): raise ValueError('请选择已连接账号中的模型')
            raw,trace=await self.account(payload,cfg['model'])
            generation={'origin':'ai','model_name':cfg['model'],'model_version':'unknown','metadata_status':'incomplete',**trace}
        else:
            model=cfg['llm']
            async with httpx.AsyncClient(timeout=180,trust_env=False) as client:
                response=await client.post(endpoint(model),headers=headers(model),json={'model':model['model_name'],'temperature':0,
                    'messages':[{'role':'system','content':'Return only grounded structured JSON. Do not call tools.'},{'role':'user','content':payload}],
                    'max_tokens':6000})
                response.raise_for_status();result=response.json()
            raw=result['choices'][0]['message']['content']
            generation={'origin':'ai','model_name':model['model_name'],'model_version':model['model_version'],
                        'reported_model':result.get('model'),'system_fingerprint':result.get('system_fingerprint')}
        proposal=schema.model_validate(parse(raw)).model_dump()
        generation.update(prompt_version=VERSION+':'+task,prompt_hash=digest(payload),response_hash=digest(raw),verification_status='unverified')
        return proposal,generation

    async def account(self,text,model):
        bridge=self.bridge
        await bridge.start()
        result=await bridge.rpc('thread/start',{'cwd':str(bridge.runtime/'research'),'sandbox':'read-only','approvalPolicy':'never',
            'model':model,'baseInstructions':'You extract source-grounded JSON only. Never execute tools, shell commands or read files.',
            'developerInstructions':'Use only the supplied research question or paper. Follow the supplied JSON schema. No external lookup.',
            'dynamicTools':[],'serviceName':'medical_evidence_extraction'},timeout=60)
        thread=result['thread']['id'];queue=asyncio.Queue();bridge.queues[thread]=queue;items={};completed=False
        try:
            started=await bridge.rpc('turn/start',{'threadId':thread,'model':model,'input':[{'type':'text','text':text}]},timeout=60)
            bridge.active_turns[thread]=started['turn']['id']
            async with asyncio.timeout(240):
                while True:
                    event=await queue.get();method,params=event['method'],event['params']
                    if method=='item/agentMessage/delta':
                        key=params.get('itemId','answer');items[key]=items.get(key,'')+params.get('delta','')
                    elif method=='item/completed' and params.get('item',{}).get('type')=='agentMessage':
                        item=params['item'];items[item.get('id','answer')]=item.get('text','')
                    elif method=='turn/completed':
                        if params['turn'].get('status')!='completed': raise RuntimeError('模型抽取未完成')
                        completed=True;break
                    elif method=='bridge/closed': raise RuntimeError('模型连接断开')
            return list(items.values())[-1] if items else '',{'thread_id':thread,'turn_id':started['turn']['id']}
        finally:
            if not completed:
                with suppress(Exception): await bridge.interrupt(thread)
            bridge.queues.pop(thread,None);bridge.active_turns.pop(thread,None)
