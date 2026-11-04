"""Create UniRig's transformer architecture locally, without OPT model downloads."""
from pathlib import Path

import yaml
from transformers import OPTConfig


def prepare_skeleton_config(source, work):
    """Keep the upstream architecture and checkpoint; replace its online config lookup."""
    recipe = Path(source) / 'configs/model/unirig_ar_350m_1024_81920_float32.yaml'
    model = yaml.safe_load(recipe.read_text(encoding='utf-8'))
    if model['llm']['pretrained_model_name_or_path'] != 'facebook/opt-350m':
        raise RuntimeError('Unsupported UniRig transformer architecture.')

    # UniRig initializes this architecture, then loads its own MIT checkpoint.
    # These are structural dimensions, not pretrained OPT weights or a tokenizer.
    architecture = Path(work).resolve() / 'transformer-architecture'
    OPTConfig(
        hidden_size=1024, num_hidden_layers=24, ffn_dim=4096,
        num_attention_heads=16, word_embed_proj_dim=512,
        do_layer_norm_before=False, activation_dropout=0.0,
    ).save_pretrained(architecture)
    model['llm']['pretrained_model_name_or_path'] = str(architecture)
    model['llm']['local_files_only'] = True
    target = Path(work).resolve() / 'skeleton-model.yaml'
    target.write_text(yaml.safe_dump(model), encoding='utf-8')
    return target
