const element=(tag,className,text)=>{const node=document.createElement(tag);if(className)node.className=className;if(text!==undefined)node.textContent=text;return node;};
const commonHelp={
  "assetType": "Classifies the model by intended use. Generation creates a static mesh; rigging, moving parts and game behaviours are separate steps.",
  "image": "Reference image for 3D generation. A complete, readable object isolated from the background works best.",
  "name": "Model name in the library and exports. The name does not change generated appearance.",
  "preset": "Game - Default uses 20 steps, 28,000 triangles and 2K textures. High Detail retains the sampling settings and targets a denser surface.",
  "resolution": "3D generation grid: 512\u00b3, 1024\u00b3 or 1536\u00b3. Higher values require more time and memory. Texture size is independent.",
  "height": "Final model height in metres. Dimensions scale proportionally, with the origin placed at ground level.",
  "textureSize": "Square texture size. Pixels are shared across all surfaces. Use profile default chooses its automatic size.",
  "removeBg": "Removes an opaque background. Images that are already transparent keep their cutout.",
  "triangles": "Target triangles after simplification. Zero uses the profile; otherwise choose 1,000\u2013500,000. The result may be below target.",
  "seed": "Random seed for geometry generation. Keep it fixed to compare settings; small GPU variations may remain.",
  "referencePrompt": "Context stored with the model and included in the image brief. It is not sent as a text prompt to the image-to-3D engine.",
  "terrainName": "Terrain name in the library and exported files.",
  "terrainStyle": "Procedural terrain family: hills, rocks, basin or plateaus.",
  "terrainSize": "Width and length of the square terrain in metres.",
  "terrainHeight": "Maximum requested elevation range in metres.",
  "terrainPalette": "Colour and material family generated for the terrain.",
  "roughness": "Small-scale terrain detail. This is independent of PBR material roughness.",
  "flatZone": "Size of the flattened region in the terrain.",
  "terrainResolution": "Heightmap sample count. A larger heightmap produces more detail and more data.",
  "terrainSeed": "Random seed for terrain. The same seed and settings reproduce the same terrain.",
  "displayName": "Library display name. Renaming does not regenerate the model.",
  "category": "Category used to organise and search the library.",
  "reviewState": "Your review of the result: to review, needs changes, retained or rejected.",
  "tags": "Comma-separated keywords to help find your models.",
  "notes": "Notes and intended changes stored with this model. They do not modify geometry."
};
function attachCommonHelp(){for(const[id,hint]of Object.entries(commonHelp)){const input=document.getElementById(id);if(!input)continue;input.title=hint;const label=input.closest('label');if(label)label.title=hint;}}

export async function createAdvancedControls(container){
  attachCommonHelp();
  let schema,ready=false;
  const inputs=new Map();
  try{
    const [response,helpResponse]=await Promise.all([fetch('/api/generation-options'),fetch('/web/generation-help.en.json?v=2.6')]);
    if(!response.ok)throw Error('Restart the TRELLIS server and reload the page to enable advanced settings.');
    schema=await response.json();
    const help=helpResponse.ok?await helpResponse.json():{};
    for(const field of schema.fields)field.hint=help[field.key]||field.hint;
    for(const group of schema.groups){
      const details=element('details','option-group');details.dataset.group=group.id;
      details.open=group.id==='geometry';
      details.append(element('summary','',group.title),element('p','hint',group.hint));
      const grid=element('div','option-grid');details.append(grid);
      for(const field of schema.fields.filter(f=>f.group===group.id)){
        const label=element('label',field.type==='boolean'?'check option-boolean':'field');
        label.dataset.option=field.key;
        const input=element(field.type==='choice'?'select':'input');input.id='option_'+field.key;
        if(field.type==='choice')for(const choice of field.choices){const option=element('option','',choice.label);option.value=choice.value;input.append(option);}
        else if(field.type==='boolean')input.type='checkbox';
        else{input.type='number';input.min=field.min;input.max=field.max;input.step=field.step;input.required=true;}
        if(field.hint){label.title=field.hint;input.title=field.hint;}
        if(field.type==='boolean'){input.checked=field.default;label.append(input,element('span','',field.label));}
        else{label.append(element('span','',field.label),input);input.value=field.default;}
        inputs.set(field.key,input);grid.append(label);
      }
      container.append(details);
    }
    ready=true;
  }catch(error){container.append(element('p','advanced-error',error.message));}
  function read(){
    if(!ready)throw Error('Advanced settings are not available on this server yet.');
    return Object.fromEntries(schema.fields.map(field=>[field.key,field.type==='boolean'?inputs.get(field.key).checked:field.type==='choice'?inputs.get(field.key).value:Number(inputs.get(field.key).value)]));
  }
  function sync(){
    const remesh=inputs.get('mesh_mode')?.value==='remesh';
    for(const key of ['remesh_project','remesh_band'])if(inputs.has(key))inputs.get(key).closest('label').hidden=!remesh;
    for(const key of ['structure_steps','shape_steps','texture_steps'])if(inputs.has(key))inputs.get(key).placeholder='0 = profil';
  }
  function apply(values={}){
    if(!ready)return;
    for(const field of schema.fields){const value=values[field.key]??field.default,input=inputs.get(field.key);if(field.type==='boolean')input.checked=value;else input.value=String(value);}
    sync();
  }
  inputs.get('mesh_mode')?.addEventListener('change',sync);sync();
  return {get ready(){return ready;},get defaults(){return schema?.default_generation;},read,apply,reset:()=>apply(),
    openGroup:id=>{const group=container.querySelector(`[data-group="${id}"]`);if(group)group.open=true;}};
}
