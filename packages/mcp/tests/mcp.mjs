import assert from 'node:assert/strict';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { StdioClientTransport } from '@modelcontextprotocol/sdk/client/stdio.js';
import { fileURLToPath } from 'node:url';
import { mkdir, writeFile } from 'node:fs/promises';

const evidence=new URL('../../../private/hardware-checks/',import.meta.url);

const client=new Client({name:'open-phone-check',version:'0.1.0'});
const env=Object.fromEntries(['OPEN_PHONE_BRIDGE_URL','OPEN_PHONE_PYTHON','OPEN_PHONE_IOS','OPEN_PHONE_HOME','OPEN_PHONE_NATIVE_BUNDLE']
  .filter(key=>process.env[key]).map(key=>[key,process.env[key]]));
const transport=new StdioClientTransport({command:process.execPath,args:[fileURLToPath(new URL('../src/mcp.mjs',import.meta.url))],env,stderr:'inherit'});
try{
  await client.connect(transport);
  const {tools}=await client.listTools();assert.equal(tools.length,17);
  assert.equal(new Set(tools.map(t=>t.name)).size,17);
  const status=await client.callTool({name:'phone_status',arguments:{}});assert.ok(!status.isError);
  const invalid=await client.callTool({name:'phone_tap',arguments:{x:2,y:.5}});assert.equal(invalid.isError,true);
  console.log(JSON.stringify({mcp:'passed',tools:tools.length,invalid_coordinate:'rejected'}));
  const at=process.argv.indexOf('--hardware');
  if(at>=0){
    const address=process.argv[at+1];assert.ok(address,'Provide an iPhone Bluetooth address after --hardware');
    const connected=await client.callTool({name:'phone_connect',arguments:{address}});assert.ok(!connected.isError,JSON.stringify(connected));
    if(process.argv.includes('--apps')){
      const inventory=await client.callTool({name:'phone_apps',arguments:{}});assert.ok(!inventory.isError);
      const value=JSON.parse(inventory.content[0].text);assert.ok(value.applications.length>0);
      console.log(JSON.stringify({installed_apps:'passed',count:value.applications.length}));
    }
    const screenshot=await client.callTool({name:'phone_screenshot',arguments:{max_dimension:1000}});assert.ok(!screenshot.isError);
    const img=screenshot.content.find(c=>c.type==='image');assert.ok(img);assert.equal(img.mimeType,'image/jpeg');
    await mkdir(evidence,{recursive:true});
    const file=fileURLToPath(new URL('mcp-screenshot.jpg',evidence));
    await writeFile(file,Buffer.from(img.data,'base64'));
    console.log(JSON.stringify({hardware:'connected',screenshot:file,...JSON.parse(screenshot.content.find(c=>c.type==='text').text)}));
    if(process.argv.includes('--bridge')){
      const run=async(kind,payload)=>{
        const enqueued=await client.callTool({name:'phone_shortcut_action',arguments:{kind,payload,trigger:true,ttl:60}});
        assert.ok(!enqueued.isError,JSON.stringify(enqueued));
        const action=JSON.parse(enqueued.content[0].text);
        for(let i=0;i<30;i++){
          await new Promise(resolve=>setTimeout(resolve,500));
          const response=await client.callTool({name:'phone_shortcut_result',arguments:{action_id:action.id}});
          assert.ok(!response.isError,JSON.stringify(response));
          const state=JSON.parse(response.content[0].text);
          if(state.state==='completed')return state.result;
          assert.ok(!['expired','cancelled'].includes(state.state),JSON.stringify(state));
        }
        throw new Error('Phone Shortcut did not complete; inspect its screen for a permission prompt');
      };
      const text='OpenPhone MCP ✓ 🌍';
      await run('copy_text',{text});
      const clipboard=await run('read_clipboard',{});assert.equal(clipboard.text,text);
      console.log(JSON.stringify({shortcut_bridge:'passed',unicode_round_trip:true}));
    }
    if(process.argv.includes('--home')){
      const home=await client.callTool({name:'phone_press_button',arguments:{button:'home'}});assert.ok(!home.isError);
      await new Promise(resolve=>setTimeout(resolve,800));
      const after=await client.callTool({name:'phone_screenshot',arguments:{max_dimension:1000}});
      const img=after.content.find(c=>c.type==='image');assert.ok(img);
      const file=fileURLToPath(new URL('mcp-home.jpg',evidence));
      await writeFile(file,Buffer.from(img.data,'base64'));
      console.log(JSON.stringify({home_reports:'written',screenshot:file,verify:'Inspect image for the Home Screen'}));
    }
  }
}finally{await client.close()}
