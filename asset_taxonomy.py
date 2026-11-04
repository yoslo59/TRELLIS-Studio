"""Shared classification choices. Asset types describe use, not automatic behaviours."""
CATEGORY_GROUPS = {
    'Characters & equipment': ['Characters', 'Creatures', 'Animals', 'Robots', 'Weapons', 'Tools', 'Clothing & accessories'],
    'Props & interiors': ['Furniture', 'Storage & containers', 'Appliances', 'Electronics', 'Lighting', 'Food & drink', 'Collectibles', 'Decorations', 'Signs & signage'],
    'Industry & transport': ['Industrial', 'Machinery', 'Pipes & utilities', 'Vehicles', 'Vehicle parts', 'Street furniture'],
    'Architecture': ['Architecture', 'Building modules', 'Interior modules', 'Doors & windows', 'Stairs & railings', 'Roads & bridges'],
    'Environment': ['Nature', 'Plants & trees', 'Rocks & cliffs', 'Terrain', 'Water features', 'Debris & ruins'],
    'Other': ['Miniatures & sculpture', 'Other'],
}
TYPE_GROUPS = {
    'Props': [('object', 'Static prop'), ('interactive_prop', 'Interactive prop'), ('furniture', 'Furniture piece'),
              ('container', 'Container'), ('appliance', 'Appliance'), ('machine', 'Machine'), ('tool', 'Tool'),
              ('weapon', 'Weapon'), ('collectible', 'Collectible / pickup'), ('food', 'Food / drink'), ('wearable', 'Wearable / accessory')],
    'Characters': [('humanoid', 'Humanoid character'), ('creature', 'Creature'), ('animal', 'Animal'), ('robot', 'Robot / mechanical character')],
    'Vehicles': [('vehicle', 'Ground vehicle'), ('aircraft', 'Aircraft'), ('watercraft', 'Watercraft'), ('vehicle_part', 'Vehicle part')],
    'Architecture': [('architecture', 'Background building'), ('building_module', 'Building module'), ('interior_module', 'Interior module'),
                     ('door', 'Door / gate'), ('window', 'Window'), ('stairs', 'Stairs / railing'), ('bridge', 'Bridge / walkway')],
    'Environment': [('terrain_block', 'Terrain mesh patch'), ('rock', 'Rock / cliff'), ('vegetation', 'Plant / tree'),
                    ('debris', 'Debris / ruin'), ('road', 'Road / path segment'), ('water_feature', 'Water feature')],
    'Art & display': [('sculpture', 'Sculpture / miniature'), ('sign', 'Sign / display')],
}
ASSET_TYPES = dict(item for group in TYPE_GROUPS.values() for item in group)
SYSTEM_TYPES = {'imported': 'Imported model', 'rigged': 'Rigged model', 'terrain_unity': 'Procedural terrain', 'print': '3D print copy'}

def schema():
    return dict(categories=[dict(label=label, options=options) for label, options in CATEGORY_GROUPS.items()],
                asset_types=[dict(label=label, options=[dict(value=k,label=v) for k,v in options]) for label,options in TYPE_GROUPS.items()],
                system_types=SYSTEM_TYPES)
