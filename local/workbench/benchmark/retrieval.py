"""Retrievers accept only query text and the frozen public corpus, never qrels."""
from collections import Counter
import importlib.metadata
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import unicodedata

import httpx
import numpy as np

from .store import digest


class Unavailable(RuntimeError):
    """A real model/service is missing: never replace it with simulated scores."""


def tokenize(text):
    text = unicodedata.normalize('NFKC', text).casefold()
    tokens = re.findall(r'[a-z0-9]+(?:[-_][a-z0-9]+)*', text)
    for span in re.findall(r'[\u3400-\u9fff]+', text):
        tokens.extend(span)
        tokens.extend(span[i:i+2] for i in range(len(span)-1))
    return tokens


def public_corpus(documents):
    return [{'document_id': d['document_id'], 'text': d['title'] + '\n' + d['text']} for d in documents]


class BM25:
    def __init__(self, documents, config=None):
        cfg = config or {}
        self.k1, self.b = float(cfg.get('k1', 1.5)), float(cfg.get('b', .75))
        if not 0 < self.k1 <= 5 or not 0 <= self.b <= 1: raise ValueError('BM25 参数超出范围')
        self.documents = documents
        self.counts = [Counter(tokenize(d['text'])) for d in documents]
        self.lengths = [sum(x.values()) for x in self.counts]
        self.average = sum(self.lengths) / max(len(documents), 1) or 1
        self.df = Counter(term for counts in self.counts for term in counts)

    def rank(self, query):
        n, terms = len(self.documents), set(tokenize(query))
        scores = []
        for doc, counts, length in zip(self.documents, self.counts, self.lengths):
            score = 0.0
            for term in terms:
                tf = counts[term]
                if tf:
                    inverse = math.log(1 + (n - self.df[term] + .5) / (self.df[term] + .5))
                    score += inverse * tf * (self.k1 + 1) / (tf + self.k1 * (1 - self.b + self.b * length / self.average))
            if score > 0: scores.append({'document_id': doc['document_id'], 'score': score})
        return sorted(scores, key=lambda x: (-x['score'], x['document_id']))


def headers(config):
    env = config.get('api_key_env')
    if env and not os.getenv(env): raise Unavailable(f'未配置环境变量 {env}')
    scheme = config.get('auth_scheme','Bearer')
    if scheme not in {'Bearer','ApiKey','Basic'}: raise ValueError('auth_scheme 仅支持 Bearer、ApiKey 或 Basic')
    return {'Authorization': scheme + ' ' + os.environ[env]} if env else {}


def endpoint(config):
    if not config.get('api_url'): raise Unavailable('未配置模型 API 地址')
    if not config.get('model_name') or not config.get('model_version'): raise Unavailable('必须配置模型名称和版本')
    return config['api_url']


def local_model(config):
    name = config.get('model_path') or config.get('model_name')
    if not name or not config.get('model_version'): raise Unavailable('未配置本地模型名称、路径及版本')
    revision = config.get('revision')
    if not Path(name).is_dir() and not re.fullmatch(r'[a-fA-F0-9]{40}', revision or ''):
        raise Unavailable('远程仓库模型需要固定40位 commit revision；请先下载或选择本地模型路径')
    return name


def local_fingerprint(config):
    name = config.get('model_path') or config.get('model_name')
    if not name: return {}
    location = Path(name)
    if not location.is_dir(): return {'revision': config.get('revision')}
    location = location.resolve()
    fingerprints = {}
    for file in sorted(location.rglob('*')):
        if not file.is_file() or file.suffix not in {'.json','.txt','.safetensors','.bin','.model'}: continue
        hasher = hashlib.sha256()
        with file.open('rb') as stream:
            for chunk in iter(lambda:stream.read(1024*1024),b''): hasher.update(chunk)
        fingerprints[str(file.relative_to(location))] = hasher.hexdigest()
    expected = config.get('weight_manifest')
    if expected and expected != fingerprints: raise Unavailable('本地权重或分词配置已改变，请核验后保存新的模型版本')
    return {'weight_manifest_hash':digest(fingerprints),'file_count':len(fingerprints)}


def normalize_vectors(values, count, dimension=None):
    matrix = np.asarray(values, dtype=np.float32)
    if matrix.ndim != 2 or matrix.shape[0] != count or not matrix.shape[1] or not np.isfinite(matrix).all():
        raise ValueError('Embedding 返回数量、形状或数值无效')
    if dimension and matrix.shape[1] != int(dimension): raise ValueError('Embedding 维度与配置不一致')
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0): raise ValueError('Embedding 返回零向量，不能计算余弦相似度')
    return matrix / norms


class Dense:
    def __init__(self, documents, config, cache, runtime):
        self.documents, self.config, self.runtime = documents, config, runtime
        self.client = None
        provider = config.get('provider')
        if provider == 'sentence_transformers':
            name = local_model(config)
            runtime['embedding_checkpoint'] = local_fingerprint(config)
            try:
                from tooluniverse.database_setup.embedder import Embedder
                self.client = Embedder('local', name, revision=config.get('revision'),
                    cache_folder=str(cache / 'models'), local_files_only=config.get('local_files_only', True), device=config.get('device', 'cpu')).client
                self.client.max_seq_length = int(config.get('max_seq_length', 256))
            except ImportError as exc: raise Unavailable('缺少本地向量模型依赖：' + str(exc)) from None
            except OSError: raise Unavailable('本地模型权重尚未下载或不可读取') from None
        elif provider != 'custom_api':
            raise Unavailable('Embedding provider 请选择 sentence_transformers 或 custom_api')
        else:
            endpoint(config)
        cache.mkdir(parents=True, exist_ok=True)
        key = digest({'corpus': documents, 'embedding': config, 'checkpoint':runtime.get('embedding_checkpoint'), 'dependencies':environment(), 'engine': 'dense-cosine-v1'})
        location = cache / (key + '.npz')
        if location.exists():
            with np.load(location, allow_pickle=False) as saved:
                self.matrix = normalize_vectors(saved['vectors'], len(documents), config.get('dimension'))
            runtime['embedding_cache_hit'] = True
        else:
            self.matrix = self.encode([d['text'] for d in documents], query=False)
            temp = cache / (key + '.temporary.npz')
            np.savez_compressed(temp, vectors=self.matrix)
            temp.replace(location)
            runtime['embedding_cache_hit'] = False
        runtime['embedding_dimension'] = self.matrix.shape[1]
        runtime['embedding_cache_key'] = key
        runtime['embedding_model'] = {k: config.get(k) for k in ('model_name', 'model_version', 'revision', 'max_seq_length', 'device')}

    def encode(self, texts, query):
        cfg = self.config
        prompt = cfg.get('query_prompt' if query else 'document_prompt', '')
        if self.client is not None:
            method = self.client.encode_query if query and hasattr(self.client, 'encode_query') else self.client.encode_document if not query and hasattr(self.client, 'encode_document') else self.client.encode
            vectors = method(texts, prompt=prompt or None, batch_size=int(cfg.get('batch_size', 16)), show_progress_bar=False, convert_to_numpy=True)
        else:
            vectors = []
            batch_size = min(128, max(1, int(cfg.get('batch_size', 16))))
            with httpx.Client(timeout=120, trust_env=False) as client:
                for start in range(0, len(texts), batch_size):
                    batch = texts[start:start+batch_size]
                    response = client.post(endpoint(cfg), headers=headers(cfg), json={'model': cfg['model_name'], 'input': [prompt + t for t in batch]})
                    response.raise_for_status(); data = response.json()
                    if 'data' in data:
                        items = data['data']
                        if sorted(x.get('index', -1) for x in items) != list(range(len(batch))): raise ValueError('Embedding 响应索引重复或缺失')
                        values = [x['embedding'] for x in sorted(items, key=lambda x: x['index'])]
                    else: values = data.get('embeddings', [])
                    normalize_vectors(values, len(batch), cfg.get('dimension'))
                    vectors.extend(values)
                    self.runtime['embedding_reported_model'] = data.get('model')
        return normalize_vectors(vectors, len(texts), cfg.get('dimension'))

    def rank(self, query):
        vector = self.encode([query], query=True)
        scores = self.matrix @ vector[0]
        return sorted([{'document_id': d['document_id'], 'score': float(s)} for d, s in zip(self.documents, scores)], key=lambda x: (-x['score'], x['document_id']))


class Elasticsearch:
    def __init__(self, documents, config, runtime):
        self.documents, self.config = documents, config
        if not config.get('url'): raise Unavailable('未配置 Elasticsearch URL')
        self.base = config['url'].rstrip('/')
        self.client = httpx.Client(timeout=120, trust_env=False, headers=headers({'auth_scheme':'ApiKey',**config}))
        try:
            root = self.client.get(self.base); root.raise_for_status()
            runtime['elasticsearch_version'] = root.json().get('version', {}).get('number')
            corpus_hash = digest(documents)
            self.index = 'medbench-' + digest({'corpus': corpus_hash, 'analyzer': config.get('analyzer', 'standard')})[:32]
            exists = self.client.head(self.base + '/' + self.index)
            if exists.status_code == 404:
                result = self.client.put(self.base + '/' + self.index, json={'settings': {'number_of_shards': 1, 'number_of_replicas': 0},
                    'mappings': {'_meta': {'corpus_hash': corpus_hash}, 'properties': {'text': {'type': 'text', 'analyzer': config.get('analyzer', 'standard')}, 'document_id': {'type': 'keyword'}}}})
                result.raise_for_status()
                lines = []
                for doc in documents:
                    lines.extend([json.dumps({'create': {'_index': self.index, '_id': doc['document_id']}}), json.dumps(doc, ensure_ascii=False)])
                response = self.client.post(self.base + '/_bulk?refresh=true', content=('\n'.join(lines)+'\n').encode(), headers={'Content-Type':'application/x-ndjson'})
                response.raise_for_status()
                if response.json().get('errors'): raise RuntimeError('Elasticsearch 语料索引写入不完整；未进行评价')
                response = self.client.put(self.base+'/'+self.index+'/_settings', json={'index.blocks.write': True}); response.raise_for_status()
            else: exists.raise_for_status()
            count = self.client.get(self.base+'/'+self.index+'/_count'); count.raise_for_status()
            mapping = self.client.get(self.base+'/'+self.index+'/_mapping'); mapping.raise_for_status()
            if count.json()['count'] != len(documents) or mapping.json()[self.index]['mappings']['_meta']['corpus_hash'] != corpus_hash:
                raise ValueError('Elasticsearch 索引与本次固定语料不一致')
            runtime['elasticsearch_index'] = self.index
        except Exception:
            self.client.close(); raise

    def rank(self, query):
        response = self.client.post(self.base+'/'+self.index+'/_search', json={'size':len(self.documents),
            'query':{'bool':{'must':[{'match':{'text':query}}], 'filter':[{'ids':{'values':[d['document_id'] for d in self.documents]}}]}},
            'sort':[{'_score':'desc'},{'document_id':'asc'}], '_source':False})
        response.raise_for_status()
        return [{'document_id':x['_id'], 'score':float(x['_score'])} for x in response.json()['hits']['hits']]


class Reranker:
    def __init__(self, config):
        self.config, self.model, self.tokenizer = config, None, None
        provider = config.get('provider')
        if provider == 'cross_encoder':
            from sentence_transformers import CrossEncoder
            self.model = CrossEncoder(local_model(config), revision=config.get('revision'), device=config.get('device','cpu'),
                                      local_files_only=config.get('local_files_only',True), trust_remote_code=False, max_length=int(config.get('max_length',512)))
        elif provider == 'qwen3_local':
            import torch
            from transformers import AutoModelForCausalLM, AutoTokenizer
            name = local_model(config)
            options = {'revision':config.get('revision'), 'local_files_only':config.get('local_files_only',True), 'trust_remote_code':False}
            self.tokenizer = AutoTokenizer.from_pretrained(name, padding_side='left', **options)
            self.model = AutoModelForCausalLM.from_pretrained(name, torch_dtype=torch.float32, **options).to(config.get('device','cpu')).eval()
            self.yes = self.tokenizer.convert_tokens_to_ids('yes'); self.no = self.tokenizer.convert_tokens_to_ids('no')
            if self.yes is None or self.no is None or self.yes == self.no: raise ValueError('Reranker 的 yes/no token 无效')
        elif provider == 'custom_api': endpoint(config)
        else: raise Unavailable('未配置真实 Reranker provider')

    def score(self, query, texts):
        cfg = self.config
        if cfg['provider'] == 'custom_api':
            with httpx.Client(timeout=120, trust_env=False) as client:
                response = client.post(endpoint(cfg), headers=headers(cfg), json={'model':cfg['model_name'], 'query':query, 'documents':texts, 'top_n':len(texts)})
                response.raise_for_status(); data = response.json()
            items = data.get('results', data.get('data', []))
            if sorted(x.get('index', -1) for x in items) != list(range(len(texts))): raise ValueError('Reranker 返回索引重复或缺失')
            scores = [x.get('relevance_score', x.get('score')) for x in sorted(items,key=lambda x:x['index'])]
        elif cfg['provider'] == 'cross_encoder':
            values = np.asarray(self.model.predict([(query,t) for t in texts], batch_size=int(cfg.get('batch_size',8)), show_progress_bar=False))
            scores = values[:, int(cfg.get('positive_index',1))] if values.ndim == 2 else values
        else:
            import torch
            prompt = cfg.get('instruction', 'Given a medical research query, judge whether the document is relevant.')
            suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
            scores = []
            # Reserve suffix tokens before truncation so the scoring position is fixed.
            tail = self.tokenizer.encode(suffix, add_special_tokens=False)
            maximum = int(cfg.get('max_length',2048))
            if maximum < len(tail) + 64: raise ValueError('Reranker max_length 过小')
            for text in texts:
                request = '<|im_start|>system\nJudge relevance. Reply with yes or no only.<|im_end|>\n<|im_start|>user\n<Instruct>: '+prompt+'\n<Query>: '+query+'\n<Document>: '+text
                ids = self.tokenizer.encode(request, add_special_tokens=False, truncation=True, max_length=maximum-len(tail)) + tail
                with torch.inference_mode():
                    tokens = torch.tensor([ids], device=self.model.device)
                    logits = self.model(input_ids=tokens).logits[0,-1,[self.no,self.yes]]
                    scores.append(float(torch.softmax(logits.float(),dim=-1)[1]))
        if len(scores) != len(texts) or not all(math.isfinite(float(s)) for s in scores): raise ValueError('Reranker 返回非有限分数或数量不匹配')
        return [float(s) for s in scores]


class Retriever:
    def __init__(self, method, config, corpus, cache):
        if not config.get('enabled', True): raise Unavailable('此配置尚未启用；请配置真实模型或服务后另存版本')
        self.method, self.config, self.corpus = method, config, corpus
        self.runtime = {'embedding_dimension':None, 'engine':'medical-retrieval-v1'}
        self.bm25 = BM25(corpus, config.get('bm25'))
        self.dense = None; self.es = None; self.reranker = None
        effective = config.get('base_method','bm25') if method == 'llm_assisted' else method
        if effective in {'embedding','embedding_reranker','hybrid'}:
            self.dense = Dense(corpus, config.get('embedding',{}), cache, self.runtime)
        if effective == 'elasticsearch': self.es = Elasticsearch(corpus, config.get('elasticsearch',{}), self.runtime)
        if effective == 'embedding_reranker':
            reranker_config=config.get('reranker',{})
            if reranker_config.get('provider') != 'custom_api': self.runtime['reranker_checkpoint']=local_fingerprint(reranker_config)
            try: self.reranker = Reranker(reranker_config)
            except (ImportError,OSError): raise Unavailable('Reranker 依赖或本地权重不可用，请先完成配置') from None
        if method == 'llm_assisted': endpoint(config.get('llm',{}))
        self.effective = effective

    def close(self):
        if self.es: self.es.client.close()

    def expand(self, query):
        cfg = self.config['llm']
        request = {'model':cfg['model_name'], 'temperature':float(cfg.get('temperature',0)), 'max_tokens':600,
                   'messages':[{'role':'system','content':self.config.get('expansion_prompt','Expand a medical query and extract entities. Return JSON with expanded_query and entities; do not answer.')},
                               {'role':'user','content':query}]}
        if cfg.get('seed') is not None: request['seed'] = int(cfg['seed'])
        with httpx.Client(timeout=120, trust_env=False) as client:
            response = client.post(endpoint(cfg), headers=headers(cfg), json=request)
            response.raise_for_status(); data=response.json()
        raw = data['choices'][0]['message']['content']
        raw = re.sub(r'^```(?:json)?\s*|\s*```$', '', raw.strip())
        item = json.loads(raw)
        if not isinstance(item.get('expanded_query'),str) or not 1 <= len(item['expanded_query']) <= 6000:
            raise ValueError('LLM 没有返回有效的 expanded_query')
        return {'expanded_query': query + '\n' + item['expanded_query'], 'entities':item.get('entities',[]),
                'annotation_source':'ai', 'verification_status':'candidate', 'provider_model':data.get('model'),
                'generation':{'origin':'ai','model_name':cfg['model_name'],'model_version':cfg['model_version'],
                              'verification_status':'unverified','prompt_hash':digest(request['messages'][0])},
                'system_fingerprint':data.get('system_fingerprint'), 'prompt_hash':digest(request['messages'][0])}

    def rank(self, query, top_k, replay_expansion=None):
        started = time.perf_counter(); trace = {}
        if self.method == 'llm_assisted':
            trace['expansion'] = replay_expansion or self.expand(query)
            trace['expansion_replayed'] = replay_expansion is not None
            query = trace['expansion']['expanded_query']
        method = self.effective
        if method == 'bm25': ranking = self.bm25.rank(query)
        elif method == 'elasticsearch': ranking = self.es.rank(query)
        elif method in {'embedding','embedding_reranker'}:
            ranking = self.dense.rank(query)
            if method == 'embedding_reranker':
                depth = int(self.config.get('recall_depth',100))
                if depth < top_k or depth > 2000: raise ValueError('recall_depth 必须大于等于 top_k，最多2000')
                ranking = ranking[:depth]
                lookup = {d['document_id']:d['text'] for d in self.corpus}
                scores = self.reranker.score(query,[lookup[x['document_id']] for x in ranking])
                ranking = sorted([{**r, 'retrieval_score':r['score'], 'score':s} for r,s in zip(ranking,scores)], key=lambda x:(-x['score'],x['document_id']))
                trace['reranked_candidate_count'] = len(ranking)
        elif method == 'hybrid':
            cfg = self.config.get('hybrid',{}); weight=float(cfg.get('dense_weight',.5)); k=float(cfg.get('rrf_k',60))
            if not 0 <= weight <= 1 or k <= 0: raise ValueError('RRF 参数无效')
            scores = Counter()
            for rankings,w in [(self.bm25.rank(query),1-weight),(self.dense.rank(query),weight)]:
                for rank,row in enumerate(rankings,1): scores[row['document_id']] += w/(k+rank)
            ranking=sorted([{'document_id':key,'score':float(value)} for key,value in scores.items() if value>0],key=lambda x:(-x['score'],x['document_id']))
        else: raise ValueError('不支持的基础检索方法')
        trace['latency_ms'] = round((time.perf_counter()-started)*1000,3)
        return ranking[:top_k], trace


def environment():
    result = {}
    for package in ('numpy','torch','sentence-transformers','transformers','httpx','faiss-cpu'):
        try: result[package]=importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError: pass
    return result
