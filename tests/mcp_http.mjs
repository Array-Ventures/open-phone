// Real SDK/HTTP integration with an isolated relay fixture; no phone input.
import assert from 'node:assert/strict';
import http from 'node:http';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import {startMCPHTTP} from '../mcp_http.mjs';

const key='mcp-test-key-at-least-16';
let now=1000,held=null,entered=null,calls=[],closed=0;
const backendFactory=()=>({close(){closed++;},async request(path,data){
  calls.push({path,data});
  if(path==='/v1/phones')return [{id:'phone',connection_status:'online',width:1180,height:2556}];
  if(data?.method==='screenshot')return {status:'completed',result:{data:Buffer.from('fixture-image').toString('base64'),mime_type:'image/jpeg',width:621,height:1344,screen_width:1180,screen_height:2556,generation:1,timestamp:42}};
  if(path.endsWith('/home')&&held){entered?.();await held;}
  return {status:'completed',id:'fixture-job',result:{ok:true}};
}});
const app=await startMCPHTTP({port:0,key,backendFactory,clock:()=>now});
const clients=[];
async function client(){const c=new Client({name:'http-check',version:'1'});const transport=new StreamableHTTPClientTransport(new URL(app.url),{requestInit:{headers:{Authorization:'Bearer '+key}},reconnectionOptions:{maxRetries:0,initialReconnectionDelay:1,maxReconnectionDelay:1,reconnectionDelayGrowFactor:1}});await c.connect(transport);clients.push({c,transport});return {c,transport};}
async function raw(body,session,headers={}){return fetch(app.url,{method:'POST',headers:{Authorization:'Bearer '+key,Accept:'application/json, text/event-stream','Content-Type':'application/json','MCP-Protocol-Version':'2025-11-25',...(session?{'MCP-Session-Id':session}:{}),...headers},body:JSON.stringify(body)});}
const initialize={jsonrpc:'2.0',id:0,method:'initialize',params:{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'raw',version:'1'}}};
let checks=0;
try{
  assert.equal((await raw(initialize,null,{Authorization:'Bearer wrong'})).status,401);
  assert.equal((await raw(initialize,null,{Origin:'https://attacker.invalid'})).status,403);
  const wrongHost=await new Promise((resolve,reject)=>{const req=http.request(app.url,{method:'POST',headers:{Host:'attacker.invalid',Authorization:'Bearer '+key,'Content-Type':'application/json'}},res=>{res.resume();resolve(res.statusCode)});req.on('error',reject);req.end(JSON.stringify(initialize));});
  assert.equal(wrongHost,403);
  assert.equal((await raw({jsonrpc:'2.0',id:1,method:'tools/list'})).status,400);
  assert.equal(calls.length,0);checks++;

  const first=await client(),second=await client();
  assert.notEqual(first.transport.sessionId,second.transport.sessionId);
  assert.equal((await first.c.listTools()).tools.length,13);
  assert.equal((await second.c.listTools()).tools.length,13);checks++;
  assert.equal((await first.c.callTool({name:'screenshot',arguments:{phone_id:'phone'}})).content[0].type,'image');
  const before=calls.length;
  assert.equal((await second.c.callTool({name:'tap',arguments:{phone_id:'phone',x:620,y:1343}})).isError,true);
  assert.equal(calls.length,before);checks++;

  assert.ok(!(await first.c.callTool({name:'tap',arguments:{phone_id:'phone',x:620,y:1343}})).isError);
  assert.deepEqual(calls.at(-1),{path:'/v1/phones/phone/action',data:{method:'native_gesture',params:{action:'tap',params:{x:1179,y:2555},width:1180,height:2556}}});
  const count=calls.length;
  assert.equal((await first.c.callTool({name:'tap',arguments:{phone_id:'phone',x:1,y:1}})).isError,true);
  assert.equal(calls.length,count);checks++;

  await first.c.callTool({name:'screenshot',arguments:{phone_id:'phone'}});now+=30001;
  const stale=calls.length;
  assert.equal((await first.c.callTool({name:'tap',arguments:{phone_id:'phone',x:1,y:1}})).isError,true);
  assert.equal(calls.length,stale);checks++;

  const invalid=calls.length;
  assert.equal((await first.c.callTool({name:'press_home',arguments:{phone_id:'phone',extra:true}})).isError,true);
  assert.equal((await first.c.callTool({name:'press_home',arguments:{phone_id:'phone',constructor:'unexpected'}})).isError,true);
  assert.equal(calls.length,invalid);checks++;

  const body={jsonrpc:'2.0',id:'once',method:'tools/call',params:{name:'press_home',arguments:{phone_id:'phone'}}};
  let release;held=new Promise(resolve=>{release=resolve});const started=new Promise(resolve=>{entered=resolve});
  const initial=raw(body,first.transport.sessionId);await started;
  const duplicate=await raw(body,first.transport.sessionId);assert.equal(duplicate.status,409);
  release();assert.equal((await initial).status,200);held=null;
  const accepted=calls.length;
  const retry=await raw(body,first.transport.sessionId);assert.equal(retry.status,200);assert.ok(!(await retry.json()).result.isError);
  assert.equal(calls.length,accepted);
  const mismatch=await raw({...body,params:{name:'press_key',arguments:{phone_id:'phone',key:'enter'}}},first.transport.sessionId);
  assert.equal((await mismatch.json()).result.isError,true);assert.equal(calls.length,accepted);checks++;

  for(let i=0;i<65;i++)await raw({jsonrpc:'2.0',id:'evict-'+i,method:'tools/call',params:{name:'get_phone_status',arguments:{phone_id:'phone'}}},first.transport.sessionId);
  const afterEviction=calls.length;
  assert.equal((await (await raw(body,first.transport.sessionId)).json()).result.isError,true);
  assert.equal(calls.length,afterEviction);checks++;

  const session=second.transport.sessionId;await second.transport.terminateSession();
  assert.equal(app.sessions.has(session),false);
  assert.equal((await raw({jsonrpc:'2.0',id:9,method:'tools/list'},session)).status,404);checks++;
  assert.ok(closed>=1);
}finally{
  for(const {c} of clients)await c.close();
  await app.close();
}

await assert.rejects(startMCPHTTP({host:'192.168.1.2',port:0,key,backendFactory}),/require a TLS/);checks++;
const limited=await startMCPHTTP({port:0,key,maxSessions:1,rateLimit:1,backendFactory});
try{
  const transport=new StreamableHTTPClientTransport(new URL(limited.url),{requestInit:{headers:{Authorization:'Bearer '+key}},reconnectionOptions:{maxRetries:0,initialReconnectionDelay:1,maxReconnectionDelay:1,reconnectionDelayGrowFactor:1}});
  const c=new Client({name:'capacity-check',version:'1'});await c.connect(transport);
  const response=await fetch(limited.url,{method:'POST',headers:{Authorization:'Bearer '+key,Accept:'application/json, text/event-stream','Content-Type':'application/json'},body:JSON.stringify(initialize)});
  assert.equal(response.status,503);
  await assert.rejects(c.listTools(),error=>error.code===429);await c.close();checks++;
}finally{await limited.close();}
let idleClock=0;
const idle=await startMCPHTTP({port:0,key,idleMs:20,clock:()=>idleClock,backendFactory});
try{
  const initialized=await fetch(idle.url,{method:'POST',headers:{Authorization:'Bearer '+key,Accept:'application/json, text/event-stream','Content-Type':'application/json'},body:JSON.stringify(initialize)});
  const id=initialized.headers.get('mcp-session-id');await initialized.json();assert.ok(idle.sessions.has(id));
  idleClock=21;
  for(let i=0;i<20&&idle.sessions.size;i++)await new Promise(resolve=>setTimeout(resolve,10));
  assert.equal(idle.sessions.size,0);checks++;
}finally{await idle.close();}
console.log(JSON.stringify({http_mcp:'passed',checks,tools:13,scope:'SDK and HTTP integration with isolated relay fixture'}));
