"""One isolated UniRig job using the installed, unmodified upstream inference."""
from pathlib import Path
import json, os, subprocess, sys, time
import yaml

root=Path(__file__).resolve().parent
job=Path(sys.argv[1]).resolve();work=job/'rig-work';work.mkdir(exist_ok=True)
started=time.time()
def stage(message,percent):
    path=job/'status.tmp'
    path.write_text(json.dumps(dict(state='running',stage=message,progress=percent,elapsed=round(time.time()-started,1)),ensure_ascii=False),encoding='utf-8')
    path.replace(job/'status.json');print(message,flush=True)
def run(*args):
    subprocess.run([sys.executable,*map(str,args)],cwd=root/'source',check=True)
for name,recipe,checkpoint in [('skeleton','quick_inference_skeleton_articulationxl_ar_256.yaml','skeleton.ckpt'),('skin','quick_inference_unirig_skin.yaml','skin.ckpt')]:
    task=yaml.safe_load((root/'source/configs/task'/recipe).read_text())
    task['resume_from_checkpoint']=str(root/'models'/checkpoint)
    task['experiment_name']='studio_'+job.name+'_'+name
    (work/(name+'.yaml')).write_text(yaml.safe_dump(task))
def extract(source,suffix):
    run('-m','src.data.extract','--config=configs/data/quick_inference.yaml','--input='+str(source),'--require_suffix='+suffix,'--output_dir='+str(work/'intermediate'),'--force_override=true','--num_runs=1','--id=0','--time=studio','--faces_target_count=50000')
source=work/'input.glb' if (work/'input.glb').exists() else job/'source.glb'
stage('Analyse du personnage',8);extract(source,'glb')
stage('UniRig · prédiction du squelette',22)
run('run.py','--task='+str(work/'skeleton.yaml'),'--seed=12345','--input='+str(source),'--output='+str(work/'skeleton.fbx'),'--npz_dir='+str(work/'intermediate'))
run(root/'skeleton-preview.py',job)
stage('Préparation des articulations',45);extract(work/'skeleton.fbx','fbx')
stage('UniRig · calcul des poids',58)
run('run.py','--task='+str(work/'skin.yaml'),'--seed=12345','--input='+str(work/'skeleton.fbx'),'--output='+str(work/'skin.fbx'),'--npz_dir='+str(work/'intermediate'),'--data_name=raw_data.npz')
stage('Transfert du rig sur le modèle texturé',80)
run('-m','src.inference.merge','--require_suffix=glb,fbx','--num_runs=1','--id=0','--source='+str(work/'skin.fbx'),'--target='+str(source),'--output='+str(work/'rigged.glb'))
