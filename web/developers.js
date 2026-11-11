const serverMode = document.body.dataset.edition === 'server';
if (serverMode) {
  document.title = 'TRELLIS Server · API and MCP Documentation';
  document.querySelector('main').replaceChildren(document.getElementById('server-developer-guide').content.cloneNode(true));
  document.querySelector('header .version').textContent = 'TRELLIS SERVER';
  document.querySelector('aside > small').textContent = 'DOCUMENTATION';
  document.querySelector('.local').childNodes[1].textContent = 'TRELLIS Server';
  document.querySelector('nav a[href="#mcp"]').lastChild.textContent = 'MCP connection';

  const endpoint = `${location.origin}/api/v1`;
  document.getElementById('serverApiEndpoint').textContent = endpoint;
  document.getElementById('serverApiExample').textContent =
    `Invoke-RestMethod -Uri '${endpoint}/models?limit=1' -Headers @{ Authorization = "Bearer $env:TRELLIS_API_TOKEN" }`;
  document.getElementById('serverMcpExample').textContent = `[mcp_servers.trellis-studio]
command = "C:/TRELLIS/.mcp-venv/Scripts/python.exe"
args = ["C:/TRELLIS/trellis_mcp.py"]
env_vars = ["TRELLIS_API_TOKEN"]

[mcp_servers.trellis-studio.env]
TRELLIS_API_URL = ${JSON.stringify(location.origin)}`;
}

const status=document.getElementById('status');
try{const response=await fetch('/api/v1/status');if(!response.ok)throw Error();const data=await response.json();status.textContent=`Studio ${data.studio_version} · API v${data.api_version}`;if(!serverMode)document.getElementById('mcpEndpoint').textContent=data.mcp_url;}catch{status.textContent=serverMode?'Server unavailable':'Studio unavailable';}
if(!serverMode){try{const response=await fetch('/api/v1/client-config');if(!response.ok)throw Error();document.getElementById('config').textContent=JSON.stringify(await response.json(),null,2);}catch{document.getElementById('config').textContent='See docs/MCP.md for local client configuration.';}}
for(const button of document.querySelectorAll('[data-copy]'))button.onclick=async()=>{try{await navigator.clipboard.writeText(document.getElementById(button.dataset.copy).textContent);const label=button.lastChild;if(label?.nodeType===Node.TEXT_NODE){const old=label.textContent;label.textContent='Copied';setTimeout(()=>label.textContent=old,1400);}}catch{button.title='Copy this example from the code panel.';}};
const links=[...document.querySelectorAll('nav a')],observer=new IntersectionObserver(entries=>{for(const entry of entries)if(entry.isIntersecting)for(const a of links)a.classList.toggle('active',a.hash==='#'+entry.target.id);},{rootMargin:'-100px 0px -55% 0px'});document.querySelectorAll('main>section').forEach(section=>observer.observe(section));
