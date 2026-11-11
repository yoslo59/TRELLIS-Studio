import {icon} from './card-menu.js?v=3.3.5';
const nameOf=j=>j?.metadata?.display_name||j?.settings.name||'Model';
export function createDiagnostics({api}){
 const dialog=document.createElement('dialog');dialog.id='diagnosticsDialog';dialog.className='studio-dialog diagnostics-dialog';dialog.setAttribute('aria-labelledby','diagnosticsTitle');dialog.innerHTML=`<header class="studio-dialog-head"><div><span class="eyebrow">MODEL HEALTH</span><h2 id="diagnosticsTitle">Model diagnostics</h2><p id="diagnosticModelName"></p></div><button type="button" class="dialog-x" data-close aria-label="Close diagnostics"></button></header><div class="diagnostic-summary" id="diagnosticSummary"></div><div id="diagnosticContent" class="diagnostic-content"></div><footer class="studio-dialog-actions"><span id="diagnosticTime"></span><a id="downloadDiagnostic" class="secondary action-link" hidden download></a><button id="rerunDiagnostics" class="secondary" type="button">Re-run checks</button></footer>`;document.body.append(dialog);const $=id=>dialog.querySelector('#'+id);let job=null,version=0;
 let edgePreview=null;
 const el=(tag,cls,text)=>{const n=document.createElement(tag);n.className=cls||'';if(text!==undefined)n.textContent=text;return n;};
 function section(title){const s=el('section','diagnostic-section');const h=el('h3','',title);s.append(h);$('diagnosticContent').append(s);return s;}
 function metrics(parent,entries){const g=el('dl','diagnostic-metrics');for(const [key,value]of entries)g.append(el('dt','',key),el('dd','',String(value)));parent.append(g);}
 function closePreview(){edgePreview?.dispose();edgePreview=null;}
 function edgeSection(report,own){
  const data=report.edge_overlay;
  if(!data||!Object.values(data.positions).some(positions=>positions.length))return;
  const area=section('Edge locations');
  const view=el('div','prepare-preview diagnostic-edge-view');view.hidden=true;
  view.setAttribute('role','region');view.setAttribute('aria-label','3D mesh with marked edges');
  const toggle=el('button','secondary','Show edge overlay');toggle.type='button';toggle.setAttribute('aria-expanded','false');
  const legend=el('p','diagnostic-edge-legend');
  if(data.positions.boundary.length)legend.append(el('span','boundary','Amber · boundary'));
  if(data.positions.nonmanifold.length)legend.append(el('span','nonmanifold','Pink · nonmanifold'));
  if(data.sampled)legend.append(el('span','', 'Sampled edge locations'));
  const notice=el('p','tool-error');notice.setAttribute('role','status');
  area.append(legend,toggle,view,notice);
  const id=job.id;
  toggle.onclick=async()=>{
   if(edgePreview){
    closePreview();view.hidden=true;toggle.setAttribute('aria-expanded','false');toggle.textContent='Show edge overlay';return;
   }
   toggle.disabled=true;notice.textContent='';view.hidden=false;
   try{
    const {createPreparationPreview}=await import('./preparation-preview.js?v=3.3.5');
    const preview=await createPreparationPreview(view,`/api/jobs/${id}/files/model.glb?v=${report.source_sha256}`);
    if(own!==version){preview.dispose();return;}
    edgePreview=preview;preview.showEdges(data);toggle.setAttribute('aria-expanded','true');toggle.textContent='Hide edge overlay';
   }catch(error){
    if(own===version){view.hidden=true;notice.textContent='Unable to display this model in 3D. '+error.message;}
   }finally{toggle.disabled=false;}
  };
 }
 async function run(force){if(!job)return;const own=++version;closePreview();$('rerunDiagnostics').disabled=true;$('diagnosticContent').replaceChildren(el('p','tool-loading','Checking topology, UVs, materials and dimensions…'));$('diagnosticSummary').textContent='Analysis in progress';$('downloadDiagnostic').hidden=true;
  try{const d=await api(`/api/jobs/${job.id}/diagnostics?force=${force?'true':'false'}`);if(own!==version)return;const s=d.summary;$('diagnosticContent').replaceChildren();$('diagnosticSummary').className='diagnostic-summary '+d.status;$('diagnosticSummary').replaceChildren(icon(d.status==='attention'?'alert':'shield'),el('strong','',d.status==='attention'?'Needs attention':'Measured checks complete'),el('span','',`${d.issues.filter(i=>i.severity!=='info').length} finding(s) · ${s.meshes} mesh(es) · ${s.clips} clips`));
   const geometry=section('Geometry & scale');metrics(geometry,[['Triangles',s.triangles.toLocaleString('en-US')],['Vertices',s.vertices.toLocaleString('en-US')],['Width × height × depth',s.dimensions_m.map(x=>(x*100).toLocaleString('en-US',{maximumFractionDigits:2})+' cm').join(' × ')],['Boundary edges',s.boundary_edges.toLocaleString('en-US')],['Nonmanifold edges',s.nonmanifold_edges.toLocaleString('en-US')],['Degenerate faces',s.degenerate_faces.toLocaleString('en-US')],['Connected shells',s.connected_shells],['Surface',s.closed_surface?'Closed':'Open / needs inspection']]);
   edgeSection(d,own);
   const uv=section('UVs, materials & motion');metrics(uv,[['UV coordinates',s.has_uv?'Present':'Missing or incomplete'],['Collapsed UV faces',s.degenerate_uv_faces.toLocaleString('en-US')],['Materials',s.materials],['Embedded textures',s.textures],['Skeleton joints',s.bones],['Animation clips',s.clips]]);if(d.textures.length){const list=el('details','diagnostic-extra');list.append(el('summary','',`Texture inventory (${d.textures.length})`));for(const t of d.textures)list.append(el('p','texture-detail',`${t.name} · ${t.width} × ${t.height} · ${t.mode}`));uv.append(list);}
   const findings=section('Findings');findings.classList.add('diagnostic-findings');if(!d.issues.length)findings.append(el('p','','No issues were detected by these checks.'));const extra=d.issues.length>8?el('details','diagnostic-extra'):null;if(extra)extra.append(el('summary','',`Show ${d.issues.length-8} more findings`));for(const [index,item] of d.issues.entries()){const row=el('div','diagnostic-finding '+item.severity);row.append(icon(item.severity==='info'?'info':'alert'),el('p','',item.message));(index>=8&&extra?extra:findings).append(row);}if(extra)findings.append(extra);
   const scope=section('What these checks cover');scope.classList.add('diagnostic-scope');scope.append(el('p','',d.scope),el('p','','Not evaluated: '+d.not_checked.join(' · ')+'.'));$('diagnosticTime').textContent='Checked '+new Date(d.created*1000).toLocaleString('en-US');$('downloadDiagnostic').hidden=false;$('downloadDiagnostic').href=`/api/jobs/${job.id}/diagnostics/report`;$('downloadDiagnostic').replaceChildren(icon('download'),document.createTextNode('Report JSON'));
  }catch(error){if(own===version){$('diagnosticSummary').textContent='Analysis unavailable';$('diagnosticContent').replaceChildren(el('p','tool-error',error.message));}}finally{if(own===version)$('rerunDiagnostics').disabled=false;}}
 dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.addEventListener('close',()=>{++version;closePreview();});$('rerunDiagnostics').onclick=()=>run(true);
 return {open(selected){if(!selected||selected.state!=='done')return;job=selected;$('diagnosticModelName').textContent=nameOf(job);if(!dialog.open)dialog.showModal();run(false);}};
}
