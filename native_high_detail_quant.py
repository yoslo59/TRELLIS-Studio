"""Transient GGUF weight casts for the standalone TRELLIS.2 Q4 pipeline.

Copyright City96. The packed-tensor and parameter-loading code adapts
City96's GGUF operators at revision 6ea2651e7df66d7585f6ffee804b20e92fb38b8a,
licensed under Apache-2.0. See native_workflows/CITY96-APACHE-2.0.txt.
Studio replaces the original layer integration with ordinary PyTorch
layers and retains only the fixed-weight inference path.
"""

from __future__ import annotations

import gguf
import torch

from native_high_detail_dequant import dequantize_tensor, is_quantized


def get_orig_shape(reader, tensor_name):
    key = f'comfy.gguf.orig_shape.{tensor_name}'
    field = reader.get_field(key)
    if field is None:
        return None
    if tuple(field.types) != (gguf.GGUFValueType.ARRAY, gguf.GGUFValueType.INT32):
        raise TypeError(f'Unexpected shape metadata for {tensor_name}.')
    return torch.Size(int(field.parts[index][0]) for index in field.data)


class GGMLTensor(torch.Tensor):
    """Packed bytes with the logical shape retained for model construction."""

    def __new__(cls, data, *, tensor_type, tensor_shape):
        return super().__new__(cls, data)

    def __init__(self, data, *, tensor_type, tensor_shape):
        self.tensor_type = tensor_type
        self.tensor_shape = tensor_shape

    @property
    def shape(self):
        return getattr(self, 'tensor_shape', self.size())

    def to(self, *args, **kwargs):
        output = super().to(*args, **kwargs)
        output.tensor_type = getattr(self, 'tensor_type', None)
        output.tensor_shape = self.shape
        return output

    def clone(self, *args, **kwargs):
        return self

    def detach(self):
        return self

    def new_empty(self, size, *args, **kwargs):
        return GGMLTensor(super().new_empty(size, *args, **kwargs),
                          tensor_type=self.tensor_type, tensor_shape=size)


class GGMLLayer(torch.nn.Module):
    """Install packed parameters directly and cast them for each operation."""

    dequant_dtype = None

    def is_ggml_quantized(self, *, weight=None, bias=None):
        return is_quantized(self.weight if weight is None else weight) or is_quantized(
            self.bias if bias is None else bias)

    def _load_from_state_dict(self, state_dict, prefix, local_metadata, strict, missing_keys,
                              unexpected_keys, error_msgs):
        weight = state_dict.get(prefix + 'weight')
        bias = state_dict.get(prefix + 'bias')
        if isinstance(self, torch.nn.Linear) or self.is_ggml_quantized(weight=weight, bias=bias):
            if weight is None:
                missing_keys.append(prefix + 'weight')
            else:
                self.weight = torch.nn.Parameter(weight, requires_grad=False)
            if bias is not None:
                self.bias = torch.nn.Parameter(bias, requires_grad=False)
            unexpected_keys.extend(key for key in state_dict
                                   if key not in (prefix + 'weight', prefix + 'bias'))
            return
        super()._load_from_state_dict(state_dict, prefix, local_metadata, strict, missing_keys,
                                      unexpected_keys, error_msgs)

    def get_weight(self, parameter, dtype, device):
        if parameter is None:
            return None
        weight = dequantize_tensor(parameter.to(device), dtype, self.dequant_dtype)
        if isinstance(weight, GGMLTensor):
            weight = weight.as_subclass(torch.Tensor)
        return weight.to(device=device, dtype=dtype)

    def cast_bias_weight(self, input):
        return (self.get_weight(self.weight, input.dtype, input.device),
                self.get_weight(self.bias, input.dtype, input.device))

    def forward_cast_weights(self, input, *args, **kwargs):
        return self.forward_ggml_cast_weights(input, *args, **kwargs)


class GGMLOps:
    """GGUF-compatible dense operators used by the portable model."""

    class Linear(GGMLLayer, torch.nn.Linear):
        def __init__(self, in_features, out_features, bias=True, device=None, dtype=None):
            torch.nn.Module.__init__(self)
            self.in_features = in_features
            self.out_features = out_features
            self.weight = None
            self.bias = None

        def forward(self, input):
            return self.forward_cast_weights(input)

        def forward_ggml_cast_weights(self, input):
            weight, bias = self.cast_bias_weight(input)
            return torch.nn.functional.linear(input, weight, bias)

    class Conv2d(GGMLLayer, torch.nn.Conv2d):
        def reset_parameters(self):
            pass

        def forward(self, input):
            weight, bias = self.cast_bias_weight(input)
            return self._conv_forward(input, weight, bias)
