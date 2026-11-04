export function createPrintCopy({api,startJob}){
  const $=id=>document.getElementById(id);let selected=null,submitting=false;
  $('closePrint').onclick=()=>$('printDialog').close();
  $('printForm').onsubmit=async event=>{event.preventDefault();if(!selected||submitting)return;submitting=true;$('confirmPrint').disabled=true;
    try{const result=await api(`/api/jobs/${selected.id}/print-copy`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({height_mm:Number($('printHeight').value),voxel_mm:Number($('printVoxel').value)})});
      $('printDialog').close();await startJob(result.id);
    }catch(error){$('printNotice').textContent=error.message;}
    finally{submitting=false;$('confirmPrint').disabled=false;}
  };
  return {open(job){if(!job||job.state!=='done')return;selected=job;$('printSourceName').textContent=job.metadata?.display_name||job.settings.name;
    $('printNotice').textContent='';$('confirmPrint').disabled=false;$('printDialog').showModal();}};
}
