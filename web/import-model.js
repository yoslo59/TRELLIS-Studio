export function createModelImport({api,startJob}){
 const $=id=>document.getElementById(id);let uploading=false;
 $('openImport').onclick=()=>{$('importNotice').textContent='';$('importDialog').showModal();};
 $('closeImport').onclick=()=>{if(!uploading)$('importDialog').close();};
 $('importFile').onchange=()=>{const file=$('importFile').files[0];if(file)$('importFileName').textContent=file.name;if(file)$('importName').value=file.name.replace(/\.[^.]+$/,'').slice(0,80);};
 $('importForm').onsubmit=async e=>{e.preventDefault();if(uploading)return;const file=$('importFile').files[0];if(!file)return;if(file.size>500*1024*1024){$('importNotice').textContent='The file exceeds 500 MB.';return;}
  uploading=true;$('confirmImport').disabled=true;$('closeImport').disabled=true;$('importNotice').textContent='Uploading to the local studio…';
  try{const result=await api('/api/import',{method:'POST',body:new FormData($('importForm'))});$('importDialog').close();await startJob(result.id);}
  catch(e){$('importNotice').textContent=e.message;}
  finally{uploading=false;$('confirmImport').disabled=false;$('closeImport').disabled=false;}
 };
 $('importDialog').addEventListener('cancel',e=>{if(uploading)e.preventDefault();});
}
