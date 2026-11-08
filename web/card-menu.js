export function icon(name){const svg=document.createElementNS('http://www.w3.org/2000/svg','svg');svg.classList.add('ui-icon');svg.setAttribute('aria-hidden','true');const use=document.createElementNS(svg.namespaceURI,'use');use.setAttribute('href',`/web/icons.svg#${name}`);svg.append(use);return svg;}
export function createCardMenu({version,remove,details,print,compare,diagnostics,export:exportModel,history,folder,lod,game,refinish}){
  const menu=document.getElementById('assetMenu');let trigger=null,job=null;
  const close=()=>{if(menu.matches(':popover-open'))menu.hidePopover();trigger?.setAttribute('aria-expanded','false');};
  menu.addEventListener('toggle',e=>{if(e.newState==='closed')trigger?.setAttribute('aria-expanded','false');});
  for(const [action,handler]of Object.entries({details,print,compare,diagnostics,export:exportModel,history,folder,lod,game,refinish}))menu.querySelector(`[data-action=${action}]`).onclick=()=>{const selected=job;close();handler(selected);};
  menu.querySelector('[data-action=version]').onclick=()=>{const selected=job;close();version(selected);};
  menu.querySelector('[data-action=remove]').onclick=()=>{const selected=job;close();remove(selected);};
  menu.addEventListener('keydown',e=>{if(['ArrowDown','ArrowUp','Home','End'].includes(e.key)){e.preventDefault();const items=[...menu.querySelectorAll('button:not([hidden]):not(:disabled)')],i=items.indexOf(document.activeElement),next=e.key==='Home'?0:e.key==='End'?items.length-1:(i+(e.key==='ArrowDown'?1:-1)+items.length)%items.length;items[next]?.focus();}});
  addEventListener('resize',close);document.getElementById('libraryGrid').addEventListener('scroll',close,{passive:true});
  return {close,button(j,{busy,features,activeId}){
    const b=document.createElement('button');b.type='button';b.className='card-more';b.append(icon('more'));b.setAttribute('aria-label','Actions for '+(j.metadata?.display_name||j.settings.name));b.setAttribute('aria-haspopup','menu');b.setAttribute('aria-controls','assetMenu');b.setAttribute('aria-expanded','false');
    b.onclick=e=>{e.stopPropagation();if(trigger===b&&menu.matches(':popover-open')){close();return;}close();trigger=b;job=j;
      menu.querySelector('[data-action=version]').disabled=busy;
      menu.querySelector('[data-action=print]').disabled=busy||j.state!=='done';
      for(const action of ['compare','diagnostics','export','history','lod','game'])menu.querySelector(`[data-action=${action}]`).disabled=j.state!=='done';
      const refinishItem=menu.querySelector('[data-action=refinish]');
      refinishItem.hidden=!j.can_refinish;
      refinishItem.disabled=busy||!j.can_refinish;
      menu.querySelector('[data-action=remove]').disabled=!features||j.id===activeId;
      menu.showPopover();b.setAttribute('aria-expanded','true');const r=b.getBoundingClientRect(),box=menu.getBoundingClientRect();menu.style.left=Math.max(10,Math.min(innerWidth-box.width-10,r.right-box.width))+'px';menu.style.top=(r.top-box.height-9>10?r.top-box.height-9:r.bottom+9)+'px';menu.querySelector('button:not([hidden]):not(:disabled)')?.focus();
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
