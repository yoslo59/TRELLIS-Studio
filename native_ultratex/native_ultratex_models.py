"""Component loaders for the Studio native UltraTex checkpoints."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import importlib.util
import json
import re
import sys

from einops import rearrange
from gguf import GGUFReader, GGMLQuantizationType
import numpy as np
from safetensors import safe_open
import torch
from torch import nn
from torch.nn import functional as F


@dataclass(frozen=True)
class TextureAssets:
    root: Path

    @property
    def backbone(self):
        return self.root / 'diffusion_models/flux-2-klein-base-9b-Q4_0.gguf'

    @property
    def text(self):
        return self.root / 'text_encoders/Qwen3-8B-Q4_K_M.gguf'

    @property
    def vae(self):
        return self.root / 'vae/flux2-vae.safetensors'

    @property
    def decoder(self):
        return self.root / 'ultratex/ultratex_flux2_decoder.pt'

    def adapter(self, branch):
        names = {'albedo': 'ultratex_flux2.safetensors', 'mr': 'ultratex_flux2_mr.safetensors'}
        return self.root / 'loras' / names[branch]


def load_pinned_file(name, path, expected):
    if sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError(f'Source for {name} changed.')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class PackedTensor:
    """Read one unchanged mmap-backed GGUF tensor, cast only at operator use."""

    def __init__(self, tensor, dequant):
        self.tensor = tensor
        self.shape = tuple(map(int, tensor.shape[::-1]))
        self.dequant = dequant

    def read(self, device, dtype):
        packed = torch.from_numpy(np.array(self.tensor.data, copy=True)).to(device)
        kind = self.tensor.tensor_type
        if kind in (GGMLQuantizationType.F32, GGMLQuantizationType.F16):
            return packed.reshape(self.shape).to(dtype)
        return self.dequant(packed, kind, self.shape).to(dtype)

    def embedding(self, ids, dtype):
        flat, indices = torch.unique(ids.cpu().reshape(-1), return_inverse=True)
        selected = torch.from_numpy(np.array(self.tensor.data[flat.numpy()], copy=True)).to(ids.device)
        shape = (len(flat), self.shape[1])
        kind = self.tensor.tensor_type
        if kind in (GGMLQuantizationType.F32, GGMLQuantizationType.F16):
            values = selected.reshape(shape).to(dtype)
        else:
            values = self.dequant(selected, kind, shape).to(dtype)
        return values[indices.to(ids.device)].reshape(*ids.shape, shape[1])


class PackedLinear(nn.Module):
    def __init__(self, weight, bias=None, adapter=None, token_chunk=4096):
        super().__init__()
        self.packed_weight = weight
        self.packed_bias = bias
        self.adapter = adapter
        self.token_chunk = token_chunk
        self.out_features, self.in_features = weight.shape

    def materialize(self, device, dtype):
        weight = self.packed_weight.read(device, dtype)
        if self.adapter is not None:
            up, down, factor = self.adapter
            up, down = up.to(device, torch.float32), down.to(device, torch.float32)
            for start in range(0, self.out_features, 4096):
                end = min(start + 4096, self.out_features)
                delta = torch.mm(up[start:end], down).mul_(factor)
                weight[start:end].add_(delta.to(dtype))
        return weight

    def forward(self, values):
        weight = self.materialize(values.device, values.dtype)
        bias = self.packed_bias.read(values.device, values.dtype) if self.packed_bias is not None else None
        chunk = self.token_chunk
        if values.ndim != 3 or not chunk or values.shape[1] <= chunk:
            return F.linear(values, weight, bias)
        result = values.new_empty((*values.shape[:-1], self.out_features))
        for start in range(0, values.shape[1], chunk):
            result[:, start:start + chunk] = F.linear(values[:, start:start + chunk], weight, bias)
        return result


def load_lora(path):
    config_path = path.with_name('adapter_config.json')
    config = json.loads(config_path.read_text()) if config_path.is_file() else {}
    if config.get('use_dora') or config.get('use_rslora'):
        raise ValueError('Expected ordinary UltraTex PEFT LoRA.')
    adapters = {}
    consumed = set()
    with safe_open(path, framework='pt', device='cpu') as reader:
        for key in reader.keys():
            if not key.endswith('.lora_A.default.weight'):
                continue
            module = key.removeprefix('base_model.model.').removesuffix('.lora_A.default.weight')
            upper = key.replace('.lora_A.', '.lora_B.')
            down, up = reader.get_tensor(key), reader.get_tensor(upper)
            alpha = config.get('alpha_pattern', {}).get(module, config.get('lora_alpha', down.shape[0]))
            adapters[module] = (up, down, float(alpha) / down.shape[0])
            consumed.update((key, upper))
        if set(reader.keys()) != consumed or not adapters:
            raise ValueError('UltraTex LoRA has missing or unsupported tensors.')
    return adapters


def load_klein_gguf(assets, source, gguf_dequant, branch, *, device, dtype, lora_strength=1.0, token_chunk=4096):
    """Map the GGUF and every LoRA to the original Klein-9B module topology."""
    with torch.device('meta'):
        model = source.Flux2(source.Klein9BParams())
    reader = GGUFReader(assets.backbone)
    weights = {tensor.name: PackedTensor(tensor, gguf_dequant) for tensor in reader.tensors}
    expected = model.state_dict()
    if set(expected) != set(weights):
        raise ValueError(f'Klein GGUF schema mismatch: {sorted(set(expected) ^ set(weights))[:12]}')
    for name, tensor in expected.items():
        if tuple(tensor.shape) != weights[name].shape:
            raise ValueError(f'Klein GGUF shape mismatch: {name}')
    adapters = load_lora(assets.adapter(branch))
    for name, module in list(model.named_modules()):
        if not isinstance(module, nn.Linear):
            continue
        weight = weights.pop(name + '.weight')
        bias = weights.pop(name + '.bias') if module.bias is not None else None
        adapter = adapters.pop(name, None)
        if adapter is not None and (adapter[0].shape[0] != weight.shape[0] or adapter[1].shape[1] != weight.shape[1]
                                    or adapter[0].shape[1] != adapter[1].shape[0]):
            raise ValueError(f'UltraTex LoRA shape mismatch: {name}')
        if adapter is not None and lora_strength != 1.0:
            up, down, factor = adapter
            adapter = (up, down, factor * lora_strength) if lora_strength else None
        model.set_submodule(name, PackedLinear(weight, bias, adapter, token_chunk))
    if adapters:
        raise ValueError(f'UltraTex LoRAs target unknown Klein modules: {sorted(adapters)[:12]}')
    model.load_state_dict({key: weight.read(device, dtype) for key, weight in weights.items()}, strict=True, assign=True)
    return model.eval()


QWEN_TOKENIZER_SHA256 = {
    'merges.txt': '8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5',
    'tokenizer_config.json': '49292bd4a58a43382bf01311bc3ed7a151a7f16fe07d155e900ea1af281555db',
    'vocab.json': 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910',
}


def text_prompt_tokens(text, tokenizer_path):
    from transformers import Qwen2Tokenizer

    for name, expected in QWEN_TOKENIZER_SHA256.items():
        if sha256((tokenizer_path / name).read_bytes()).hexdigest() != expected:
            raise ValueError(f'Unexpected Qwen tokenizer file: {name}')
    tokenizer = Qwen2Tokenizer.from_pretrained(str(tokenizer_path), local_files_only=True)
    prompt = f'<|im_start|>user\n{text}<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
    ids = tokenizer(prompt, add_special_tokens=False, truncation=True, max_length=512)['input_ids']
    return torch.tensor([ids + [151643] * max(0, 512 - len(ids))])


def qwen_state_key(name):
    special = {'token_embd.weight': 'embed_tokens.weight', 'output_norm.weight': 'norm.weight',
               'output.weight': 'lm_head.weight'}
    if name in special:
        return special[name]
    _, layer, submodule, suffix = name.split('.')
    mapping = {
        'attn_norm': 'input_layernorm', 'ffn_norm': 'post_attention_layernorm',
        'attn_q': 'self_attn.q_proj', 'attn_k': 'self_attn.k_proj', 'attn_v': 'self_attn.v_proj',
        'attn_output': 'self_attn.o_proj', 'attn_q_norm': 'self_attn.q_norm', 'attn_k_norm': 'self_attn.k_norm',
        'ffn_up': 'mlp.up_proj', 'ffn_down': 'mlp.down_proj', 'ffn_gate': 'mlp.gate_proj',
    }
    return f'layers.{layer}.{mapping[submodule]}.{suffix}'


def load_qwen_text(assets, gguf_dequant, *, device, dtype):
    """Load exact Q4_K_M text weights into current Hugging Face Qwen3 operators."""
    from transformers import Qwen3Config, Qwen3Model

    config = Qwen3Config(vocab_size=151936, hidden_size=4096, intermediate_size=12288,
                         num_hidden_layers=36, num_attention_heads=32, num_key_value_heads=8,
                         max_position_embeddings=40960, rms_norm_eps=1e-6, head_dim=128,
                         rope_parameters={'rope_type': 'default', 'rope_theta': 1e6},
                         pad_token_id=151643, use_cache=False, attn_implementation='sdpa')
    with torch.device('meta'):
        model = Qwen3Model(config)
    reader = GGUFReader(assets.text)
    weights = {qwen_state_key(tensor.name): PackedTensor(tensor, gguf_dequant) for tensor in reader.tensors}
    lm_head = weights.pop('lm_head.weight')
    expected = model.state_dict()
    if set(expected) != set(weights) or lm_head.shape != (151936, 4096):
        raise ValueError(f'Qwen GGUF schema mismatch: {sorted(set(expected) ^ set(weights))[:12]}')
    for name, tensor in expected.items():
        if tuple(tensor.shape) != weights[name].shape:
            raise ValueError(f'Qwen GGUF shape mismatch: {name}')
    embedding = weights.pop('embed_tokens.weight')
    model.embed_tokens = nn.Identity()
    for name, module in list(model.named_modules()):
        if isinstance(module, nn.Linear):
            weight = weights.pop(name + '.weight')
            bias = weights.pop(name + '.bias') if module.bias is not None else None
            model.set_submodule(name, PackedLinear(weight, bias))
    model.rotary_emb = type(model.rotary_emb)(config).to(device)
    model.load_state_dict({name: tensor.read(device, dtype) for name, tensor in weights.items()}, strict=True, assign=True)
    return model.eval(), embedding


@torch.inference_mode()
def encode_qwen_clip(model, embedding, ids, *, device, dtype):
    ids = ids.to(device)
    hidden = model(inputs_embeds=embedding.embedding(ids, dtype), attention_mask=ids != 151643,
                   output_hidden_states=True, use_cache=False).hidden_states
    return torch.cat([hidden[index] for index in (9, 18, 27)], dim=-1)


def vae_key(key):
    """Map the Diffusers Flux2 VAE release to the official AutoEncoder layout."""
    if key.startswith('quant_conv.'):
        return 'encoder.' + key
    if key.startswith('post_quant_conv.'):
        return 'decoder.' + key
    key = key.replace('conv_shortcut', 'nin_shortcut').replace('conv_norm_out', 'norm_out')
    key = re.sub(r'encoder\.down_blocks\.(\d+)\.resnets\.(\d+)\.', r'encoder.down.\1.block.\2.', key)
    key = re.sub(r'encoder\.down_blocks\.(\d+)\.downsamplers\.0\.', r'encoder.down.\1.downsample.', key)
    key = re.sub(r'decoder\.up_blocks\.(\d+)\.resnets\.(\d+)\.',
                 lambda match: f'decoder.up.{3 - int(match[1])}.block.{match[2]}.', key)
    key = re.sub(r'decoder\.up_blocks\.(\d+)\.upsamplers\.0\.',
                 lambda match: f'decoder.up.{3 - int(match[1])}.upsample.', key)
    key = re.sub(r'mid_block\.resnets\.(\d+)\.', lambda match: f'mid.block_{int(match[1]) + 1}.', key)
    key = key.replace('mid_block.attentions.0.', 'mid.attn_1.')
    for diffusers, upstream in (('to_q.', 'q.'), ('to_k.', 'k.'), ('to_v.', 'v.'),
                                ('to_out.0.', 'proj_out.'), ('group_norm.', 'norm.')):
        key = key.replace(diffusers, upstream)
    return key


class FluxTextureVAE(nn.Module):
    latent_channels = 128

    def __init__(self, source):
        super().__init__()
        model = source.AutoEncoder(source.AutoEncoderParams())
        self.encoder = model.encoder
        self.decoder = model.decoder
        self.bn = model.bn

    @torch.inference_mode()
    def encode(self, image):
        pixels = (image.movedim(-1, 1) * 2.0 - 1.0).to(self.encoder.conv_in.weight)
        moments = self.encoder(pixels)
        packed = rearrange(moments[:, :32], 'b c (h x) (w y) -> b (c x y) h w', x=2, y=2)
        return self.bn(packed)

    @torch.inference_mode()
    def raw_decode(self, latent):
        values = latent.to(self.decoder.conv_in.weight)
        var = self.bn.running_var.view(1, -1, 1, 1).to(values)
        mean = self.bn.running_mean.view(1, -1, 1, 1).to(values)
        values = values * (var + self.bn.eps).sqrt() + mean
        unpacked = rearrange(values, 'b (c x y) h w -> b c (h x) (w y)', x=2, y=2)
        return self.decoder(unpacked).float()

    def decode(self, latent):
        return (self.raw_decode(latent).movedim(1, -1) / 2.0 + 0.5).clamp(0, 1)


def load_flux_texture_vae(assets, source, *, device, dtype):
    """Strictly load the base encoder and the released foreground decoder."""
    with torch.device('meta'):
        vae = FluxTextureVAE(source)
    mapping = {}
    with safe_open(assets.vae, framework='pt', device='cpu') as reader:
        for key in reader.keys():
            target = vae_key(key)
            values = reader.get_tensor(key)
            if re.search(r'\.attn_1\.(q|k|v|proj_out)\.weight$', target):
                values = values[..., None, None]
            mapping[target] = values
    decoder = torch.load(assets.decoder, map_location='cpu', weights_only=True, mmap=True)
    decoder = decoder.get('state_dict', decoder)
    patched = {key if key.startswith('decoder.') else 'decoder.' + key: value
               for key, value in decoder.items()}
    expected = {key for key in mapping if key.startswith('decoder.')}
    if set(patched) != expected:
        raise ValueError(f'UltraTex decoder schema mismatch: {sorted(expected ^ set(patched))[:12]}')
    for key, values in patched.items():
        if values.shape != mapping[key].shape:
            raise ValueError(f'UltraTex decoder shape mismatch: {key}')
    mapping.update(patched)
    vae.load_state_dict({key: value.to(device=device, dtype=dtype if value.is_floating_point() else value.dtype)
                         for key, value in mapping.items()}, strict=True, assign=True)
    return vae.eval()
