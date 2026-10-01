// Original single-owner OAuth provider behind the official SDK's auth routes.
// Optional durable state stores signed snapshots containing token hashes.
import express from 'express';
import {createHash,randomBytes,randomUUID,timingSafeEqual} from 'node:crypto';
import {fileURLToPath} from 'node:url';
import {mcpAuthRouter,createOAuthMetadata,mcpAuthMetadataRouter} from '@modelcontextprotocol/sdk/server/auth/router.js';
import {requireBearerAuth} from '@modelcontextprotocol/sdk/server/auth/middleware/bearerAuth.js';
import {InvalidClientMetadataError,InvalidGrantError,InvalidRequestError,InvalidScopeError,InvalidTokenError,TemporarilyUnavailableError,ServerError} from '@modelcontextprotocol/sdk/server/auth/errors.js';

export const PHONE_SCOPE='phone:control';
const fresh=()=>randomBytes(32).toString('base64url');
const hash=value=>createHash('sha256').update(value).digest('hex');
const equal=(a,b)=>{if(typeof a!=='string'||typeof b!=='string')return false;const x=Buffer.from(a),y=Buffer.from(b);return x.length===y.length&&timingSafeEqual(x,y);};
const html=value=>String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export class OwnerOAuthProvider{
  constructor({resource,ownerKey,clock=Date.now,onRevoke=async()=>{},state=null,onStorageFailure=async()=>{}}){
    this.resource=new URL(resource);this.ownerKey=ownerKey;this.clock=clock;this.onRevoke=onRevoke;
    if(typeof ownerKey!=='string'||ownerKey.length<16)throw new Error('OAuth requires a separate owner key');
    this.clients=new Map();this.pending=new Map();this.codes=new Map();this.families=new Map();this.access=new Map();this.refresh=new Map();
    this.state=state;this.persistenceFailed=false;this.onStorageFailure=onStorageFailure;
    this.clientsStore={getClient:id=>{this.available();return this.clients.get(id)},registerClient:client=>this.registerClient(client)};
    if(state){this.restore(state.load());this.prune();this.commit();}
  }
  unavailable(){
    if(!this.persistenceFailed){this.persistenceFailed=true;Promise.resolve().then(()=>this.onStorageFailure()).catch(()=>{});}
    throw new ServerError('OAuth state unavailable; access is disabled until repaired and restarted');
  }
  available(){if(this.persistenceFailed)this.unavailable();if(this.state){try{this.state.healthy()}catch{this.unavailable()}}}
  snapshot(){
    return {version:1,resource:this.resource.href,clients:[...this.clients],families:[...this.families].map(([id,value])=>[id,{...value,refreshHashes:[...value.refreshHashes]}]),refresh:[...this.refresh],codes:[...this.codes].map(([digest,value])=>[digest,{clientId:value.clientId,expires:value.expires,params:{...value.params,resource:value.params.resource?.href}}])};
  }
  commit(){this.available();if(this.state){try{this.state.save(this.snapshot())}catch{this.unavailable()}}}
  restore(value){
    if(value===null)return;
    if(value?.version!==1||value.resource!==this.resource.href)throw new Error('OAuth state version or canonical resource does not match');
    for(const [name,limit] of [['clients',128],['families',256],['refresh',32768],['codes',128]]){
      if(!Array.isArray(value[name])||value[name].length>limit||value[name].some(entry=>!Array.isArray(entry)||entry.length!==2))throw new Error('Invalid OAuth state collection');
      const map=new Map(value[name]);if(map.size!==value[name].length)throw new Error('Duplicate OAuth state entry');this[name]=map;
    }
    const digest=value=>typeof value==='string'&&/^[a-f0-9]{64}$/.test(value);
    for(const [id,client] of this.clients){if(id!==client.client_id||typeof id!=='string')throw new Error('Invalid stored OAuth client');this.validateClient(client);}
    for(const [id,family] of this.families){
      if(id!==family.id||!this.clients.has(family.clientId)||family.resource!==this.resource.href||!Array.isArray(family.scopes)||family.scopes.length!==1||family.scopes[0]!==PHONE_SCOPE||!Number.isFinite(family.expires)||!Number.isFinite(family.accessExpires)||typeof family.revoked!=='boolean'||!digest(family.accessHash)||!Array.isArray(family.refreshHashes)||family.refreshHashes.some(hash=>!digest(hash)))throw new Error('Invalid stored OAuth grant');
      family.refreshHashes=new Set(family.refreshHashes);
      if(!family.revoked)this.access.set(family.accessHash,id);
    }
    for(const [key,entry] of this.refresh){const family=this.families.get(entry.familyId);if(!digest(key)||!family||typeof entry.spent!=='boolean'||!family.refreshHashes.has(key))throw new Error('Invalid stored refresh token');}
    for(const family of this.families.values())for(const key of family.refreshHashes)if(this.refresh.get(key)?.familyId!==family.id)throw new Error('Missing stored refresh token');
    for(const [key,code] of this.codes){
      const client=this.clients.get(code.clientId),params=code.params;
      if(!digest(key)||!client||!Number.isFinite(code.expires)||params?.resource!==this.resource.href||!client.redirect_uris.includes(params.redirectUri)||!/^[A-Za-z0-9_-]{43}$/.test(params.codeChallenge)||!Array.isArray(params.scopes)||params.scopes.length!==1||params.scopes[0]!==PHONE_SCOPE)throw new Error('Invalid stored authorization code');
      params.resource=new URL(params.resource);
    }
  }
  registerClient(client){
    this.prune();
    if(this.clients.size>=128)throw new TemporarilyUnavailableError('Client capacity reached');
    this.validateClient(client);
    const value={...client,client_id:randomUUID(),client_id_issued_at:Math.floor(this.clock()/1000)};
    this.clients.set(value.client_id,value);this.commit();return value;
  }
  validateClient(client){
    if(client.token_endpoint_auth_method!=='none'||client.client_secret)throw new InvalidClientMetadataError('Only public PKCE clients are supported');
    if(!Array.isArray(client.redirect_uris)||!client.redirect_uris.length||client.redirect_uris.length>8)throw new InvalidClientMetadataError('Register 1–8 redirect URIs');
    for(const value of client.redirect_uris){
      let uri;try{uri=new URL(value)}catch{throw new InvalidClientMetadataError('Invalid redirect URI')}
      const loopback=['127.0.0.1','localhost','[::1]'].includes(uri.hostname);
      if(uri.username||uri.password||uri.hash||!/^[A-Za-z0-9.:[\]-]+$/.test(uri.hostname)||!(uri.protocol==='https:'||uri.protocol==='http:'&&loopback))throw new InvalidClientMetadataError('Redirects require HTTPS or loopback HTTP, without credentials/fragments');
    }
    if(client.client_name!==undefined&&(typeof client.client_name!=='string'||client.client_name.length>120))throw new InvalidClientMetadataError('Invalid client name');
    if(client.grant_types?.some(value=>!['authorization_code','refresh_token'].includes(value))||client.response_types?.some(value=>value!=='code'))throw new InvalidClientMetadataError('Only authorization code and refresh grants are supported');
  }
  checkResource(resource){if(resource?.href!==this.resource.href)throw new InvalidRequestError('The resource must be this MCP endpoint');}
  scopes(requested){const values=requested?.length?requested:[PHONE_SCOPE];if(values.some(value=>value!==PHONE_SCOPE))throw new InvalidScopeError('Unsupported phone scope');return [PHONE_SCOPE];}
  prune(){
    this.available();const now=this.clock();let changed=false;
    for(const [key,value] of this.pending)if(value.expires<=now)this.pending.delete(key);
    for(const [key,value] of this.codes)if(value.expires<=now){this.codes.delete(key);changed=true;}
    for(const [id,family] of this.families)if(family.expires<=now){this.access.delete(family.accessHash);for(const key of family.refreshHashes)this.refresh.delete(key);this.families.delete(id);changed=true;}
    if(changed)this.commit();
  }
  async authorize(client,params,res){
    this.prune();this.checkResource(params.resource);const scopes=this.scopes(params.scopes);
    if(!/^[A-Za-z0-9_-]{43}$/.test(params.codeChallenge))throw new InvalidRequestError('Invalid S256 challenge');
    if(this.pending.size>=64||this.codes.size>=128)throw new TemporarilyUnavailableError('Authorization capacity reached');
    const id=fresh();this.pending.set(id,{clientId:client.client_id,params:{...params,scopes},expires:this.clock()+300000,csrf:null});
    res.redirect(302,new URL('/consent?id='+id,this.resource).href);
  }
  code(client,code){this.prune();const value=this.codes.get(hash(code));if(!value||value.clientId!==client.client_id)throw new InvalidGrantError('Code is expired, used or belongs to another client');return value;}
  async challengeForAuthorizationCode(client,code){return this.code(client,code).params.codeChallenge;}
  async exchangeAuthorizationCode(client,code,verifier,redirectUri,resource){
    this.checkResource(resource);const value=this.code(client,code);
    if(redirectUri!==value.params.redirectUri)throw new InvalidGrantError('Redirect does not match authorization');
    if(this.families.size>=256||this.refresh.size>=32768)throw new TemporarilyUnavailableError('Grant capacity reached');
    this.codes.delete(hash(code));return this.issue(client.client_id,value.params.scopes);
  }
  issue(clientId,scopes,family=null){
    const access=fresh(),refresh=fresh();const now=this.clock();
    if(!family){family={id:randomUUID(),clientId,scopes,resource:this.resource.href,expires:now+7*86400000,refreshHashes:new Set(),accessHash:null,revoked:false};this.families.set(family.id,family);}
    if(family.accessHash)this.access.delete(family.accessHash);
    family.accessHash=hash(access);family.accessExpires=Math.min(now+600000,family.expires);
    const refreshHash=hash(refresh);family.refreshHashes.add(refreshHash);
    this.access.set(family.accessHash,family.id);this.refresh.set(refreshHash,{familyId:family.id,spent:false});
    this.commit();
    return {access_token:access,token_type:'Bearer',expires_in:Math.floor((family.accessExpires-now)/1000),refresh_token:refresh,scope:family.scopes.join(' ')};
  }
  async exchangeRefreshToken(client,token,scopes,resource){
    this.prune();this.checkResource(resource);
    const entry=this.refresh.get(hash(token)),family=entry&&this.families.get(entry.familyId);
    if(!family||family.clientId!==client.client_id||family.revoked)throw new InvalidGrantError('Refresh token is invalid');
    if(entry.spent){await this.revokeFamily(family);throw new InvalidGrantError('Refresh token reuse revoked this grant');}
    if(scopes?.some(value=>!family.scopes.includes(value)))throw new InvalidScopeError('Refresh cannot expand scope');
    if(this.refresh.size>=32768)throw new TemporarilyUnavailableError('Refresh history capacity reached');
    entry.spent=true;return this.issue(client.client_id,family.scopes,family);
  }
  async verifyAccessToken(token){
    this.prune();const family=this.families.get(this.access.get(hash(token)));
    if(!family||family.revoked||family.accessExpires<=this.clock()||family.resource!==this.resource.href)throw new InvalidTokenError('Access token is invalid or expired');
    return {token,clientId:family.clientId,scopes:family.scopes,expiresAt:Math.floor(family.accessExpires/1000),resource:this.resource,extra:{grantId:family.id,ownerId:'owner'}};
  }
  async revokeFamily(family){family.revoked=true;this.access.delete(family.accessHash);this.commit();await this.onRevoke(family.id);}
  async revokeToken(client,request){
    this.prune();const digest=hash(request.token);const id=this.access.get(digest)||this.refresh.get(digest)?.familyId;const family=this.families.get(id);
    if(family&&family.clientId===client.client_id)await this.revokeFamily(family);
  }
  originAllowed(origin,clientId){this.available();if(!origin||origin===this.resource.origin)return true;const clients=clientId?[this.clients.get(clientId)]:this.clients.values();return [...clients].some(client=>client?.redirect_uris.some(uri=>new URL(uri).origin===origin));}
  consent(req,res){
    this.prune();const id=req.query.id,value=this.pending.get(id);
    if(typeof id!=='string'||!value){res.status(400).send('Authorization request expired. Start again from your MCP client.');return;}
    value.csrf=fresh();
    res.setHeader('Set-Cookie','openphone_consent='+value.csrf+'; HttpOnly; SameSite=Strict; Path=/consent; Max-Age=300'+(this.resource.protocol==='https:'?'; Secure':''));
    const client=this.clients.get(value.clientId),name=client.client_name||'MCP client';
    // Browser form-action also governs the post-consent redirect. Allow this
    // registered callback origin so a conforming browser can finish OAuth.
    res.set('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self' "+new URL(value.params.redirectUri).origin+"; frame-ancestors 'none'; base-uri 'none'");
    res.type('html').send(`<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>OpenPhone access</title><link rel="stylesheet" href="/oauth.css"></head><body><header><strong>OpenPhone</strong></header><main><section><h1>Allow ${html(name)} to control your phones?</h1><p>This client can list your phones, read screenshots and send taps, gestures and keyboard input.</p><p>Registered callback: <code>${html(value.params.redirectUri)}</code></p><form method="post" action="/consent"><input type="hidden" name="id" value="${html(id)}"><input type="hidden" name="csrf" value="${html(value.csrf)}"><label for="owner-key">Owner access key</label><input id="owner-key" name="owner_key" type="password" autocomplete="off" required><p class="hint">Use the separate key in your relay computer’s .oauth-owner-token file. Never enter your Mac or iPhone password here.</p><div class="row"><button name="decision" value="allow">Allow access</button><button name="decision" value="deny" class="secondary">Deny</button></div></form></section></main></body></html>`);
  }
  approve(req,res){
    this.prune();const {id,csrf,owner_key,decision}=req.body||{},value=this.pending.get(id);
    const cookie=String(req.headers.cookie||'').split(';').map(v=>v.trim()).find(v=>v.startsWith('openphone_consent='))?.slice('openphone_consent='.length);
    if(req.headers.origin!==this.resource.origin||!value||!equal(csrf,value.csrf)||!equal(cookie,value.csrf)||!equal(owner_key,this.ownerKey)||!['allow','deny'].includes(decision)){
      res.status(403).send('Access was not granted. Check the owner key and reopen the consent page.');return;
    }
    this.pending.delete(id);const redirect=new URL(value.params.redirectUri);
    if(value.params.state!==undefined)redirect.searchParams.set('state',value.params.state);
    if(decision==='deny')redirect.searchParams.set('error','access_denied');
    else{if(this.codes.size>=128){res.status(503).send('Authorization capacity reached.');return;}const code=fresh();this.codes.set(hash(code),{...value,expires:this.clock()+300000});this.commit();redirect.searchParams.set('code',code);}
    res.redirect(302,redirect.href);
  }
}

export function installOwnerOAuth(app,provider){
  const resource=provider.resource;
  app.use((req,res,next)=>{res.set({'Cache-Control':'no-store','Referrer-Policy':'no-referrer','X-Content-Type-Options':'nosniff'});next();});
  app.use('/consent',(req,res,next)=>{res.set('Content-Security-Policy',"default-src 'none'; style-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'");next();});
  app.get('/oauth.css',(req,res)=>res.sendFile(fileURLToPath(new URL('./ui/style.css',import.meta.url))));
  app.get('/consent',(req,res)=>provider.consent(req,res));
  let windowStart=Date.now(),attempts=0;
  app.post('/consent',express.urlencoded({extended:false,limit:'16kb'}),(req,res)=>{
    if(Date.now()-windowStart>60000){windowStart=Date.now();attempts=0;}
    if(++attempts>20){res.status(429).send('Too many owner-access attempts. Try again later.');return;}provider.approve(req,res);
  });
  app.use(['/authorize','/token','/revoke'],express.urlencoded({extended:false,limit:'64kb'}));
  app.use('/register',express.json({limit:'64kb'}));
  app.post('/token',(req,res,next)=>{
    if(req.body?.grant_type==='authorization_code'&&!/^[A-Za-z0-9._~-]{43,128}$/.test(req.body.code_verifier||'')){
      res.status(400).json(new InvalidRequestError('PKCE verifier must contain 43–128 unreserved characters').toResponseObject());return;
    }next();
  });
  const options={provider,issuerUrl:new URL(resource.origin),resourceServerUrl:resource,scopesSupported:[PHONE_SCOPE],resourceName:'OpenPhone'};
  const metadata=createOAuthMetadata(options);
  metadata.token_endpoint_auth_methods_supported=['none'];
  metadata.revocation_endpoint_auth_methods_supported=['none'];
  app.use(mcpAuthMetadataRouter({...options,oauthMetadata:metadata}));
  app.use(mcpAuthRouter(options));
  return requireBearerAuth({verifier:provider,requiredScopes:[PHONE_SCOPE],resourceMetadataUrl:new URL('/.well-known/oauth-protected-resource/mcp',resource).href});
}
