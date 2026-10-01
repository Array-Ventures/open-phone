#!/usr/bin/env node
import {runtimePath} from './runtime.mjs';
// Original stateful Streamable HTTP endpoint. No native phone helpers run here.
import http from 'node:http';
import https from 'node:https';
import {randomUUID,randomBytes,timingSafeEqual} from 'node:crypto';
import {readFile,writeFile,chmod} from 'node:fs/promises';
import {parseArgs} from 'node:util';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {isIP} from 'node:net';
import express from 'express';
import {StreamableHTTPServerTransport} from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import {isInitializeRequest} from '@modelcontextprotocol/sdk/types.js';
import {createRelayTools} from './relay_tools.mjs';
import {createRelayRPC} from './relay_rpc.mjs';
import {OwnerOAuthProvider,installOwnerOAuth} from './oauth.mjs';
import {OAuthState} from './oauth_state.mjs';

function equal(a,b){const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);}
function send(res,status,message,headers={}){if(res.headersSent)return;const body=JSON.stringify({jsonrpc:'2.0',id:null,error:{code:-32000,message}});res.writeHead(status,{'Content-Type':'application/json','Cache-Control':'no-store','X-Content-Type-Options':'nosniff',...headers});res.end(body);}
async function readBody(req){
  if(req.headers['content-type']?.split(';')[0]!=='application/json')throw Object.assign(new Error('application/json required'),{status:415});
  if(Number(req.headers['content-length'])>65536)throw Object.assign(new Error('Request too large'),{status:413});
  let size=0;const chunks=[];
  for await(const chunk of req){size+=chunk.length;if(size>65536)throw Object.assign(new Error('Request too large'),{status:413});chunks.push(chunk);}
  try{return JSON.parse(Buffer.concat(chunks).toString('utf8'))}catch{throw Object.assign(new Error('Invalid JSON'),{status:400});}
}

export async function startMCPHTTP({host='127.0.0.1',port=8770,key,cert,tlsKey,hostnames=[],oauth=false,ownerKey,publicUrl,oauthStateFile,maxSessions=16,idleMs=20*60*1000,rateLimit=300,clock=Date.now,backendFactory=createRelayRPC}={}){
  if(!isIP(host)||host==='0.0.0.0'||host==='::')throw new Error('Bind a specific IP address');
  const local=host==='127.0.0.1'||host==='::1';
  if(Boolean(cert)!==Boolean(tlsKey)||!local&&!cert)throw new Error('Non-loopback MCP listeners require a TLS certificate and key');
  if(!oauth&&(typeof key!=='string'||key.length<16))throw new Error('A separate MCP access key of at least 16 characters is required');
  if(oauth&&(typeof ownerKey!=='string'||ownerKey.length<16))throw new Error('A separate OAuth owner key of at least 16 characters is required');
  const sessions=new Map(),hosts=new Set();let starting=0,webApp=null,provider=null,oauthState=null,closing=false,closePromise=null;
  async function closeSession(id){const entry=sessions.get(id);if(!entry)return;sessions.delete(id);entry.backend.close();await entry.server.close();}
  const handler=async(req,res)=>{
    try{
      const authority=req.headers.host,scheme=cert?'https':'http';
      if(!hosts.has(authority)||(!oauth&&req.headers.origin&&req.headers.origin!==scheme+'://'+authority)){send(res,403,'Host or Origin rejected');return;}
      const path=new URL(req.url,scheme+'://'+authority).pathname;
      if(path==='/health'&&req.method==='GET'){if(oauth)provider.available();res.writeHead(200,{'Content-Type':'application/json','Cache-Control':'no-store'});res.end(JSON.stringify({ok:true,transport:'streamable-http',authentication:oauth?'oauth':'bearer',oauth_storage:oauth?(oauthState?'sqlite':'ephemeral'):undefined}));return;}
      if(path!=='/mcp'){send(res,404,'Unknown route');return;}
      if(oauth){if(!req.auth){send(res,503,'OAuth initialization pending');return;}}
      else if(!equal(req.headers.authorization||'','Bearer '+key)){send(res,401,'MCP bearer key required',{'WWW-Authenticate':'Bearer realm="OpenPhone MCP"'});return;}
      if(!['GET','POST','DELETE'].includes(req.method)){send(res,405,'Use GET, POST or DELETE',{'Allow':'GET, POST, DELETE'});return;}
      const body=req.method==='POST'?await readBody(req):undefined;
      if(oauth){
        try{await provider.verifyAccessToken(req.auth.token);}
        catch{send(res,401,'OAuth grant expired or revoked',{'WWW-Authenticate':'Bearer error="invalid_token", resource_metadata="'+new URL('/.well-known/oauth-protected-resource/mcp',provider.resource).href+'"'});return;}
      }
      if(Array.isArray(body)){send(res,400,'JSON-RPC batches are not supported');return;}
      let id=req.headers['mcp-session-id'];
      if(id!==undefined&&typeof id!=='string'){send(res,400,'Invalid MCP session header');return;}
      let entry=id?sessions.get(id):null;
      if(id&&!entry){send(res,404,'Session expired or unknown; initialize a new session');return;}
      const principal=oauth?req.auth.extra.grantId:'bearer';
      if(entry&&entry.principal!==principal){send(res,403,'Session belongs to another grant');return;}
      if(!entry){
        if(req.method!=='POST'||!isInitializeRequest(body)){send(res,400,'Initialize a session before requesting tools');return;}
        if(sessions.size+starting>=maxSessions){send(res,503,'MCP session capacity reached');return;}
        starting++;
        const backend=backendFactory();
        const server=createRelayTools(backend.request,{clock});
        const transport=new StreamableHTTPServerTransport({sessionIdGenerator:randomUUID,enableJsonResponse:true,onsessioninitialized:sessionId=>{id=sessionId;sessions.set(id,entry);},onsessionclosed:sessionId=>closeSession(sessionId)});
        entry={backend,server,transport,principal,last:clock(),window:clock(),count:0,inflight:0,activeIds:new Set()};
        try{await server.connect(transport);if(closing)throw Object.assign(new Error('MCP service is stopping'),{status:503});await transport.handleRequest(req,res,body);}
        catch(error){if(id)await closeSession(id);else{backend.close();await server.close();}throw error;}
        finally{starting--;if(!id){backend.close();await server.close();}}
        return;
      }
      entry.last=clock();
      if(clock()-entry.window>=60000){entry.window=clock();entry.count=0;}
      if(++entry.count>rateLimit){send(res,429,'Session request rate exceeded',{'Retry-After':Math.max(1,Math.ceil((60000-(clock()-entry.window))/1000))});return;}
      const requestId=req.method==='POST'&&body?.id!==undefined?JSON.stringify(body.id):null;
      if(requestId&&entry.activeIds.has(requestId)){send(res,409,'Request already running; it will not be executed again');return;}
      if(requestId)entry.activeIds.add(requestId);
      if(req.method==='POST')entry.inflight++;
      try{await entry.transport.handleRequest(req,res,body);}
      finally{if(requestId)entry.activeIds.delete(requestId);if(req.method==='POST')entry.inflight--;}
    }catch(error){send(res,error.status||500,error.status?error.message:'MCP request failed; no automatic replay');}
  };
  const dispatch=(req,res)=>{
    if(closing){send(res,503,'MCP service is stopping');return;}
    if(!hosts.has(req.headers.host)){send(res,403,'Host rejected');return;}
    if(oauth){if(!webApp){send(res,503,'OAuth initialization pending');return;}webApp(req,res);}
    else handler(req,res);
  };
  const listener=cert?https.createServer({cert,key:tlsKey,minVersion:'TLSv1.2',handshakeTimeout:10000},dispatch):http.createServer(dispatch);
  listener.headersTimeout=10000;listener.requestTimeout=10000;
  await new Promise((resolve,reject)=>{listener.once('error',reject);listener.listen(port,host,resolve);});
  const boundPort=listener.address().port;
  for(const name of [host,...hostnames])hosts.add((isIP(name)===6?'['+name+']':name)+':'+boundPort);
  let url=(cert?'https':'http')+'://'+(isIP(host)===6?'['+host+']':host)+':'+boundPort+'/mcp';
  try{
    if(oauth){
      const resource=new URL(publicUrl||url);
      if(resource.pathname!=='/mcp'||resource.search||resource.hash||resource.username||resource.password||resource.protocol!==(cert?'https:':'http:'))throw new Error('OAuth public URL must use the listener scheme and exact /mcp path, without credentials, query or fragment');
      if(resource.protocol!=='https:'&&!['127.0.0.1','localhost'].includes(resource.hostname))throw new Error('OAuth requires HTTPS outside IPv4 loopback');
      hosts.add(resource.host);url=resource.href;
      if(oauthStateFile)oauthState=await OAuthState.open(oauthStateFile,ownerKey);
      provider=new OwnerOAuthProvider({resource:url,ownerKey,clock,state:oauthState,onStorageFailure:()=>Promise.allSettled([...sessions.keys()].map(closeSession)),onRevoke:grantId=>Promise.allSettled([...sessions].filter(([,entry])=>entry.principal===grantId).map(([id])=>closeSession(id)))});
      const app=express();app.disable('x-powered-by');
      const auth=installOwnerOAuth(app,provider);
      app.use('/mcp',(req,res,next)=>{
        const origin=req.headers.origin;
        if(!provider.originAllowed(origin)){send(res,403,'Origin rejected');return;}
        if(origin)res.set({'Access-Control-Allow-Origin':origin,'Vary':'Origin','Access-Control-Expose-Headers':'MCP-Session-Id, WWW-Authenticate'});
        if(req.method==='OPTIONS'){
          res.set({'Access-Control-Allow-Methods':'GET, POST, DELETE, OPTIONS','Access-Control-Allow-Headers':'Authorization, Content-Type, MCP-Session-Id, MCP-Protocol-Version, Last-Event-ID','Access-Control-Max-Age':'600'});res.status(204).end();return;
        }
        auth(req,res,()=>{
          if(!provider.originAllowed(origin,req.auth.clientId)){send(res,403,'Origin does not belong to this client');return;}
          next();
        });
      });
      app.use(handler);
      app.use((error,req,res,next)=>send(res,error.status||500,'OAuth request rejected'));
      webApp=app;
    }
  }catch(error){if(oauthState)await oauthState.close();await new Promise(resolve=>listener.close(resolve));throw error;}
  const timer=setInterval(()=>{try{provider?.prune()}catch{}for(const [id,entry] of sessions)if(!entry.inflight&&clock()-entry.last>idleMs)closeSession(id).catch(()=>{});},Math.min(idleMs,10000));timer.unref();
  function close(){
    if(closePromise)return closePromise;closing=true;clearInterval(timer);
    closePromise=(async()=>{await Promise.allSettled([...sessions.keys()].map(closeSession));listener.closeIdleConnections();await new Promise(resolve=>listener.close(resolve));if(oauthState)await oauthState.close();})();return closePromise;
  }
  return {listener,sessions,close,url,oauthProvider:provider};
}

async function main(){
  const {values}=parseArgs({options:{host:{type:'string',default:'127.0.0.1'},port:{type:'string',default:'8770'},cert:{type:'string'},key:{type:'string'},hostname:{type:'string',multiple:true,default:[]},oauth:{type:'boolean',default:false},'public-url':{type:'string'},'oauth-state-file':{type:'string',default:runtimePath('private/oauth-state.sqlite')},'oauth-ephemeral':{type:'boolean',default:false},'owner-token-file':{type:'string',default:runtimePath('.oauth-owner-token')},'token-file':{type:'string',default:runtimePath('.mcp-http-token')}}});
  const tokenFile=values.oauth?values['owner-token-file']:values['token-file'];
  let token;
  try{token=(await readFile(tokenFile,'utf8')).trim();}
  catch(error){if(error.code!=='ENOENT')throw error;token=randomBytes(32).toString('base64url');await writeFile(tokenFile,token+'\n',{mode:0o600,flag:'wx'});}
  await chmod(tokenFile,0o600);
  const app=await startMCPHTTP({host:values.host,port:Number(values.port),key:values.oauth?undefined:token,ownerKey:values.oauth?token:undefined,oauth:values.oauth,oauthStateFile:values.oauth&&!values['oauth-ephemeral']?values['oauth-state-file']:undefined,publicUrl:values['public-url'],hostnames:values.hostname,cert:values.cert?await readFile(values.cert):undefined,tlsKey:values.key?await readFile(values.key):undefined});
  console.log('OpenPhone MCP: '+app.url+'; '+(values.oauth?'OAuth owner':'bearer')+' key remains in '+tokenFile);
  for(const signal of ['SIGINT','SIGTERM'])process.once(signal,()=>app.close().then(()=>process.exit(0)));
}
if(process.argv[1]&&import.meta.url===pathToFileURL(process.argv[1]).href)main().catch(error=>{console.error(error.message);process.exitCode=1;});
