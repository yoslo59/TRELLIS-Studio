import {icon} from './card-menu.js?v=3.2.0';
const names={closeLighting:'close',resetLighting:'rotate',lightingToggle:'sun',saveProfileAs:'copy',updateProfile:'edit',closeProfile:'close',confirmProfile:'check',copyReferencePrompt:'copy',resetAdvanced:'rotate',generate:'sparkles',cancel:'close',createTerrain:'terrain',shutdown:'power',
 openLibrary:'grid',toggleCollection:'grid',showTrash:'trash',favorite:'star',pagePrev:'chevron-left',pageNext:'chevron-right',confirmDelete:'trash',closeImage:'close',
 selectModels:'select',clearSelection:'close',selectPage:'select',selectMatches:'grid',compareSelection:'compare',classifySelection:'tag',exportSelectionButton:'download',trashSelection:'trash',
 applyBatch:'check',keepBatch:'close',confirmBatchTrash:'trash',closeImport:'close',confirmImport:'import',closePrint:'close',confirmPrint:'print',prepareExport:'download',newExport:'plus',cancelExportTask:'close',
 compareFrame:'fit',comparePlay:'play',rerunDiagnostics:'rotate',animationTabLibrary:'grid',animationTabAdded:'check',cancelRigSetup:'back',resetRigSetup:'rotate',confirmRigSetup:'skeleton',createRig:'skeleton'};
function decorate(button){
 if(button.hasAttribute('data-landmark'))return;
 for(const n of [...button.childNodes])if(n.nodeType===Node.TEXT_NODE)n.textContent=n.textContent.replace(/^[\s]*[＋+↻↺←→▦☆★×✕]\s*/,'');
 if(button.querySelector('svg'))return;
 const text=button.textContent.trim().toLowerCase();let name=names[button.id]||({compare:'compare',diagnostics:'shield',export:'download',details:'details',version:'version',remove:'trash',print:'print'}[button.dataset.action]);
 if(!name)name=button.dataset.angle?'compass':button.matches('[data-close],.dialog-close')?'close':/cancel|close|keep/.test(text)?'close':/restore/.test(text)?'restore':/save|apply|confirm|add/.test(text)?'check':/copy/.test(text)?'copy':/export|download/.test(text)?'download':'details';
 button.prepend(icon(name));button.classList.add('has-icon');
}
export function installButtonIcons(){
 document.querySelectorAll('button').forEach(decorate);
 new MutationObserver(records=>{const buttons=new Set();for(const r of records){if(r.target instanceof Element){const b=r.target.closest('button');if(b)buttons.add(b);}for(const n of r.addedNodes)if(n instanceof Element){if(n.matches('button'))buttons.add(n);n.querySelectorAll('button').forEach(b=>buttons.add(b));}}buttons.forEach(decorate);}).observe(document.body,{childList:true,subtree:true});
}
