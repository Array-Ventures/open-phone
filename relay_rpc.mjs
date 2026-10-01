// File-authenticated Python relay client. Never starts a device driver.
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';
import {fileURLToPath} from 'node:url';

export function createRelayRPC(env=process.env){
  let child=null,counter=0,closed=false;
  const pending=new Map();
  function fail(message){for(const p of pending.values()){clearTimeout(p.timer);p.reject(new Error(message));}pending.clear();}
  function start(){
    if(closed)throw new Error('Relay connection closed');
    if(child)return;
    child=spawn(env.OPEN_PHONE_PYTHON||'python3',[fileURLToPath(new URL('./relay_stdio.py',import.meta.url))],{env,stdio:['pipe','pipe','inherit']});
    createInterface({input:child.stdout}).on('line',line=>{
      let value;try{value=JSON.parse(line)}catch{return}
      const p=pending.get(value.id);if(!p)return;
      clearTimeout(p.timer);pending.delete(value.id);
      value.ok?p.resolve(value.result):p.reject(new Error(value.error));
    });
    child.on('exit',()=>{closed=true;fail('Relay adapter exited; no request was replayed');});
    child.on('error',error=>{closed=true;fail('Relay adapter could not start: '+error.message);});
    child.stdin.on('error',()=>{closed=true;fail('Relay adapter input closed; no request was replayed');});
  }
  function request(path,data,method){
    try{start()}catch(error){return Promise.reject(error)}
    if(pending.size>=32)return Promise.reject(new Error('Relay request capacity reached'));
    return new Promise((resolve,reject)=>{
      const id=++counter;
      const timer=setTimeout(()=>{pending.delete(id);reject(new Error('Relay request timed out; check its result before issuing another action'));},75000);
      pending.set(id,{resolve,reject,timer});
      child.stdin.write(JSON.stringify({id,method:'request',params:{path,data,method}})+'\n');
    });
  }
  function close(){closed=true;fail('Relay session closed; no request was replayed');if(child)child.kill();}
  return {request,close};
}
