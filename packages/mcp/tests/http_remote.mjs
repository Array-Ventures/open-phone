// SDK verification of a running endpoint. Read-only unless --screenshot is set;
// screenshot is still read-only but requires an available real host/phone.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {parseArgs} from 'node:util';
import {Client} from '@modelcontextprotocol/sdk/client/index.js';
import {StreamableHTTPClientTransport} from '@modelcontextprotocol/sdk/client/streamableHttp.js';
const {values}=parseArgs({options:{url:{type:'string'},'token-file':{type:'string'},phone:{type:'string'},screenshot:{type:'boolean',default:false}}});
assert.ok(values.url&&values['token-file']);
const token=(await readFile(values['token-file'],'utf8')).trim();
const transport=new StreamableHTTPClientTransport(new URL(values.url),{requestInit:{headers:{Authorization:'Bearer '+token}},reconnectionOptions:{maxRetries:0,initialReconnectionDelay:1,maxReconnectionDelay:1,reconnectionDelayGrowFactor:1}});
const c=new Client({name:'open-phone-remote-check',version:'1'});
try{
  await c.connect(transport);
  assert.equal((await c.listTools()).tools.length,13);
  const result=await c.callTool({name:'list_phones',arguments:{}});assert.ok(!result.isError,JSON.stringify(result));
  const phones=JSON.parse(result.content[0].text);
  const evidence={http_mcp:'passed',tools:13,phones:phones.map(p=>({id:p.id,connection_status:p.connection_status})),transport:new URL(values.url).protocol==='https:'?'verified TLS':'loopback HTTP'};
  if(values.phone){
    const status=await c.callTool({name:'get_phone_status',arguments:{phone_id:values.phone}});assert.ok(!status.isError,JSON.stringify(status));evidence.phone_status=JSON.parse(status.content[0].text);
    const before=await c.callTool({name:'tap',arguments:{phone_id:values.phone,x:1,y:1}});assert.equal(before.isError,true);evidence.missing_frame='rejected before input';
    if(values.screenshot){const capture=await c.callTool({name:'screenshot',arguments:{phone_id:values.phone}});assert.ok(!capture.isError,JSON.stringify(capture));assert.ok(capture.content.some(item=>item.type==='image'));evidence.screenshot='received';}
  }
  await transport.terminateSession();console.log(JSON.stringify(evidence));
}finally{await c.close();}
