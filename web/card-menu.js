export function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('ui-icon');svg.setAttribute('aria-hidden','true');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href',`/web/icons.svg#${name}`);svg.append(use);return svg;}
export function createCardMenu({version,remove,details,print,compare,diagnostics,export:exportModel,history,folder,lod,game,refinish,finishingBoard}){
  const menu=document.getElementById('assetMenu');let trigger=null,job=null;
  const boardItem=document.createElement('button');
  boardItem.type='button';boardItem.setAttribute('role','menuitem');boardItem.hidden=true;
  const boardLabel=document.createElement('span'),boardHint=document.createElement('small');
  boardLabel.textContent='Finishing board';boardHint.textContent='Queue and compare cached variants';
  boardLabel.append(boardHint);boardItem.append(icon('layers'),boardLabel);
  menu.querySelector('[data-action=refinish]').after(boardItem);
  boardItem.onclick=()=>{const selected=job;close();finishingBoard?.(selected);};
  const close=()=>{if(menu.matches(':popover-open'))menu.hidePopover();trigger?.setAttribute('aria-expanded','false');};
  function placeMenu(){
    if(!menu.matches(':popover-open'))return;
    if(!trigger?.isConnected){close();return;}
    const viewport=window.visualViewport;
    const left=(viewport?.offsetLeft||0)+10,top=(viewport?.offsetTop||0)+10;
    const right=left+(viewport?.width||innerWidth)-20,bottom=top+(viewport?.height||innerHeight)-20;
    const anchor=trigger.getBoundingClientRect();
    if(anchor.bottom<top||anchor.top>bottom){close();return;}
    menu.style.maxWidth=Math.max(0,right-left)+'px';
    menu.style.maxHeight=Math.max(0,bottom-top)+'px';
    const box=menu.getBoundingClientRect();
    const above=anchor.top-box.height-9,below=anchor.bottom+9;
    const preferred=below+box.height<=bottom?below:above;
    menu.style.left=Math.max(left,Math.min(right-box.width,anchor.right-box.width))+'px';
    menu.style.top=Math.max(top,Math.min(bottom-box.height,preferred))+'px';
  }
  menu.addEventListener('toggle',e=>{if(e.newState==='closed')trigger?.setAttribute('aria-expanded','false');});
  for(const [action,handler]of Object.entries({details,print,compare,diagnostics,export:exportModel,history,folder,lod,game,refinish}))menu.querySelector(`[data-action=${action}]`).onclick=()=>{const selected=job;close();handler(selected);};
  menu.querySelector('[data-action=version]').onclick=()=>{const selected=job;close();version(selected);};
  menu.querySelector('[data-action=remove]').onclick=()=>{const selected=job;close();remove(selected);};
  menu.addEventListener('keydown',e=>{if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const items=[...menu.querySelectorAll('button:not([hidden]):not(:disabled)')],i=items.indexOf(document.activeElement),next=e.key==='Home'?0:e.key==='End'?items.length-1:(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;items[next]?.focus();}});
  addEventListener('resize',placeMenu);
  addEventListener('scroll',placeMenu,{passive:true});
  window.visualViewport?.addEventListener('resize',placeMenu);
  window.visualViewport?.addEventListener('scroll',placeMenu,{passive:true});
  document.getElementById('libraryGrid').addEventListener('scroll',close,{passive:true});
  return {close,button(j,{busy,features,activeId}){
    const b=document.createElement('button');b.type='button';b.className='card-more';b.append(icon('more'));b.setAttribute('aria-label','Actions for '+(j.metadata?.display_name||j.settings.name));b.setAttribute('aria-haspopup','menu');b.setAttribute('aria-controls','assetMenu');b.setAttribute('aria-expanded','false');
    b.onclick=e=>{e.stopPropagation();if(trigger===b&&menu.matches(':popover-open')){close();return;}close();trigger=b;job=j;
      menu.querySelector('[data-action=version]').disabled=busy;
      menu.querySelector('[data-action=print]').disabled=busy||j.state!=='done';
      for(const action of ['compare','diagnostics','export','history','lod','game'])menu.querySelector(`[data-action=${action}]`).disabled=j.state!=='done';
      const refinishItem=menu.querySelector('[data-action=refinish]');
      refinishItem.hidden=!j.can_refinish;
      refinishItem.disabled=busy||!j.can_refinish;
      boardItem.hidden=!finishingBoard||!j.can_refinish;
      menu.querySelector('[data-action=remove]').disabled=!features||j.id===activeId;
      menu.showPopover({source:b});b.setAttribute('aria-expanded','true');placeMenu();
      menu.scrollTop=0;menu.querySelector('button:not([hidden]):not(:disabled)')?.focus({preventScroll:true});
    };return b;
  }};
}
export function updateCardStatus(card,j){
  const running=['running','queued'].includes(j.state),percent=Math.min(100,Math.max(0,Math.round(j.progress||0)));
  card.classList.toggle('is-generating',running);card.dataset.state=j.state;
  let progress=card.querySelector('.card-progress');
  if(running){if(!progress){progress=document.createElement('div');progress.className='card-progress';const row=document.createElement('div');row.className='card-progress-row';const stage=document.createElement('span');stage.className='card-stage';const value=document.createElement('strong');value.className='card-percent';row.append(stage,value);const track=document.createElement('div');track.className='card-track';track.setAttribute('role','progressbar');track.setAttribute('aria-label','Generation progress');track.setAttribute('aria-valuemin','0');track.setAttribute('aria-valuemax','100');track.append(document.createElement('i'));progress.append(row,track);card.append(progress);}const stage=progress.querySelector('.card-stage');stage.textContent=j.stage||'Generating';stage.title=stage.textContent;progress.querySelector('.card-percent').textContent=percent+' %';progress.querySelector('.card-track').setAttribute('aria-valuenow',percent);progress.querySelector('i').style.width=percent+'%';}
  else progress?.remove();
}
