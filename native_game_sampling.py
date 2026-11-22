
"""Standalone Character 05 rectified-flow schedules and samplers."""

from dataclasses import dataclass
import math

import torch


@dataclass(frozen=True)
class SamplingStage:
    seed: int
    steps: int
    scheduler: str
    shift: float
    cfg: float
    rescale: float
    cfg_switch: float
    cfg_end: float = 1.0
    cfg_override: float = 1.0
    sampler: str = 'euler'
    denoise: float = 1.0


CHARACTER_STAGES = (
    SamplingStage(56, 12, 'normal', 5.0, 7.5, 0.7, 0.667),
    SamplingStage(42, 20, 'normal', 3.0, 7.5, 0.5, 0.769),
    SamplingStage(42, 12, 'simple', 3.0, 7.5, 0.5, 0.769),
)


def flow_sigma(fraction, shift):
    return shift * fraction / (1 + (shift - 1) * fraction) if shift != 1 else fraction


def _full_schedule(stage, steps):
    # Preserve the multiply-then-divide rounding of the flow time parameter.
    table = flow_sigma((torch.arange(1, 1001) / 1000 * 1000) / 1000, stage.shift)
    name = stage.scheduler
    if name == 'simple':
        stride = len(table) / steps
        sigma = table[-(1 + (torch.arange(steps, dtype=torch.float64) * stride).long())]
    elif name in ('normal', 'sgm_uniform'):
        endpoints = table[[0, -1]] * 1000
        timestep = torch.linspace(endpoints[1], endpoints[0], steps + (name == 'sgm_uniform'))[:steps]
        sigma = flow_sigma(timestep / 1000, stage.shift)
    elif name == 'ddim_uniform':
        sigma = table[1::max(len(table) // steps, 1)].flip(0)
    elif name == 'beta':
        import numpy as np
        from scipy.stats import beta

        quantiles = 1 - np.linspace(0, 1, steps, endpoint=False)
        indexes = np.rint(beta.ppf(quantiles, .6, .6) * (len(table) - 1)).astype(np.int64)
        sigma = table[torch.from_numpy(indexes).unique_consecutive()]
    elif name == 'karras':
        low, high = float(table[0]) ** (1 / 7), float(table[-1]) ** (1 / 7)
        sigma = (high + torch.linspace(0, 1, steps) * (low - high)) ** 7
    elif name == 'exponential':
        sigma = torch.linspace(math.log(float(table[-1])), math.log(float(table[0])), steps).exp()
    elif name == 'linear_quadratic':
        linear_steps, threshold = steps // 2, .025
        if not linear_steps:
            sigma = table[-1:]
        else:
            quadratic_steps = steps - linear_steps
            quadratic = (linear_steps - threshold * steps) / (linear_steps * quadratic_steps ** 2)
            linear = threshold / linear_steps - 2 * quadratic * linear_steps
            constant = quadratic * linear_steps ** 2
            fractions = [index * threshold / linear_steps for index in range(linear_steps)]
            fractions.extend(quadratic * index ** 2 + linear * index + constant
                             for index in range(linear_steps, steps))
            sigma = torch.FloatTensor([1 - value for value in fractions]) * table[-1]
    elif name == 'kl_optimal':
        ramp = torch.arange(steps, dtype=torch.float).div_(max(steps - 1, 1))
        sigma = (ramp * math.atan(float(table[0])) + (1 - ramp) * math.atan(float(table[-1]))).tan_()
    else:
        raise ValueError(f'Unsupported sampler schedule {name}')
    return torch.cat((sigma, sigma.new_zeros(1)))


def schedule(stage):
    if not .01 <= stage.denoise <= 1:
        raise ValueError('Geometry denoise must be between 0.01 and 1.')
    if not math.isfinite(stage.shift) or stage.shift <= 0:
        raise ValueError('Geometry timestep shift must be positive.')
    base_steps = stage.steps if stage.denoise > .9999 else int(stage.steps / stage.denoise)
    extra = stage.sampler == 'dpm_2'
    sigma = _full_schedule(stage, base_steps + extra)
    if extra and sigma.numel() > 2:
        sigma = torch.cat((sigma[:-2], sigma[-1:]))
    return sigma[-(stage.steps + 1):] if stage.denoise <= .9999 else sigma


def effective_cfg(stage, sigma):
    # The graph applies CFG Override before it sets the structure shift to five.
    high = flow_sigma(1 - stage.cfg_switch, 3.0)
    low = flow_sigma(1 - stage.cfg_end, 3.0)
    return stage.cfg_override if low <= float(sigma) <= high else stage.cfg


@torch.no_grad()
def sample_flow(stage, latent, predict, progress=None):
    """Call predict(x, timestep_float, needs_unconditional) for velocity estimates."""
    generator = torch.Generator(device='cpu').manual_seed(stage.seed)
    noise = torch.randn(latent.shape, generator=generator, dtype=torch.float32, device='cpu')
    sigma = schedule(stage).to(latent.device)
    x = sigma[0] * noise.to(device=latent.device, dtype=latent.dtype) + (1 - sigma[0]) * latent
    reduce = tuple(range(1, x.ndim))
    levels = x.new_ones((x.shape[0],))

    def denoise(value, noise_level):
        level = noise_level * levels
        cfg = effective_cfg(stage, level[0])
        conditional, unconditional = predict(value, level * 1000, not math.isclose(cfg, 1))
        conditional = value - conditional * level.reshape(-1, *(1,) * (value.ndim - 1))
        if unconditional is None:
            unconditional = torch.zeros_like(conditional)
        else:
            unconditional = value - unconditional * level.reshape(-1, *(1,) * (value.ndim - 1))
        guided = unconditional + cfg * (conditional - unconditional)
        ratio = conditional.std(dim=reduce, keepdim=True) / guided.std(dim=reduce, keepdim=True).clamp_min(1e-8)
        corrected = stage.rescale * (guided * ratio) + (1.0 - stage.rescale) * guided
        return value - (value - corrected)

    sampler = stage.sampler
    if sampler not in ('euler', 'ddim', 'heun', 'dpm_2', 'dpmpp_2m', 'euler_ancestral', 'ipndm'):
        raise ValueError(f'Unsupported geometry sampler {sampler}.')
    if sampler == 'euler_ancestral':
        seed = (stage.seed + (x.device.type == 'cpu')) % 2**64
        step_noise = torch.Generator(device=x.device).manual_seed(seed)
    old_denoised = old_time = None
    derivatives = []
    total = len(sigma) - 1
    for index in range(total):
        current, following = sigma[index:index + 2]
        denoised = denoise(x, current)
        derivative = (x - denoised) / current
        delta = following - current
        if sampler == 'heun' and following != 0:
            corrected_x = x + derivative * delta
            corrected_d = (corrected_x - denoise(corrected_x, following)) / following
            x = x + (derivative + corrected_d) / 2 * delta
        elif sampler == 'dpm_2' and following != 0:
            middle = current.log().lerp(following.log(), .5).exp()
            midpoint_x = x + derivative * (middle - current)
            x = x + (midpoint_x - denoise(midpoint_x, middle)) / middle * delta
        elif sampler == 'dpmpp_2m':
            if following != current:
                t, next_t = current.log().neg(), following.log().neg()
                elapsed = next_t - t
                blended = denoised
                if old_denoised is not None and following != 0:
                    ratio = (t - old_time) / elapsed
                    weight = 1 / (2 * ratio)
                    blended = (1 + weight) * denoised - weight * old_denoised
                x = (next_t.neg().exp() / t.neg().exp()) * x - (-elapsed).expm1() * blended
                old_denoised, old_time = denoised, t
        elif sampler == 'euler_ancestral':
            if following == 0:
                x = denoised
            elif following != current:
                down = following * (1 + (following / current - 1))
                alpha_ratio = (1 - following) / (1 - down)
                renoise = (following**2 - down**2 * (1 - following)**2 / (1 - down)**2).clamp_min(0)**.5
                fraction = down / current
                x = fraction * x + (1 - fraction) * denoised
                random = torch.randn(x.shape, dtype=x.dtype, device=x.device, generator=step_noise)
                x = alpha_ratio * x + random * renoise
        elif sampler == 'ipndm':
            if following == 0:
                x = denoised
            elif len(derivatives) == 3:
                a, b, c = derivatives
                x = x + delta * (55 * derivative - 59 * c + 37 * b - 9 * a) / 24
            elif len(derivatives) == 2:
                b, c = derivatives
                x = x + delta * (23 * derivative - 16 * c + 5 * b) / 12
            elif derivatives:
                x = x + delta * (3 * derivative - derivatives[-1]) / 2
            else:
                x = x + delta * derivative
            derivatives = [*derivatives[-2:], derivative]
        else:
            x = x + derivative * delta
        if progress is not None:
            progress(index + 1, total)
    return x
