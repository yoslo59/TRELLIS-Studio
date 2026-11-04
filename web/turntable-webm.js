// Deterministic, silent WebM turntables. Explicit timestamps keep all frames even
// when the browser throttles background-tab requestAnimationFrame callbacks.
// Container: https://www.webmproject.org/docs/container/
const bytes=n=>{const result=[];do{result.unshift(n%256);n=Math.floor(n/256);}while(n);return new Uint8Array(result);};
const join=parts=>{const result=new Uint8Array(parts.reduce((n,p)=>n+p.length,0));let offset=0;for(const p of parts){result.set(p,offset);offset+=p.length;}return result;};
function vint(n){let length=1;while(n>=2**(length*7)-1)length++;const result=new Uint8Array(length);for(let i=length-1;i>=0;i--){result[i]=n%256;n=Math.floor(n/256);}result[0]|=1<<(8-length);return result;}
const element=(id,data)=>join([bytes(id),vint(data.length),data]);
const uint=(id,n)=>element(id,bytes(n));
const string=(id,s)=>element(id,new TextEncoder().encode(s));
const master=(id,parts)=>element(id,join(parts));
const float=(id,n)=>{const data=new Uint8Array(8);new DataView(data.buffer).setFloat64(0,n);return element(id,data);};
function mux(chunks,width,height,fps,seconds,codec){
 const header=master(0x1a45dfa3,[uint(0x4286,1),uint(0x42f7,1),uint(0x42f2,4),uint(0x42f3,8),string(0x4282,'webm'),uint(0x4287,4),uint(0x4285,2)]);
 const info=master(0x1549a966,[uint(0x2ad7b1,1000000),string(0x4d80,'TRELLIS Studio'),string(0x5741,'TRELLIS Studio'),float(0x4489,seconds*1000)]);
 const tracks=master(0x1654ae6b,[master(0xae,[uint(0xd7,1),uint(0x73c5,1),uint(0x83,1),uint(0x9c,0),uint(0x23e383,Math.round(1e9/fps)),string(0x86,codec==='vp8'?'V_VP8':'V_VP9'),master(0xe0,[uint(0xb0,width),uint(0xba,height)])])]);
 const clusters=[];let blocks=[],base=0;
 const flush=()=>{if(blocks.length)clusters.push(master(0x1f43b675,[uint(0xe7,base),...blocks]));blocks=[];};
 for(const chunk of chunks){const ms=Math.round(chunk.timestamp/1000);if(chunk.key&&ms-base>=1000){flush();base=ms;}const block=new Uint8Array(4+chunk.data.length);block[0]=0x81;new DataView(block.buffer).setInt16(1,ms-base);block[3]=chunk.key?0x80:0;block.set(chunk.data,4);blocks.push(element(0xa3,block));}flush();
 return new Blob([header,master(0x18538067,[info,tracks,...clusters])],{type:'video/webm'});
}
export async function renderTurntable({canvas,width,height,seconds,draw,cancelled,progress}){
 if(!window.VideoEncoder||!window.VideoFrame)throw Error('Turntable export needs WebCodecs. Use a current Chrome or Edge browser.');
 const fps=24,total=seconds*fps;let config=null;
 for(const codec of ['vp09.00.10.08','vp8']){const candidate={codec,width,height,bitrate:6000000,framerate:fps,latencyMode:'realtime',hardwareAcceleration:'prefer-software'};if((await VideoEncoder.isConfigSupported(candidate)).supported){config=candidate;break;}}
 if(!config)throw Error('This browser cannot encode a WebM turntable. Try a current Chrome or Edge browser.');
 const chunks=[];let failure=null;const encoder=new VideoEncoder({output(chunk){const data=new Uint8Array(chunk.byteLength);chunk.copyTo(data);chunks.push({data,timestamp:chunk.timestamp,key:chunk.type==='key'});},error(error){failure=error;}});
 const channel=new MessageChannel(),yieldTask=()=>new Promise(resolve=>{channel.port1.onmessage=resolve;channel.port2.postMessage(0);});
 try{encoder.configure(config);for(let i=0;i<total;i++){if(cancelled())return null;if(failure)throw failure;draw(i/total);const frame=new VideoFrame(canvas,{timestamp:Math.round(i*1e6/fps),duration:Math.round(1e6/fps)});try{encoder.encode(frame,{keyFrame:i%(fps*2)===0});}finally{frame.close();}progress((i+1)/total*100);if(encoder.encodeQueueSize>=6)await encoder.flush();if(i%4===0)await yieldTask();}await encoder.flush();if(failure)throw failure;if(chunks.length!==total)throw Error('Turntable export did not encode every frame.');return cancelled()?null:mux(chunks,width,height,fps,seconds,config.codec);}finally{if(encoder.state!=='closed')encoder.close();channel.port1.close();channel.port2.close();}
}
