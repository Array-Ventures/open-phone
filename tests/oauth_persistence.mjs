// Isolated durable OAuth HTTP/crash checks. No native driver or phone input.
import assert from 'node:assert/strict';
import {spawn,spawnSync} from 'node:child_process';
import {mkdtemp,readFile,rm,stat} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHash,randomBytes} from 'node:crypto';
import {startMCPHTTP} from '../mcp_http.mjs';
import {OAuthState} from '../oauth_state.mjs';
import {OwnerOAuthProvider} from '../oauth.mjs';

const ownerKey='isolated-persistence-owner-key-not-live';
const backendFactory=()=>({close(){},async request(path){assert.equal(path,'/v1/phones');return [];}});
if(process.argv[2]==='--fixture'){
  const app=await startMCPHTTP({port:Number(process.argv[4]),oauth:true,ownerKey,oauthStateFile:process.argv[3],backendFactory});
  console.log(JSON.stringify({url:app.url,lockPid:app.oauthProvider.state.lock.pid}));
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>app.close().then(()=>process.exit(0)));
}else{
  const directory=await mkdtemp(join(tmpdir(),'openphone-oauth-state-check-')),path=join(directory,'oauth.sqlite');
  const children=new Set();let service=null,checks=0;
  function sql(query,params=[],file=path){const result=spawnSync('python3',['-c','import sqlite3,json,sys\nwith sqlite3.connect(sys.argv[1]) as db:\n rows=db.execute(sys.argv[2],json.loads(sys.argv[3])).fetchall()\n print(json.dumps(rows))',file,query,JSON.stringify(params)],{encoding:'utf8'});assert.equal(result.status,0,result.stderr);return JSON.parse(result.stdout);}
  async function launch(port=0){
    const child=spawn(process.execPath,[fileURLToPath(import.meta.url),'--fixture',path,String(port)],{stdio:['ignore','pipe','pipe']});children.add(child);
    const exited=new Promise(resolve=>child.once('exit',(code,signal)=>{children.delete(child);resolve({code,signal})}));
    let error='';child.stderr.on('data',data=>{error+=data});
    const value=await new Promise((resolve,reject)=>{
      let data='';const timer=setTimeout(()=>reject(new Error('OAuth fixture startup timed out')),5000);
      child.stdout.on('data',chunk=>{data+=chunk;if(data.includes('\n')){clearTimeout(timer);try{resolve(JSON.parse(data.split('\n')[0]))}catch(err){reject(err)}}});
      child.once('exit',()=>{clearTimeout(timer);reject(new Error('OAuth fixture exited before readiness: '+error))});
    });
    return {...value,child,exited,base:new URL(value.url).origin,port:Number(new URL(value.url).port)};
  }
  async function stop(signal='SIGTERM'){
    const current=service;service=null;current.child.kill(signal);const result=await current.exited;
    assert.equal(signal==='SIGKILL'?result.signal:result.code,signal==='SIGKILL'?'SIGKILL':0);
    // Prove the lifetime flock is released before starting another service.
    let lock;for(let i=0;i<100;i++){try{lock=await OAuthState.open(path,ownerKey);break}catch{await new Promise(resolve=>setTimeout(resolve,10))}}
    assert.ok(lock,'Exited service must release its OAuth writer lock');await lock.close();return current.port;
  }
  async function register(){const response=await fetch(service.base+'/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({client_name:'Persistence fixture',redirect_uris:['http://127.0.0.1:9930/callback'],token_endpoint_auth_method:'none',grant_types:['authorization_code','refresh_token'],response_types:['code']})});assert.equal(response.status,201);return response.json();}
  async function post(route,params){return fetch(service.base+route,{method:'POST',body:new URLSearchParams(params),redirect:'manual'});}
  async function consent(client){
    const verifier=randomBytes(32).toString('base64url'),challenge=createHash('sha256').update(verifier).digest('base64url');
    const url=new URL('/authorize',service.base);url.search=new URLSearchParams({response_type:'code',client_id:client.client_id,redirect_uri:client.redirect_uris[0],code_challenge:challenge,code_challenge_method:'S256',resource:service.url,scope:'phone:control'});
    const response=await fetch(url,{redirect:'manual'});assert.equal(response.status,302);const location=response.headers.get('location');
    const page=await fetch(location),text=await page.text();assert.equal(page.status,200);
    return {verifier,location,id:new URL(location).searchParams.get('id'),csrf:text.match(/name="csrf" value="([^"]+)"/)[1],cookie:page.headers.get('set-cookie').split(';')[0]};
  }
  async function approve(form){const response=await fetch(service.base+'/consent',{method:'POST',headers:{Origin:service.base,Cookie:form.cookie},body:new URLSearchParams({id:form.id,csrf:form.csrf,owner_key:ownerKey,decision:'allow'}),redirect:'manual'});assert.equal(response.status,302);return {...form,code:new URL(response.headers.get('location')).searchParams.get('code')};}
  async function exchange(client,value){return post('/token',{grant_type:'authorization_code',client_id:client.client_id,code:value.code,code_verifier:value.verifier,redirect_uri:client.redirect_uris[0],resource:service.url});}
  async function refresh(client,tokens){return post('/token',{grant_type:'refresh_token',client_id:client.client_id,refresh_token:tokens.refresh_token,resource:service.url});}
  const init={jsonrpc:'2.0',id:0,method:'initialize',params:{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'persistence-fixture',version:'1'}}};
  async function raw(token,session,body=init){return fetch(service.url,{method:'POST',headers:{Authorization:'Bearer '+token,Accept:'application/json, text/event-stream','Content-Type':'application/json',...(session?{'MCP-Session-Id':session}:{})},body:JSON.stringify(body)});}
  async function tokens(response){assert.equal(response.status,200);return response.json();}
  try{
    service=await launch();const resource=service.url;
    assert.equal((await stat(path)).mode&0o777,0o600);assert.equal((await stat(path+'.lock')).mode&0o777,0o600);
    await assert.rejects(OAuthState.open(path,ownerKey),/already owned/);checks++;
    const client=await register(),value=await approve(await consent(client)),first=await tokens(await exchange(client,value));
    const initialized=await raw(first.access_token);assert.equal(initialized.status,200);const oldSession=initialized.headers.get('mcp-session-id');await initialized.json();
    const rotated=await tokens(await refresh(client,first));assert.notEqual(first.refresh_token,rotated.refresh_token);
    const pending=await consent(client),unexchanged=await approve(await consent(client));
    const rawStored=sql('SELECT data FROM state WHERE id=1')[0][0];
    for(const secret of [ownerKey,first.access_token,first.refresh_token,rotated.access_token,rotated.refresh_token,value.code,unexchanged.code,pending.csrf])assert.ok(!rawStored.includes(secret));
    assert.equal(sql('PRAGMA integrity_check')[0][0],'ok');assert.equal(sql('PRAGMA application_id')[0][0],0x4F504F41);checks++;

    const port=await stop('SIGKILL');service=await launch(port);
    assert.equal((await raw(rotated.access_token,oldSession,{jsonrpc:'2.0',id:1,method:'tools/list'})).status,404);
    const newSessionResponse=await raw(rotated.access_token);assert.equal(newSessionResponse.status,200);const newSession=newSessionResponse.headers.get('mcp-session-id');await newSessionResponse.json();
    const listed=await raw(rotated.access_token,newSession,{jsonrpc:'2.0',id:2,method:'tools/list'});assert.equal(listed.status,200);assert.equal((await listed.json()).result.tools.length,13);
    assert.equal((await fetch(pending.location)).status,400);checks++;
    assert.equal((await exchange(client,value)).status,400);const retainedCode=await tokens(await exchange(client,unexchanged));assert.ok(retainedCode.access_token);assert.equal((await exchange(client,unexchanged)).status,400);checks++;
    assert.equal((await refresh(client,first)).status,400);assert.equal((await raw(rotated.access_token)).status,401);assert.equal((await refresh(client,rotated)).status,400);checks++;

    assert.equal((await post('/revoke',{client_id:client.client_id,token:retainedCode.refresh_token})).status,200);
    const port2=await stop();service=await launch(port2);assert.equal((await raw(retainedCode.access_token)).status,401);assert.equal((await refresh(client,retainedCode)).status,400);assert.equal((await exchange(client,unexchanged)).status,400);checks++;

    // An actual SQLite abort must not return new tokens or undo an old commit.
    const beforeFailure=await tokens(await exchange(client,await approve(await consent(client))));
    const storedBefore=sql('SELECT data FROM state WHERE id=1')[0][0];
    sql("CREATE TRIGGER fixture_abort BEFORE UPDATE ON state BEGIN SELECT RAISE(ABORT,'fixture write failure'); END");
    const failed=await refresh(client,beforeFailure);assert.equal(failed.status,500);assert.ok(!(await failed.json()).access_token);
    assert.equal(sql('SELECT data FROM state WHERE id=1')[0][0],storedBefore);assert.equal((await raw(beforeFailure.access_token)).status,500);
    assert.equal((await fetch(service.base+'/health')).status,500);
    await stop();sql('DROP TRIGGER fixture_abort');checks++;

    let state=await OAuthState.open(path,ownerKey);
    try{const provider=new OwnerOAuthProvider({resource,ownerKey,state});assert.ok(await provider.verifyAccessToken(beforeFailure.access_token));}
    finally{await state.close();}checks++;
    state=await OAuthState.open(path,'different-isolated-owner-key');try{assert.throws(()=>new OwnerOAuthProvider({resource,ownerKey:'different-isolated-owner-key',state}),/owner key or integrity/);}finally{await state.close();}
    state=await OAuthState.open(path,ownerKey);try{assert.throws(()=>new OwnerOAuthProvider({resource:'http://127.0.0.1:9999/mcp',ownerKey,state}),/canonical resource/);}finally{await state.close();}
    assert.equal(sql('SELECT data FROM state WHERE id=1')[0][0],storedBefore);checks++;

    state=await OAuthState.open(path,ownerKey);
    try{
      const provider=new OwnerOAuthProvider({resource,ownerKey,state,clock:()=>Date.now()+8*86400000});assert.equal(provider.families.size,0);assert.equal(provider.refresh.size,0);assert.equal(provider.codes.size,0);assert.equal(provider.clients.size,1);
      const after=state.load();assert.equal(after.families.length,0);assert.equal(after.refresh.length,0);
      const exited=new Promise(resolve=>state.lock.once('exit',resolve));state.lock.kill('SIGKILL');await exited;
      await assert.rejects(provider.verifyAccessToken(beforeFailure.access_token),/state unavailable/);assert.throws(()=>provider.registerClient(client),/state unavailable/);
    }finally{await state.close();}checks++;

    // Modifying even public metadata without the owner's signature fails load.
    const envelope=JSON.parse(sql('SELECT data FROM state WHERE id=1')[0][0]);envelope.payload=envelope.payload.replace('Persistence fixture','Modified fixture');sql('UPDATE state SET data=? WHERE id=1',[JSON.stringify(envelope)]);
    const corrupt=sql('SELECT data FROM state WHERE id=1')[0][0];state=await OAuthState.open(path,ownerKey);try{assert.throws(()=>new OwnerOAuthProvider({resource,ownerKey,state}),/owner key or integrity/);}finally{await state.close();}
    assert.equal(sql('SELECT data FROM state WHERE id=1')[0][0],corrupt);checks++;
    const other=join(directory,'unrelated.sqlite');sql('CREATE TABLE valuable(value TEXT)',[],other);const original=await readFile(other);
    state=await OAuthState.open(other,ownerKey);try{assert.throws(()=>new OwnerOAuthProvider({resource,ownerKey,state}),/storage unavailable/);}finally{await state.close();}
    assert.deepEqual(await readFile(other),original);checks++;
    const marked=join(directory,'unrelated-marked.sqlite');sql('PRAGMA application_id=12345',[],marked);const markedBefore=await readFile(marked);
    state=await OAuthState.open(marked,ownerKey);try{assert.throws(()=>new OwnerOAuthProvider({resource,ownerKey,state}),/storage unavailable/);}finally{await state.close();}assert.deepEqual(await readFile(marked),markedBefore);
    const operationPath=join(directory,'operation.sqlite');
    const operation=spawn('python3',['-c','import os,sys,fcntl\nfd=os.open(sys.argv[1],os.O_RDWR|os.O_CREAT,0o600)\nfcntl.flock(fd,fcntl.LOCK_EX)\nprint("ready",flush=True)\nsys.stdin.buffer.read()\nos.close(fd)',operationPath+'.operation.lock'],{stdio:['pipe','pipe','ignore']});
    await new Promise((resolve,reject)=>{operation.stdout.once('data',resolve);operation.once('error',reject);operation.once('exit',()=>reject(new Error('Operation lock exited before readiness')))});
    try{await assert.rejects(OAuthState.open(operationPath,ownerKey),/already owned/);}
    finally{await new Promise(resolve=>{operation.once('exit',resolve);operation.stdin.end()});}
    state=await OAuthState.open(operationPath,ownerKey);try{assert.equal(state.load(),null);}finally{await state.close();}checks++;
  }finally{
    if(service){service.child.kill('SIGTERM');await service.exited;}
    for(const child of children)child.kill('SIGKILL');
    await rm(directory,{recursive:true,force:true});
  }
  console.log(JSON.stringify({oauth_persistence:'passed',checks,scope:'SQLite, real HTTP restart/SIGKILL, refresh reuse, revocation and storage failure with isolated credentials; no phone input'}));
}
