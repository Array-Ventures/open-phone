// Original durable snapshot adapter; no native phone helper ownership.
import {spawn,spawnSync} from 'node:child_process';
import {createHmac,timingSafeEqual} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {resolve} from 'node:path';

const helper=fileURLToPath(new URL('./oauth_state.py',import.meta.url));
const sign=(key,payload)=>createHmac('sha256',key).update('OpenPhone OAuth state v1\n').update(payload).digest('hex');
const equal=(a,b)=>{if(typeof a!=='string'||typeof b!=='string')return false;const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);};

export class OAuthState{
  constructor(path,ownerKey,lock){this.path=resolve(path);this.ownerKey=ownerKey;this.lock=lock;this.closed=false;this.failed=false;}
  static async open(path,ownerKey){
    const lock=spawn('python3',[helper,'hold',resolve(path)],{stdio:['pipe','pipe','ignore']});
    lock.stdin.on('error',()=>{});
    lock.on('error',()=>{});
    try{
      await new Promise((resolve,reject)=>{
        let output='',settled=false;const timer=setTimeout(()=>finish(new Error('OAuth state lock timed out')),5000);
        const finish=(error)=>{if(settled)return;settled=true;clearTimeout(timer);lock.stdout.off('data',onData);error?reject(error):resolve();};
        const onData=data=>{output+=data;if(output.length>1024){finish(new Error('Invalid OAuth state lock response'));return;}if(output.includes('\n')){try{const value=JSON.parse(output.split('\n')[0]);finish(value.ready?null:new Error('OAuth state storage unavailable or already owned'))}catch{finish(new Error('Invalid OAuth state lock response'))}}};
        lock.stdout.on('data',onData);
        lock.once('error',()=>finish(new Error('Unable to start OAuth state lock')));
        lock.once('exit',()=>finish(new Error('OAuth state storage unavailable or already owned')));
      });
      return new OAuthState(path,ownerKey,lock);
    }catch(error){lock.stdin.end();lock.kill();throw error;}
  }
  healthy(){if(this.closed||this.failed||this.lock.exitCode!==null||this.lock.signalCode!==null)throw new Error('OAuth state storage unavailable');}
  run(command,input){
    this.healthy();
    const result=spawnSync('python3',[helper,command,this.path,String(this.lock.pid)],{input,encoding:'utf8',timeout:5000,maxBuffer:17*1024*1024});
    if(result.error||result.status!==0){this.failed=true;throw new Error('OAuth state storage unavailable');}
    try{return JSON.parse(result.stdout)}catch{this.failed=true;throw new Error('Invalid OAuth state response')}
  }
  load(){
    const loaded=this.run('load');if(loaded?.snapshot===null)return null;
    if(typeof loaded?.snapshot!=='string')throw new Error('Invalid OAuth state response');
    const envelope=JSON.parse(loaded.snapshot);
    if(!envelope||typeof envelope!=='object')throw new Error('OAuth state owner key or integrity check failed');
    if(typeof envelope.payload!=='string'||!equal(envelope.mac,sign(this.ownerKey,envelope.payload)))throw new Error('OAuth state owner key or integrity check failed');
    this.lastPayload=envelope.payload;return JSON.parse(envelope.payload);
  }
  save(value){
    this.healthy();const payload=JSON.stringify(value);if(payload===this.lastPayload)return;
    const result=this.run('save',JSON.stringify({payload,mac:sign(this.ownerKey,payload)}));
    if(!result?.saved){this.failed=true;throw new Error('OAuth state commit was not confirmed');}
    this.lastPayload=payload;
  }
  async close(){
    if(this.closed)return;this.closed=true;
    if(this.lock.exitCode!==null||this.lock.signalCode!==null)return;
    await new Promise(resolve=>{this.lock.once('exit',resolve);this.lock.stdin.end();});
  }
}
