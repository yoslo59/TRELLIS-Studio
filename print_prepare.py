"""Validated, bounded settings for an explicitly separate print derivative."""
import math

def validate_options(data):
    if not isinstance(data,dict) or set(data)-{'height_mm','voxel_mm'}:raise ValueError('Unknown print setting.')
    result={}
    for key,default,low,high in [('height_mm',120,20,500),('voxel_mm',.4,.15,2)]:
        value=data.get(key,default)
        if type(value) not in (int,float) or not math.isfinite(value) or not low<=value<=high:raise ValueError('Invalid '+key)
        result[key]=float(value)
    return result
