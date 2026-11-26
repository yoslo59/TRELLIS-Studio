import {createQueueStudio} from './queue-studio.js?v=profile-catalogue';
import {createWorkspaceLibrary} from './workspace-library.js?v=3.4.0';
import {createVersionHistory} from './version-history.js?v=3.4.0';
import {createModelWorkbench} from './model-workbench.js?v=3.4.0';
import {createPresentationStudio} from './presentation-studio.js?v=3.4.0';
import {createCollectionTransfer} from './collection-transfer.js?v=3.4.0';
import {createFinishingBoard} from './finishing-board.js?v=native-options';
import {createTextureProjection,createBakedGameCopy} from './texture-projection.js?v=native-options';
import {createHdMasterFinishing} from './hd-master-finishing.js?v=detail-refinement-native';
import {createFaceRefinement} from './face-refine.js?v=face-refine';
import {button,el} from './workbench-ui.js?v=3.4.0';
export async function createStudioWorkbench(context){
 const {getJob,tools}=context,queue=createQueueStudio(context),workspace=createWorkspaceLibrary({...context,getSelection:()=>tools.selection.ids}),history=createVersionHistory({...context,compare:tools.compare}),prepare=createModelWorkbench({...context,compare:tools.compare}),presentation=createPresentationStudio(context),transfer=createCollectionTransfer({...context,getSelection:()=>tools.selection.ids,workspace,exports:tools.exports});
 const finishingBoard=createFinishingBoard({...context,compare:tools.compare});
 const textureProjection=createTextureProjection(context),bakedGameCopy=createBakedGameCopy(context);
 const hdMasterFinishing=createHdMasterFinishing(context);
 const faceRefinement = createFaceRefinement(context);
 const queueButton=button('Queue','list','rail-button',()=>queue.open());queueButton.id='queueTab';queueButton.append(el('b','',''));queueButton.lastChild.id='queueCount';document.querySelector('.rail-status')?.before(queueButton);if(!queueButton.parentNode)document.getElementById('rigTab').after(queueButton);
 const prepareButton=button('Prepare','sliders','secondary');prepareButton.id='prepareModel';prepareButton.setAttribute('aria-haspopup','menu');document.getElementById('compareModel').before(prepareButton);const menu=el('div','wb-popover');menu.id='prepareMenu';menu.setAttribute('popover','auto');menu.setAttribute('role','menu');document.body.append(menu);for(const[mode,title,glyph,desc]of [['lod','Automatic LODs','layers','Reduce detail for distance'],['game','Game preparation','cube','Pivot, scale and collisions'],['motion','Motion workshop','motion','Loops, root motion and contacts']]){const b=button(title,glyph,'wb-menu-item',()=>{menu.hidePopover();prepare.open(getJob(),mode);});b.append(el('small','',desc));menu.append(b);}prepareButton.onclick = () => {
     menu.showPopover();
     const rect = prepareButton.getBoundingClientRect();
     menu.style.left = Math.max(8, Math.min(innerWidth - menu.offsetWidth - 8, rect.left)) + 'px';
     menu.style.top = Math.max(8, Math.min(innerHeight - menu.offsetHeight - 8, rect.bottom + 8)) + 'px';
 };
 const faceButton = button('Refine face', 'fit', 'wb-menu-item', () => {
     menu.hidePopover();
     faceRefinement.open(getJob());
 });
 faceButton.id = 'prepareRefineFace';
 faceButton.hidden = true;
 faceButton.append(el('small', '', 'Refine a face from its portrait'));
 menu.append(faceButton);
 const finishButton=button('Finishing board','layers','wb-menu-item',()=>{menu.hidePopover();finishingBoard.open(getJob());});const finishHint=el('small','','Compare geometry and texture variants');finishButton.append(finishHint);finishButton.hidden=true;menu.append(finishButton);
 const projectionButton=button('Texture Projection','image','wb-menu-item',()=>{menu.hidePopover();textureProjection.open(getJob());});const projectionHint=el('small','','Align references or use saved views');projectionButton.append(projectionHint);projectionButton.hidden=true;menu.append(projectionButton);
 const masterButton=button('HD Master Finishing','layers','wb-menu-item',()=>{menu.hidePopover();hdMasterFinishing.open(getJob());});
 const masterHint=el('small','','Reconstruct a dense master from the generation cache');
 masterButton.id='prepareHdMaster';masterButton.append(masterHint);masterButton.hidden=true;menu.append(masterButton);
 const bakedButton=button('Game Copy from HD','cube','wb-menu-item',()=>{menu.hidePopover();bakedGameCopy.open(getJob());});bakedButton.append(el('small','','Protect important details and bake a lighter copy'));bakedButton.hidden=true;menu.append(bakedButton);
 const historyButton=button('History','version','secondary',()=>history.open(getJob()));historyButton.id='modelHistory';document.getElementById('diagnoseModel').after(historyButton);const capture=button('','camera','tool-button',()=>presentation.open());capture.id='presentModel';capture.title='Present model';capture.setAttribute('aria-label','Present model');document.getElementById('lightingToggle').after(capture);
 const libraryButton=button('','more','secondary');libraryButton.id='libraryTools';libraryButton.title='Collection tools';libraryButton.setAttribute('aria-label','Collection tools');libraryButton.setAttribute('aria-haspopup','menu');document.querySelector('.library-actions').append(libraryButton);const libraryMenu=el('div','wb-popover');libraryMenu.setAttribute('popover','auto');libraryMenu.setAttribute('role','menu');document.body.append(libraryMenu);for(const[title,glyph,callback]of [['Collections & folders','folder',()=>workspace.open()],['Prepare image batch','image',()=>queue.open(true)],['Back up & transfer','download',()=>transfer.open()]])libraryMenu.append(button(title,glyph,'wb-menu-item',()=>{libraryMenu.hidePopover();callback();}));libraryButton.onclick=()=>{libraryMenu.showPopover();const r=libraryButton.getBoundingClientRect();libraryMenu.style.left=Math.max(10,Math.min(innerWidth-280,r.right-260))+'px';libraryMenu.style.top=r.bottom+8+'px';};
 let queueTick=0, hasMasterFinishing=false;await workspace.init();return {
  queue,workspace,history,prepare,presentation,transfer,finishingBoard,textureProjection,bakedGameCopy,
  setCapabilities(status){
   faceButton.hidden = !status.features?.includes('face_refinement');
   projectionButton.hidden=!status.features?.includes('texture_projection');
   hasMasterFinishing=status.features?.includes('hd_master_finishing')===true;
   masterButton.hidden=!hasMasterFinishing||!getJob()?.can_refinish;
   bakedButton.hidden=!status.features?.includes('baked_game_copy');
  },
  update(job){
   for(const b of[prepareButton,historyButton,capture])b.disabled=!job||job.state!=='done';
   const nativeWorkflow=['game_asset_native','high_detail_native'].includes(job?.settings?.workflow_id);
   finishButton.hidden=!finishingBoard.canOpen(job);
   const finishLabel=nativeWorkflow&&!job.can_refinish?'Retexture this mesh and compare copies':'Compare geometry and texture variants';
   const projectionLabel=nativeWorkflow?'Align and project a reference image':'Align references or use saved views';
   if(finishHint.textContent!==finishLabel)finishHint.textContent=finishLabel;
   if(projectionHint.textContent!==projectionLabel)projectionHint.textContent=projectionLabel;
   masterButton.hidden=!hasMasterFinishing||!job?.can_refinish;
   finishingBoard.update();workspace.update();if(++queueTick%3===0)queue.update();
  },
 };
}
