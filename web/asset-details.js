const $=id=>document.getElementById(id);
const typeNames=new Map();
export const assetTypeName=value=>typeNames.get(value)||value||'Static prop';
export async function createAssetDetails(){
  const response=await fetch('/api/asset-taxonomy');if(!response.ok)throw Error('Asset classification is unavailable. Restart TRELLIS.');
  const data=await response.json();
  function choice(value,label){const o=document.createElement('option');o.value=value;o.textContent=label;return o;}
  function populate(id,groups,empty){const select=$(id);select.replaceChildren();if(empty)select.append(choice('',empty));
    for(const group of groups){const g=document.createElement('optgroup');g.label=group.label;for(const item of group.options){const value=typeof item==='string'?item:item.value,label=typeof item==='string'?item:item.label;g.append(choice(value,label));}select.append(g);}}
  for(const group of data.asset_types)for(const item of group.options)typeNames.set(item.value,item.label);
  for(const [value,label]of Object.entries(data.system_types))typeNames.set(value,label);
  populate('category',data.categories,'Uncategorised');populate('assetType',data.asset_types);
  populate('detailAssetType',[...data.asset_types,{label:'Source & workflow',options:Object.entries(data.system_types).map(([value,label])=>({value,label}))}]);
  const filter=$('libraryFilter');for(const value of ['object','architecture','imported','rigged'])filter.querySelector(`option[value="${value}"]`)?.remove();
  const filterTypes=document.createElement('optgroup');filterTypes.label='Asset type';for(const [value,label]of typeNames)filterTypes.append(choice('type:'+value,label));filter.append(filterTypes);
  function setValue(id,value){const select=$(id);select.querySelector('[data-legacy]')?.remove();if(![...select.options].some(o=>o.value===value)){const o=choice(value,value);o.dataset.legacy='true';select.append(o);}select.value=value;}
  function hint(){const type=$('assetType').value;
    $('assetHint').textContent=['humanoid','robot'].includes(type)?'Show the full character in a clear A/T pose, with limbs separated. Generation creates a static mesh; add a rig in Animate.':
      ['creature','animal'].includes(type)?'Show the entire body, with legs and appendages separated. Generation creates a static mesh.':
      type==='architecture'?'Non-enterable background building. Show the entire facade, roof and secondary forms.':
      type==='terrain_block'?'An isolated ground patch. Mesh output without a heightmap.':
      'Show one complete subject against a plain neutral background. Asset type is a classification; moving parts and game behaviours require separate setup.';
  }
  $('assetType').addEventListener('change',hint);hint();
  function update(j){const r=j?.result||{},m=j?.metadata||{};
    $('detailTitle').textContent=m.display_name||j?.settings.name||'No model selected';
    $('detailSubtitle').textContent=j?assetTypeName(m.asset_type||j.settings.asset_type):'Choose a model from your collection';
    $('detailThumbnail').src=j?`/api/jobs/${j.id}/files/${j.settings.asset_type==='terrain_unity'?'heightmap.png':'input.png'}?v=${j.result?.revision||j.state}`:'/web/asset-placeholder.svg';
    $('detailThumbnail').onerror=()=>{$('detailThumbnail').onerror=null;$('detailThumbnail').src='/web/asset-placeholder.svg';};
    $('detailTriangles').textContent=r.triangles?Number(r.triangles).toLocaleString('en-US'):'—';
    const adaptive=r.adaptive_finishing,adaptiveNotice=$('adaptiveFinishingReport');
    adaptiveNotice.hidden=!adaptive;
    if(adaptive){
      const sample=adaptive.trials.at(-1);
      const outcome=adaptive.target_met?'tolerance met':'tolerance missed within the triangle ceiling';
      adaptiveNotice.textContent=`Adaptive finishing · ${outcome} · sampled surface error (p99): ${sample.p99_voxels.toFixed(2)} / ${adaptive.tolerance_voxels} source voxels.`;
    }
    const normalBake = r.normal_bake;
    const normalNotice = $('normalBakeReport');
    normalNotice.hidden = normalBake?.state !== 'applied';
    if (!normalNotice.hidden) {
      normalNotice.textContent = `Master detail baked into a ${normalBake.width} × ${normalBake.height} normal map. View in PBR.`;
    }
    $('detailHeight').textContent=r.height_m?Number(r.height_m*100).toLocaleString('en-US',{maximumFractionDigits:1})+' cm':'—';
    $('detailClips').textContent=String(r.animations??r.animation_set?.length??j?.settings.animation_clips?.length??0);
    const report=r.print_report;$('printReportCard').hidden=!report;
    if(report){$('printSurfaceState').textContent=report.closed_surface?'Closed surface verified':'Surface repair needed';
      $('printReportSummary').textContent=`${report.connected_shells} connected shell(s) · voxel ${Number(report.effective_voxel_mm).toFixed(2)} mm · STL in mm`;
      $('printWarnings').replaceChildren(...report.warnings.map(text=>{const li=document.createElement('li');li.textContent=text;return li;}));
      $('printReportDownload').href=`/api/jobs/${j.id}/files/print-report.json`;}
  }
  return {setValue,hint,update};
}
