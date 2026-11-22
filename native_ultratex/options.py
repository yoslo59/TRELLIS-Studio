"""Supported inputs for Studio's native Character 05 texture stages."""


VIEW_FIELDS = frozenset({
    'lora_strength', 'view_resolution', 'view_count', 'seed', 'steps', 'guidance',
    'sparse_retention', 'tiled_decode',
})
SHARP_ATLAS_MODES = frozenset({'sharp_source', 'sharp_coherent', 'sharp_anchor', 'sharp_anchor_v2'})
EDITABLE_TEXTURE_FIELDS = {
    '357': frozenset({'text'}),
    '376': frozenset({'text'}),
    '375': frozenset({'alignment', 'mask_polarity', 'normal_mode', 'normal_view_resolution', 'camera_fit',
                      'image_margin_percent', 'image_padding_pixels', 'reference_zoom'}),
    '358': VIEW_FIELDS | {'extra_angle_mode', 'extra_denoise', 'extra_steps'},
    '364': VIEW_FIELDS,
    '377': frozenset({'texture_size', 'completion', 'upscale_mode', 'device'}),
    '384': frozenset({'alpha_mode', 'alpha_cutoff'}),
    '387': frozenset({'ram_cache_mb', 'disk_budget_gb', 'system_headroom_mb', 'gpu_headroom_mb', 'token_chunk',
                      'activation_factor', 'atlas_tile', 'decode_tile', 'performance_mode', 'max_vram_mb',
                      'adaptive_diagnostics'}),
}


def validate_texture_options(options):
    for identifier in ('358', '364'):
        size = options.get(identifier, {}).get('view_resolution', 1024)
        if size % 16:
            raise ValueError('UltraTex view resolution must be a multiple of 16.')
    bake = options.get('377', {})
    if bake.get('texture_size') == 8192 and bake.get('upscale_mode', 'sharp_anchor_v2') not in SHARP_ATLAS_MODES:
        raise ValueError('An 8K UltraTex atlas requires a Sharp upscale mode.')


def texture_bake_options(config):
    """Translate the published UltraTex bake modes into the independent baker's flags."""
    mode = config['upscale_mode']
    return {
        'device_preference': 'cpu' if config['device'] == 'cpu' else 'cuda',
        'detail': mode in ('fast_detail', 'detail_consistent'),
        'consistent': mode == 'detail_consistent',
        'source_sharp': mode == 'sharp_source',
        'coherent': mode == 'sharp_coherent',
        'anchor': mode in ('sharp_anchor', 'sharp_anchor_v2'),
        'exact_support': mode == 'sharp_anchor_v2',
    }
