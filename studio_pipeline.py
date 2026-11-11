"""Local checkpoint views: load only the flow models used by the chosen pipeline."""
from pathlib import Path
import json

def prepare_pipeline_config(root, job, resolution):
    root=Path(root);job=Path(job)
    if resolution not in (512,1024,1536):raise ValueError('Résolution 3D non prise en charge.')
    kind={512:'512',1024:'1024_cascade',1536:'1536_cascade'}[resolution]
    folder=job/'pipeline-config';folder.mkdir(exist_ok=True)
    source=root/'models'/'trellis2'
    config=json.loads((source/'pipeline.json').read_text(encoding='utf-8'))
    for key,value in list(config['args']['models'].items()):
        if resolution==512 and key.endswith('_1024') or resolution>=1024 and key=='tex_slat_flow_model_512':
            del config['args']['models'][key];continue
        prefix=source/value
        for suffix in ('.json','.safetensors'):
            if not Path(str(prefix)+suffix).is_file():raise FileNotFoundError('Poids manquants : '+str(prefix)+suffix)
        # The loader accepts a standalone local prefix as its fallback. Absolute
        # prefixes work with workspace data on another drive and deep Windows paths.
        config['args']['models'][key]=str(prefix.resolve())
    config['args']['default_pipeline_type']=kind
    (folder/'pipeline.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    return folder,kind
