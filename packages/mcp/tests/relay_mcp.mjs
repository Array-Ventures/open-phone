import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';

const evidence=new URL('../../../private/hardware-checks/',import.meta.url);

const env=Object.fromEntries(['OPEN_PHONE_RELAY_URL','OPEN_PHONE_RELAY_CA','OPEN_PHONE_RELAY_TOKEN_FILE','OPEN_PHONE_PYTHON']
  .filter(key=>process.env[key]).map(key=>[key,process.env[key]]));
const client=new Client({name:'open-phone-relay-check',version:'0.1.0'});
const transport=new StdioClientTransport({command:process.execPath,args:[fileURLToPath(new URL('../src/relay_mcp.mjs',import.meta.url))],env,stderr:'inherit'});
try{
  await client.connect(transport);
  const {tools}=await client.listTools();assert.equal(tools.length,13);
  assert.equal(new Set(tools.map(t=>t.name)).size,13);
  console.log(JSON.stringify({relay_mcp:'inventory passed',tools:13}));
  const at=process.argv.indexOf('--hardware');
  if(at>=0){
    const phone_id=process.argv[at+1];assert.ok(phone_id);
    const phones=await client.callTool({name:'list_phones',arguments:{}});assert.ok(!phones.isError,JSON.stringify(phones));
    assert.ok(JSON.parse(phones.content[0].text).some(p=>p.id===phone_id && p.connection_status==='online'));
    const absentFrame=await client.callTool({name:'tap',arguments:{phone_id,x:1,y:1}});assert.equal(absentFrame.isError,true);
    const screenshot=async(filename)=>{
      const value=await client.callTool({name:'screenshot',arguments:{phone_id}});assert.ok(!value.isError,JSON.stringify(value));
      const image=value.content.find(c=>c.type==='image');assert.ok(image);assert.equal(image.mimeType,'image/jpeg');
      const metadata=JSON.parse(value.content.find(c=>c.type==='text').text);
      assert.ok(metadata.width<=1344 && metadata.height<=1344);
      await mkdir(evidence,{recursive:true});
      await writeFile(fileURLToPath(new URL(filename,evidence)),Buffer.from(image.data,'base64'));
      return metadata;
    };
    await screenshot('relay-mcp-initial.jpg');
    const home=await client.callTool({name:'press_home',arguments:{phone_id}});assert.ok(!home.isError,JSON.stringify(home));
    await new Promise(resolve=>setTimeout(resolve,1500));
    const frame=await screenshot('relay-mcp-home.jpg');
    const invalid=await client.callTool({name:'tap',arguments:{phone_id,x:frame.width,y:1}});assert.equal(invalid.isError,true);
    console.log(JSON.stringify({hardware:'images and Home passed',frame,missing_frame:'rejected',invalid_coordinate:'rejected'}));
    if(process.argv.includes('--review-tap')){
      console.log('HARNESS: inspect relay-mcp-home.jpg, then write TAP x y using a safe visible target in that image.');
      if(process.stdin.readableEnded || process.stdin.destroyed)throw new Error('Review requires an open input stream; run with a TTY');
      process.stdin.resume();
      const value=await new Promise((resolve,reject)=>{
        const cleanup=()=>{clearTimeout(timer);process.stdin.off('data',data);process.stdin.off('end',end)};
        const data=chunk=>{cleanup();resolve(chunk.toString().trim())};
        const end=()=>{cleanup();reject(new Error('Review input closed before a target was supplied'))};
        const timer=setTimeout(()=>{cleanup();reject(new Error('Review target was not supplied within 60 seconds'))},60000);
        process.stdin.once('data',data);process.stdin.once('end',end);
      });
      process.stdin.pause();const match=/^TAP (\d+) (\d+)$/.exec(value);assert.ok(match,'Expected TAP x y');
      const x=Number(match[1]),y=Number(match[2]);assert.ok(x<frame.width && y<frame.height);
      const tap=await client.callTool({name:'tap',arguments:{phone_id,x,y}});
      assert.ok(!tap.isError,JSON.stringify(tap));
      await new Promise(resolve=>setTimeout(resolve,1500));await screenshot('relay-mcp-tap.jpg');
      console.log(JSON.stringify({tap:'completed',x,y,screenshot:'relay-mcp-tap.jpg',verify:'Inspect the intended UI result'}));
    }
  }
}finally{await client.close()}
