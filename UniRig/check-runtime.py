"""Startup probe, no model weights loaded and no persistent GPU allocation."""
from pathlib import Path
import importlib, json, sys, tempfile, time

root=Path(__file__).resolve().parent
result=dict(ready=False,checked_at=time.time(),python=sys.version.split()[0],message='')
try:
    import torch
    for name in ('flash_attn','torch_scatter','torch_cluster','spconv','bpy','open3d'):
        importlib.import_module(name)
    if not torch.cuda.is_available(): raise RuntimeError('CUDA is unavailable under WSL.')
    for filename in ('skeleton.ckpt','skin.ckpt'):
        if not (root/'models'/filename).is_file(): raise RuntimeError('Missing model weights: '+filename)
    from model_config import prepare_skeleton_config
    from transformers import AutoConfig
    import yaml
    with tempfile.TemporaryDirectory(prefix='trellis-rig-probe-') as work:
        model=yaml.safe_load(prepare_skeleton_config(root/'source',work).read_text(encoding='utf-8'))
        config=AutoConfig.from_pretrained(**model['llm'])
        assert config.num_hidden_layers==24 and config.num_attention_heads==16
    result.update(ready=True,gpu=torch.cuda.get_device_name(),torch=torch.__version__,message='UniRig ready · on-demand compute')
except Exception as error: result['message']=str(error)
path=root.parent/'logs'/'components.json';path.parent.mkdir(exist_ok=True)
path.write_text(json.dumps(dict(unirig=result),indent=2,ensure_ascii=False),encoding='utf-8')
print(result['message'],flush=True)
sys.exit(0 if result['ready'] else 1)
