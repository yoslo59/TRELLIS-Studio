import {icon} from './card-menu.js?v=3.1.0';
export const el=(tag,cls,text)=>{const n=document.createElement(tag);if(cls)n.className=cls;if(text!==undefined)n.textContent=text;return n;};
export function button(text,glyph,cls='secondary',action){const b=el('button',cls);b.type='button';b.append(icon(glyph),document.createTextNode(text));if(action)b.onclick=action;return b;}
export function dialog(id,title,kicker,description,cls=''){
 const d=el('dialog','studio-dialog workbench-dialog '+cls);d.id=id;d.setAttribute('aria-labelledby',id+'Title');const head=el('header','studio-dialog-head'),copy=el('div');copy.append(el('span','eyebrow',kicker));const h=el('h2','',title);h.id=id+'Title';copy.append(h);if(description)copy.append(el('p','',description));head.append(copy,button('','close','dialog-x',()=>d.close()));head.lastChild.setAttribute('aria-label','Close '+title.toLowerCase());const body=el('div','wb-body'),notice=el('p','wb-notice'),foot=el('footer','studio-dialog-actions');notice.setAttribute('role','status');d.append(head,body,notice,foot);document.body.append(d);return {element:d,body,foot,notice,show(){notice.textContent='';if(!d.open)d.showModal();},close(){d.close();}};
}
export function field(label,input,hint){const wrap=el('label','field');wrap.append(el('span','',label),input);if(hint)wrap.append(el('small','',hint));return wrap;}
export function input(type,value,attrs={}){const i=el('input');i.type=type;for(const[k,v]of Object.entries(attrs))i.setAttribute(k,v);i.value=value;return i;}
export function select(choices,value){const s=el('select');for(const[v,label]of choices){const o=el('option','',label);o.value=v;s.append(o);}s.value=value;return s;}
export async function action(ui,control,work){control.disabled=true;ui.notice.textContent='';try{await work();}catch(e){ui.notice.textContent=e.message;}finally{control.disabled=false;}}
export function folderChoices(data,all=false){const result=all?[['*','All folders'],['','Unfiled']]:[['','Unfiled']];const visit=(parent,depth)=>{for(const f of data.folders.filter(f=>f.parent_id===parent)){result.push([f.id,'\u00a0\u00a0'.repeat(depth)+f.name]);visit(f.id,depth+1);}};visit('',0);return result;}
export function download(blob,name){const url=URL.createObjectURL(blob),a=el('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),15000);}
