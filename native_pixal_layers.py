"""Tensor operations required by the standalone shape models."""

from types import SimpleNamespace
import json

import torch
from torch import nn
from torch.nn import functional as F
from safetensors import safe_open


class AttentionTensorContainer:
    def __init__(self, tensor):
        self.tensor = tensor

    def take(self):
        tensor, self.tensor = self.tensor, None
        return tensor


class AttentionMetadata(nn.Module):
    """The pinned shape checkpoints use PyTorch attention."""


def optimized_attention(query, key, value, heads, *, skip_reshape, skip_output_reshape, mask=None, **kwargs):
    assert skip_reshape and skip_output_reshape
    return F.scaled_dot_product_attention(query.take(), key.take(), value.take(), attn_mask=mask)


def free_memory(device):
    device = torch.device(device)
    if device.type == 'cuda':
        free, _ = torch.cuda.mem_get_info(device)
        return free + torch.cuda.memory_reserved(device) - torch.cuda.memory_allocated(device)
    import psutil
    return psutil.virtual_memory().available


def cast_to(tensor, dtype, device):
    return tensor.to(device=device, dtype=dtype)


def cast_to_input(tensor, source):
    return cast_to(tensor, source.dtype, source.device)


class EmptyLinear(nn.Linear):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **(kwargs | {'device': 'meta'}))


class PackedLinear(nn.Module):
    def __init__(self, weight, scale, bias, config):
        super().__init__()
        if config.get('format') != 'int8_tensorwise' or not config.get('convrot'):
            raise ValueError('Unexpected quantized shape-model operator.')
        group_size = config.get('convrot_groupsize')
        if weight.dtype != torch.int8 or weight.ndim != 2 or scale.shape != (weight.shape[0], 1):
            raise ValueError('Unexpected packed ConvRot weight shape.')
        if type(group_size) is not int or group_size < 1 or weight.shape[1] % group_size:
            raise ValueError('Unexpected ConvRot group size.')
        self.register_buffer('weight', weight)
        self.register_buffer('scale', scale)
        self.register_buffer('bias', bias)
        self.group_size = group_size

    def forward(self, tensor):
        if not isinstance(tensor, torch.Tensor):
            return tensor.replace(self.forward(tensor.feats))
        from comfy_kitchen import int8_linear
        bias = None if self.bias is None else self.bias.to(tensor.dtype)
        return int8_linear(tensor, self.weight, self.scale, bias, tensor.dtype,
                           convrot=True, convrot_groupsize=self.group_size)


disable_weight_init = SimpleNamespace(Linear=nn.Linear, Conv3d=nn.Conv3d, LayerNorm=nn.LayerNorm)
flow_operations = SimpleNamespace(Linear=EmptyLinear, LayerNorm=nn.LayerNorm)


def load_flow_weights(model, checkpoint, prefix, *, device, dtype):
    """Load exactly one stage without allocating dense copies of packed weights."""
    with safe_open(checkpoint, framework='pt', device='cpu') as weights:
        keys = {key.removeprefix(prefix) for key in weights.keys() if key.startswith(prefix)}
        remaining = set(keys)
        for name, module in list(model.named_modules()):
            qkey = name + '.comfy_quant'
            if not isinstance(module, EmptyLinear) or qkey not in keys:
                continue
            packed = name + '.weight'
            scale = name + '.weight_scale'
            bias = name + '.bias'
            config = json.loads(weights.get_tensor(prefix + qkey).numpy().tobytes())
            layer = PackedLinear(
                weights.get_tensor(prefix + packed).to(device),
                weights.get_tensor(prefix + scale).to(device),
                weights.get_tensor(prefix + bias).to(device, dtype=dtype) if bias in keys else None,
                config,
            )
            parent, _, attribute = name.rpartition('.')
            setattr(model.get_submodule(parent), attribute, layer)
            remaining.difference_update((qkey, packed, scale, bias))

        # Packed layers were installed above. Their buffers are already final.
        state = {key: value for key, value in model.state_dict().items() if key not in remaining}
        state.update({key: weights.get_tensor(prefix + key).to(device, dtype=dtype) for key in remaining})
    missing_reference = [key for key, value in state.items() if value.is_meta]
    if missing_reference:
        raise ValueError(f'Missing shape model weights: {missing_reference[:3]}')
    model.load_state_dict(state, strict=True, assign=True)

    # Move only the nonpersistent tables that were made on the CPU.
    return model.to(device=device).eval()
