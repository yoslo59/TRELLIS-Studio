// Named data transfers between the stages used by the native workers.
const game = {
  columns: [
    ['reference','193','368','55','15'], ['312','56','242','316','40','319','117','87'],
    ['199','125','108','3','119','279','126'], ['18','94','23','decode'],
    ['325','327','328'], ['335','337','338'], ['341','342','374'],
    ['375','354','355','356','387'], ['358','364','357','376'], ['377','384','export'],
  ],
  stages: {
    decode: {title: 'Decode surface', type: 'mesh'},
  },
  links: [
    ['reference','312','image'], ['193','312','image'], ['312','56','image'], ['55','56','model'],
    ['56','242','condition'], ['312','3','image'], ['15','3','model'], ['242','3','condition'],
    ['316','40','condition'], ['316','319','condition'], ['40','199','model'], ['319','199','model'],
    ['40','279','model'], ['319','279','model'], ['199','125','model'], ['125','108','model'],
    ['108','3','model'], ['87','3','latent'], ['3','119','latent'], ['117','119','model'],
    ['119','18','latent'], ['279','126','model'], ['126','18','model'], ['126','23','model'],
    ['18','94','latent'], ['117','94','model'], ['94','23','latent'], ['23','decode','latent'],
    ['117','decode','model'], ['decode','325','mesh'], ['325','327','mesh'], ['327','328','mesh'],
    ['328','335','mesh'], ['335','337','mesh'], ['337','338','mesh'], ['328','341','mesh'],
    ['338','341','mesh'], ['341','342','mesh'], ['342','374','mesh'], ['374','375','mesh'],
    ['reference','375','image'], ['368','375','image'], ['387','375','condition'],
    ['356','357','model'], ['356','376','model'], ['354','358','model'], ['354','364','model'],
    ['355','358','model'], ['355','364','model'], ['357','358','condition'], ['357','364','condition'],
    ['376','358','condition'], ['376','364','condition'], ['375','358','image'], ['375','364','image'],
    ['358','377','texture'], ['364','377','texture'], ['377','384','texture'], ['342','384','mesh'],
    ['384','export','mesh'],
  ],
};

const detail = {
  columns: [
    ['reference','12','44'], ['17','pixal','41'], ['11','14','20'], ['42','22','23'],
    ['25','hd-ultratex','export'],
  ],
  stages: {
    pixal: {title: 'PiXal3D shape', type: 'geometry', geometry_backend: 'pixal3d'},
    'hd-ultratex': {title: 'UltraTex multiview', type: 'texture', texture_method: 'ultratex'},
  },
  links: [
    ['reference','12','image'], ['reference','pixal','image'], ['44','pixal','condition'],
    ['12','17','image'], ['44','17','model'], ['17','41','mesh'], ['pixal','41','mesh'],
    ['41','11','mesh'], ['11','14','mesh'], ['14','20','mesh'], ['12','20','image'],
    ['44','20','model'], ['20','42','mesh'], ['42','22','mesh'], ['22','23','mesh'],
    ['23','25','mesh'], ['12','25','image'], ['44','25','model'],
    ['25','export','mesh','trellis'], ['25','hd-ultratex','mesh'],
    ['reference','hd-ultratex','image'], ['hd-ultratex','export','mesh'],
  ],
};

const endpoints = {
  reference: {title: 'Reference image', type: 'reference'},
  export: {title: 'Model GLB', type: 'output'},
};

export function workflowTopology(stages, recipe = stages.some(stage => stage.id === '316') ? game : detail) {
  const nodes = new Map(Object.entries({...endpoints, ...recipe.stages}).map(([id, node]) => [id, {id, ...node}]));
  for (const stage of stages) nodes.set(stage.id, stage);
  const placed = new Set(recipe.columns.flat());
  const columns = recipe.columns.map(column => column.filter(id => nodes.has(id)));
  const remainder = [...nodes.keys()].filter(id => !placed.has(id));
  if (remainder.length) columns.push(remainder);
  return {nodes, columns, links: recipe.links.filter(([from, to]) => nodes.has(from) && nodes.has(to))};
}
