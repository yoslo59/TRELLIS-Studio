"""Validation shared by single and grouped metadata edits."""
from asset_taxonomy import ASSET_TYPES,SYSTEM_TYPES

def validate_metadata(incoming):
    if not isinstance(incoming,dict) or set(incoming)-{'display_name','category','asset_type','tags','notes','reference_prompt','review_state','favorite'}:raise ValueError('Unknown metadata field.')
    values={}
    for key,limit in [('display_name',80),('category',40),('notes',2000),('reference_prompt',6000)]:
        if key in incoming:
            if not isinstance(incoming[key],str) or len(incoming[key])>limit:raise ValueError('Invalid '+key)
            values[key]=incoming[key].strip()
    if 'display_name' in values and (not values['display_name'] or '\n' in values['display_name']):raise ValueError('Invalid model name.')
    if 'asset_type' in incoming:
        if not isinstance(incoming['asset_type'],str) or incoming['asset_type'] not in ASSET_TYPES|SYSTEM_TYPES:raise ValueError('Unknown asset type.')
        values['asset_type']=incoming['asset_type']
    if 'favorite' in incoming:
        if type(incoming['favorite']) is not bool:raise ValueError('Invalid favourite flag.')
        values['favorite']=incoming['favorite']
    if 'review_state' in incoming:
        if incoming['review_state'] not in ('review','retouch','retained','rejected'):raise ValueError('Invalid review state.')
        values['review_state']=incoming['review_state']
    if 'tags' in incoming:
        tags=incoming['tags']
        if not isinstance(tags,list) or len(tags)>12 or any(not isinstance(t,str) or len(t)>40 for t in tags):raise ValueError('Use up to 12 tags of 40 characters each.')
        values['tags']=list(dict.fromkeys(t.strip() for t in tags if t.strip()))
    return values

def validate_ids(values,maximum=100):
    import re
    if not isinstance(values,list) or not 1<=len(values)<=maximum or any(not isinstance(v,str) or not re.fullmatch('[a-f0-9]{32}',v) for v in values) or len(set(values))!=len(values):
        raise ValueError(f'Choose 1–{maximum} distinct models.')
    return values
