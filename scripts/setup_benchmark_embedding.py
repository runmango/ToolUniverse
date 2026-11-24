"""Download a public embedding model at an immutable revision into this workspace.

Example: python scripts/setup_benchmark_embedding.py --model sentence-transformers/all-MiniLM-L6-v2
Writes a model configuration, never Gold judgments or benchmark scores.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path

root=Path(__file__).resolve().parents[1]
cache=root/'.tooluniverse/runtime/workbench/benchmark-cache/models'
os.environ['HF_HUB_DISABLE_SYMLINKS_WARNING']='1'
os.environ['HF_HUB_DISABLE_XET']='1'
parser=argparse.ArgumentParser()
parser.add_argument('--model',required=True)
parser.add_argument('--revision')
parser.add_argument('--max-seq-length',type=int,default=256)
args=parser.parse_args()

from huggingface_hub import HfApi, snapshot_download
from tooluniverse.database_setup.embedder import Embedder

revision=HfApi(token=False).model_info(args.model,revision=args.revision).sha
print(json.dumps({'model':args.model,'revision':revision,'cache':str(cache)}),flush=True)
location=snapshot_download(args.model,revision=revision,cache_dir=cache,token=False,
                           allow_patterns=['*.json','*.txt','*.safetensors','1_Pooling/*','2_Normalize/*'],max_workers=4)
embedder=Embedder('local',location,local_files_only=True,device='cpu')
embedder.client.max_seq_length=args.max_seq_length
vectors=embedder.embed(['myelodysplastic syndromes clinical trials','donor lymphocyte infusion after transplantation'])
manifest={}
for file in sorted(Path(location).rglob('*')):
    if file.is_file():
        hasher=hashlib.sha256()
        with file.open('rb') as handle:
            for chunk in iter(lambda:handle.read(1024*1024),b''): hasher.update(chunk)
        manifest[str(file.relative_to(location))]=hasher.hexdigest()
configuration={'name':args.model+' 本地 Embedding','version':revision,'method':'embedding','config':{'enabled':True,'embedding':{
    'provider':'sentence_transformers','model_name':args.model,'model_version':revision,'revision':revision,
    'model_path':str(Path(location)),'local_files_only':True,'device':'cpu','dimension':int(vectors.shape[1]),
    'max_seq_length':args.max_seq_length,'batch_size':16,'weight_manifest':manifest}}}
destination=root/'.tooluniverse/runtime/workbench/benchmark-local-model.json'
destination.write_text(json.dumps(configuration,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'configuration':str(destination),'dimension':vectors.shape[1],'verified_real_inference':True}),flush=True)
