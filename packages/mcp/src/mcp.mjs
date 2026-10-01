#!/usr/bin/env node
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import {pythonExecutable} from './runtime.mjs';

const child=spawn(pythonExecutable(),['-m','openphone.device.driver'],{stdio:['pipe','pipe','inherit']});
let counter=0;const pending=new Map();
createInterface({input:child.stdout}).on('line',line=>{
  let value;try{value=JSON.parse(line)}catch{return}
  const request=pending.get(value.id);if(!request)return;
  clearTimeout(request.timer);pending.delete(value.id);
  value.ok?request.resolve(value.result):request.reject(new Error(value.error || 'Driver operation failed'));
});
child.on('exit',()=>{for(const p of pending.values()){clearTimeout(p.timer);p.reject(new Error('Phone driver exited'))}pending.clear()});
child.on('error',error=>{console.error(`OpenPhone driver could not start: ${error.message}`);process.exitCode=1});
function call(method,params){return new Promise((resolve,reject)=>{
  const id=++counter;const timer=setTimeout(()=>{pending.delete(id);reject(new Error('Phone driver timed out'))},45000);
  pending.set(id,{resolve,reject,timer});child.stdin.write(JSON.stringify({id,method,params})+'\n');
})}
const number={type:'number',minimum:0,maximum:1};
const tools=[
  ['devices','List USB iPhone screen devices. This excludes cameras.',{},[]],
  ['connect','Connect USB capture and Classic Bluetooth HID for the chosen iPhone.',{address:{type:'string'},capture_id:{type:'string',default:'auto'}},['address']],
  ['status','Read capture and Bluetooth channel status; diagnostics includes recent transport events.',{diagnostics:{type:'boolean',default:false}},[]],
  ['apps','List installed app metadata through trusted USB. Requires optional go-ios. System inventory can include background components.',{include_system:{type:'boolean',default:true}},[]],
  ['screenshot','Get a fresh screenshot. Input coordinates are normalized to its full screen.',{max_dimension:{type:'number',minimum:320,maximum:4096},format:{type:'string',enum:['jpeg','png']}},[]],
  ['move','Move the pointer without pressing a button.',{x:number,y:number},['x','y']],
  ['tap','Tap normalized screen coordinates. count requests double/triple tap; hold_ms requests a long press. Verify that scrolling has settled before choosing a target.',{x:number,y:number,hold_ms:{type:'number',minimum:30,maximum:2000},count:{type:'integer',minimum:1,maximum:3},settle_ms:{type:'number',minimum:0,maximum:1500},interval_ms:{type:'number',minimum:50,maximum:500}},['x','y']],
  ['swipe','Drag from start to end using normalized screen coordinates.',{x1:number,y1:number,x2:number,y2:number,duration_ms:{type:'number',minimum:100,maximum:2000}},['x1','y1','x2','y2']],
  ['hold_and_drag','Hold at the starting point, then drag and release.',{x1:number,y1:number,x2:number,y2:number,hold_ms:{type:'number',minimum:0,maximum:2000},duration_ms:{type:'number',minimum:100,maximum:2000}},['x1','y1','x2','y2']],
  ['flick','Swipe quickly and release while moving, allowing momentum.',{x1:number,y1:number,x2:number,y2:number,duration_ms:{type:'number',minimum:100,maximum:2000}},['x1','y1','x2','y2']],
  ['scroll','Scroll content using a timed pointer drag.',{direction:{type:'string',enum:['up','down','left','right']},distance:{type:'number',minimum:0.05,maximum:0.7},duration_ms:{type:'number',minimum:100,maximum:2000}},[]],
  ['type_text','Type up to 200 US-layout characters in the focused field.',{text:{type:'string',maxLength:200}},['text']],
  ['press_key','Press a named key (enter, escape, backspace, tab, space, arrows) or one US-layout character. Modifier bits: Control=1, Shift=2, Option=4, Command=8.',{key:{type:'string'},modifiers:{type:'integer',minimum:0,maximum:255},repeat_count:{type:'integer',minimum:1,maximum:20}},['key']],
  ['press_button','Send device Button 3 (Home) or Button 9 (optional Use OpenPhone Shortcut). Each must be mapped in AssistiveTouch.',{button:{type:'string',enum:['home','shortcut']}},['button']],
  ['shortcut_action','Queue one action for the separately configured optional Shortcut bridge. trigger sends Button 9; a queued or written response does not mean completion. Experimental until the Shortcut is imported and validated on the phone.',{kind:{type:'string',enum:['copy_text','read_clipboard','open_app','open_url']},payload:{type:'object'},trigger:{type:'boolean',default:false},ttl:{type:'number',minimum:5,maximum:120}},['kind','payload']],
  ['shortcut_result','Read optional Shortcut completion status and result. Expired actions are not retried.',{action_id:{type:'string',pattern:'^[0-9a-f]{32}$'}},['action_id']],
  ['shortcut_cancel','Cancel a pending optional Shortcut action. This cannot undo an action the phone has already executed.',{action_id:{type:'string',pattern:'^[0-9a-f]{32}$'}},['action_id']],
];
const server=new Server({name:'open-phone',version:'0.1.0'},{capabilities:{tools:{}}});
server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:tools.map(([name,description,properties,required])=>({name:'phone_'+name,description,inputSchema:{type:'object',properties,required,additionalProperties:false}}))}));
server.setRequestHandler(CallToolRequestSchema,async request=>{
  const name=request.params.name.replace(/^phone_/,'');
  if(!tools.some(t=>t[0]===name))throw new Error('Unknown phone tool');
  try{
    const result=await call(name,request.params.arguments || {});
    if(name==='screenshot'){
      const {data,mime_type,...metadata}=result;
      return {content:[{type:'image',data,mimeType:mime_type},{type:'text',text:JSON.stringify(metadata)}]};
    }
    return {content:[{type:'text',text:JSON.stringify(result)}]};
  }catch(error){return {content:[{type:'text',text:error.message}],isError:true}}
});
process.on('exit',()=>child.kill());
process.stdin.on('end',()=>{child.stdin.end();server.close()});
await server.connect(new StdioServerTransport());
