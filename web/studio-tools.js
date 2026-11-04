import {createComparison} from './compare-studio.js?v=3.1.0';
import {createDiagnostics} from './diagnostics.js?v=3.1.0';
import {createExportStudio} from './export-studio.js?v=3.1.0';
import {createLibrarySelection} from './library-selection.js?v=3.1.0';
import {installButtonIcons} from './button-icons.js?v=3.1.0';
import {icon} from './card-menu.js?v=3.1.0';
export function createStudioTools({api,getRecords,getJob,refresh,printCopy,labelAnimation}){
 const compare=createComparison({getRecords,labelAnimation}),diagnostics=createDiagnostics({api}),exports=createExportStudio({api,printCopy});
 const selection=createLibrarySelection({api,getRecords,refresh,compare,exports});const $=id=>document.getElementById(id);
 $('compareModel').onclick=()=>compare.open(getJob());$('diagnoseModel').onclick=()=>diagnostics.open(getJob());$('exportModel').onclick=()=>exports.open(getJob());$('detailDiagnostics').onclick=()=>diagnostics.open(getJob());
 installButtonIcons();
 return {compare,diagnostics,exports,selection,update(job,records,trashMode){for(const id of ['compareModel','diagnoseModel','exportModel','detailDiagnostics'])$(id).disabled=!job||job.state!=='done';selection.sync(records,trashMode);},
  mediaBadge(job){const m=job.media,available=m?.available??job.state==='done';const animated=m?.animated??!!job.settings.animation_clips?.length;const badge=document.createElement('span');badge.className='card-media '+(animated?'animated':available?'static':'pending');const label=available?(animated?`Animated · ${m?.clips||job.settings.animation_clips?.length||1} clips`:'Static model'):'Model not ready';badge.title=label;badge.setAttribute('aria-label',label);badge.append(icon(animated?'motion':available?'cube':'clock'));const text=document.createElement('span');text.textContent=animated?String(m?.clips||job.settings.animation_clips?.length||1):'';badge.append(text);return badge;}};
}
