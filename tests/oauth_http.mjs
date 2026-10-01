// Isolated OAuth HTTP + official SDK integration. No browser automation or phone input.
import assert from 'node:assert/strict';
import http from 'node:http';
import {createHash,randomBytes} from 'node:crypto';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
import {startMCPHTTP} from '../mcp_http.mjs';

const ownerKey='isolated-owner-key-not-a-live-secret';
let offset=0,calls=0,closed=0,checks=0;
const backendFactory=()=>({close(){closed++},async request(path){calls++;if(path==='/v1/phones')return [];return {status:'completed',result:{ok:true}};}});
const app=await startMCPHTTP({port:0,oauth:true,ownerKey,clock:()=>Date.now()+offset,backendFactory});
const base=new URL(app.url).origin,clients=[];
const init={jsonrpc:'2.0',id:0,method:'initialize',params:{protocolVersion:'2025-11-25',capabilities:{},clientInfo:{name:'oauth-fixture',version:'1'}}};
const metadata={client_name:'OAuth fixture <script>',redirect_uris:['http://127.0.0.1:9901/callback'],token_endpoint_auth_method:'none',grant_types:['authorization_code','refresh_token'],response_types:['code'],scope:'phone:control'};
async function post(path,params){return fetch(base+path,{method:'POST',body:new URLSearchParams(params),redirect:'manual'});}
async function register(extra={}){const res=await fetch(base+'/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...metadata,...extra})});assert.equal(res.status,201);return res.json();}
function pkce(){const verifier=randomBytes(32).toString('base64url');return {verifier,challenge:createHash('sha256').update(verifier).digest('base64url')};}
function authorizeURL(client,challenge,extra={}){const url=new URL('/authorize',base);url.search=new URLSearchParams({response_type:'code',client_id:client.client_id,redirect_uri:client.redirect_uris[0],code_challenge:challenge,code_challenge_method:'S256',resource:app.url,scope:'phone:control',state:'fixture-state',...extra});return url;}
async function consent(url){
  const redirect=await fetch(url,{redirect:'manual'});assert.equal(redirect.status,302);
  const location=redirect.headers.get('location');assert.equal(new URL(location).pathname,'/consent');
  const page=await fetch(location);assert.equal(page.status,200);
  const content=await page.text(),id=new URL(location).searchParams.get('id'),csrf=content.match(/name="csrf" value="([^"]+)"/)[1],cookie=page.headers.get('set-cookie').split(';')[0];
  assert.ok(content.includes('&lt;script&gt;')||content.includes('SDK fixture'));
  assert.ok(!content.includes(ownerKey));assert.ok(page.headers.get('content-security-policy').includes("frame-ancestors 'none'"));
  return {id,csrf,cookie};
}
async function approve(form,extra={},headers={}){return fetch(base+'/consent',{method:'POST',headers:{Origin:base,Cookie:form.cookie,...headers},body:new URLSearchParams({id:form.id,csrf:form.csrf,owner_key:ownerKey,decision:'allow',...extra}),redirect:'manual'});}
async function grant(client){const proof=pkce(),form=await consent(authorizeURL(client,proof.challenge));const res=await approve(form);assert.equal(res.status,302);const url=new URL(res.headers.get('location'));assert.equal(url.searchParams.get('state'),'fixture-state');return {proof,code:url.searchParams.get('code')};}
function exchange(client,value,extra={}){return post('/token',{grant_type:'authorization_code',client_id:client.client_id,code:value.code,code_verifier:value.proof.verifier,redirect_uri:client.redirect_uris[0],resource:app.url,...extra});}
function refresh(client,tokens,extra={}){return post('/token',{grant_type:'refresh_token',client_id:client.client_id,refresh_token:tokens.refresh_token,resource:app.url,...extra});}
async function raw(token,session,body=init,headers={}){return fetch(app.url,{method:'POST',headers:{Authorization:'Bearer '+token,Accept:'application/json, text/event-stream','Content-Type':'application/json',...(session?{'MCP-Session-Id':session}:{}),...headers},body:JSON.stringify(body)});}
async function statusJSON(response,status,error){assert.equal(response.status,status);const body=await response.json();if(error)assert.equal(body.error,error);return body;}

try{
  const challenge=await raw('invalid');assert.equal(challenge.status,401);assert.ok(challenge.headers.get('www-authenticate').includes('/.well-known/oauth-protected-resource/mcp'));
  assert.equal((await raw(ownerKey)).status,401);assert.equal(calls,0);
  const resource=await (await fetch(base+'/.well-known/oauth-protected-resource/mcp')).json();assert.equal(resource.resource,app.url);assert.deepEqual(resource.scopes_supported,['phone:control']);
  const discovery=await (await fetch(base+'/.well-known/oauth-authorization-server')).json();assert.deepEqual(discovery.token_endpoint_auth_methods_supported,['none']);assert.deepEqual(discovery.code_challenge_methods_supported,['S256']);assert.equal(discovery.registration_endpoint,base+'/register');checks++;

  const a=await register(),b=await register({redirect_uris:['https://second.example/callback']});assert.notEqual(a.client_id,b.client_id);assert.equal(a.client_secret,undefined);
  for(const extra of [{redirect_uris:['http://public.example/callback']},{redirect_uris:['https://u:p@example.com/callback']},{redirect_uris:['https://example.com/#fragment']},{redirect_uris:['https://bad;host.example/callback']},{token_endpoint_auth_method:'client_secret_post'}]){
    const response=await fetch(base+'/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...metadata,...extra})});await statusJSON(response,400,'invalid_client_metadata');
  }checks++;

  const proof=pkce();const wrongRedirect=await fetch(authorizeURL(a,proof.challenge,{redirect_uri:'https://attacker.invalid/callback'}),{redirect:'manual'});assert.equal(wrongRedirect.status,400);assert.equal(wrongRedirect.headers.get('location'),null);
  for(const extra of [{resource:'https://wrong.example/mcp'},{scope:'other'},{code_challenge_method:'plain'},{resource:''}]){
    const response=await fetch(authorizeURL(a,proof.challenge,extra),{redirect:'manual'});assert.equal(response.status,302);assert.ok(new URL(response.headers.get('location')).searchParams.has('error'));
  }checks++;

  const form=await consent(authorizeURL(a,proof.challenge));
  assert.equal((await approve(form,{owner_key:'wrong'})).status,403);
  assert.equal((await approve(form,{csrf:'wrong'})).status,403);
  assert.equal((await approve(form,{}, {Origin:'https://attacker.invalid'})).status,403);
  assert.equal((await approve(form,{}, {Cookie:''})).status,403);
  const deny=await approve(form,{decision:'deny'});assert.equal(deny.status,302);assert.equal(new URL(deny.headers.get('location')).searchParams.get('error'),'access_denied');assert.equal((await approve(form)).status,403);checks++;

  const value=await grant(a);
  await statusJSON(await exchange(a,value,{code_verifier:pkce().verifier}),400,'invalid_grant');
  await statusJSON(await exchange(a,value,{code_verifier:'short'}),400,'invalid_request');
  await statusJSON(await exchange(b,value),400,'invalid_grant');
  await statusJSON(await exchange(a,value,{redirect_uri:'https://wrong.example/callback'}),400,'invalid_grant');
  await statusJSON(await exchange(a,value,{resource:'https://wrong.example/mcp'}),400,'invalid_request');
  const tokens=await statusJSON(await exchange(a,value),200);assert.equal(tokens.expires_in,600);assert.equal(tokens.scope,'phone:control');
  await statusJSON(await exchange(a,value),400,'invalid_grant');checks++;

  const first=await raw(tokens.access_token);assert.equal(first.status,200);const session=first.headers.get('mcp-session-id');await first.json();assert.ok(app.sessions.has(session));
  const other=await statusJSON(await exchange(b,await grant(b)),200);
  assert.equal((await raw(other.access_token,session,{jsonrpc:'2.0',id:1,method:'tools/list'})).status,403);
  assert.equal((await raw(tokens.access_token,session,{jsonrpc:'2.0',id:2,method:'tools/list'},{Origin:'https://second.example'})).status,403);
  const allowed=await raw(tokens.access_token,session,{jsonrpc:'2.0',id:3,method:'tools/list'},{Origin:'http://127.0.0.1:9901'});assert.equal(allowed.status,200);assert.equal(allowed.headers.get('access-control-allow-origin'),'http://127.0.0.1:9901');assert.equal((await allowed.json()).result.tools.length,13);
  const unauthOrigin=await raw('invalid',null,init,{Origin:'http://127.0.0.1:9901'});assert.equal(unauthOrigin.status,401);assert.ok(unauthOrigin.headers.get('access-control-expose-headers').includes('WWW-Authenticate'));
  assert.equal((await fetch(app.url,{method:'OPTIONS',headers:{Origin:'http://127.0.0.1:9901'}})).status,204);
  assert.equal((await fetch(app.url,{method:'OPTIONS',headers:{Origin:'https://attacker.invalid'}})).status,403);checks++;

  await statusJSON(await refresh(b,tokens),400,'invalid_grant');
  await statusJSON(await refresh(a,tokens,{resource:'https://wrong.example/mcp'}),400,'invalid_request');
  await statusJSON(await refresh(a,tokens,{scope:'other'}),400,'invalid_scope');
  const rotated=await statusJSON(await refresh(a,tokens),200);assert.notEqual(rotated.access_token,tokens.access_token);assert.notEqual(rotated.refresh_token,tokens.refresh_token);
  assert.equal((await raw(tokens.access_token,session)).status,401);assert.equal((await raw(rotated.access_token,session,{jsonrpc:'2.0',id:4,method:'tools/list'})).status,200);
  await statusJSON(await refresh(a,tokens),400,'invalid_grant');assert.equal(app.sessions.has(session),false);assert.equal((await raw(rotated.access_token)).status,401);await statusJSON(await refresh(a,rotated),400,'invalid_grant');checks++;

  const revoked=await statusJSON(await exchange(a,await grant(a)),200);const initialized=await raw(revoked.access_token);const revokedSession=initialized.headers.get('mcp-session-id');await initialized.json();
  assert.equal((await post('/revoke',{client_id:b.client_id,token:revoked.refresh_token})).status,200);assert.equal((await raw(revoked.access_token,revokedSession,{jsonrpc:'2.0',id:5,method:'tools/list'})).status,200);
  assert.equal((await post('/revoke',{client_id:a.client_id,token:revoked.refresh_token})).status,200);assert.equal(app.sessions.has(revokedSession),false);assert.equal((await raw(revoked.access_token)).status,401);checks++;

  const expiring=await grant(a);offset+=300001;await statusJSON(await exchange(a,expiring),400,'invalid_grant');
  const expiredForm=await consent(authorizeURL(a,pkce().challenge));offset+=300001;assert.equal((await approve(expiredForm)).status,403);
  const short=await statusJSON(await exchange(a,await grant(a)),200);offset+=600001;assert.equal((await raw(short.access_token)).status,401);assert.equal((await refresh(a,short)).status,200);offset=0;checks++;

  // Real SDK discovery, DCR, challenge creation, code exchange and reconnect.
  let info,savedTokens,verifier,authorization,discovered;
  const sdkProvider={redirectUrl:'http://127.0.0.1:9902/callback',clientMetadata:{...metadata,client_name:'SDK fixture',redirect_uris:['http://127.0.0.1:9902/callback']},clientInformation:()=>info,saveClientInformation:value=>{info=value},tokens:()=>savedTokens,saveTokens:value=>{savedTokens=value},codeVerifier:()=>verifier,saveCodeVerifier:value=>{verifier=value},redirectToAuthorization:value=>{authorization=value},state:()=> 'sdk-state',discoveryState:()=>discovered,saveDiscoveryState:value=>{discovered=value}};
  const firstClient=new Client({name:'SDK fixture',version:'1'}),firstTransport=new StreamableHTTPClientTransport(new URL(app.url),{authProvider:sdkProvider});clients.push(firstClient);
  await assert.rejects(firstClient.connect(firstTransport),/Unauthorized/);assert.ok(info.client_id);assert.ok(authorization);
  const sdkForm=await consent(authorization),sdkApproved=await approve(sdkForm);assert.equal(sdkApproved.status,302);const callback=new URL(sdkApproved.headers.get('location'));assert.equal(callback.searchParams.get('state'),'sdk-state');
  await firstTransport.finishAuth(callback.searchParams.get('code'));assert.ok(savedTokens.access_token);await firstClient.close();
  const sdkClient=new Client({name:'SDK fixture',version:'1'}),transport=new StreamableHTTPClientTransport(new URL(app.url),{authProvider:sdkProvider});clients.push(sdkClient);await sdkClient.connect(transport);
  assert.equal((await sdkClient.listTools()).tools.length,13);assert.deepEqual((await sdkClient.callTool({name:'list_phones',arguments:{}})).content.map(v=>v.type),['text']);checks++;

  // Expired access causes SDK refresh; the existing grant/session stays valid.
  const old=savedTokens.access_token,oldSession=transport.sessionId;offset+=600001;
  assert.equal((await sdkClient.listTools()).tools.length,13);assert.notEqual(savedTokens.access_token,old);assert.equal(transport.sessionId,oldSession);offset=0;checks++;
  const wrongHost=await new Promise((resolve,reject)=>{const req=http.request(base+'/.well-known/oauth-authorization-server',{headers:{Host:'attacker.invalid'}},res=>{res.resume();resolve(res.statusCode)});req.on('error',reject);req.end();});assert.equal(wrongHost,403);assert.ok(closed>=2);checks++;
}finally{for(const client of clients)await client.close();await app.close();}

await assert.rejects(startMCPHTTP({port:0,oauth:true,ownerKey,publicUrl:'http://127.0.0.1/mcp?wrong=1',backendFactory}),/exact \/mcp/);checks++;
const restarted=await startMCPHTTP({port:0,oauth:true,ownerKey,backendFactory});try{assert.equal(restarted.oauthProvider.clients.size,0);assert.equal(restarted.oauthProvider.families.size,0);}finally{await restarted.close();}checks++;
console.log(JSON.stringify({oauth_http:'passed',checks,scope:'OAuth protocol and SDK integration against an isolated relay fixture; no rendered browser or real phone'}));
