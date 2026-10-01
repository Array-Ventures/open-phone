// Shared original phone-tool implementation; each instance owns its frame/history state.
import { Server } from '@modelcontextprotocol/sdk/server/index.js';
import { CallToolRequestSchema, ListToolsRequestSchema } from '@modelcontextprotocol/sdk/types.js';
import { createHash } from 'node:crypto';
const phone={type:'string',pattern:'^[A-Za-z0-9_-]{1,80}$'};
const pixel={type:'integer',minimum:0};const point={phone_id:phone,x:pixel,y:pixel};
const speed={type:'string',enum:['slow','medium','fast']};
const drag={phone_id:phone,from_x:pixel,from_y:pixel,to_x:pixel,to_y:pixel,speed};
const tools=[
  ['list_phones','List phones registered by Mac hosts, with status and native dimensions.',{},[]],
  ['get_phone_status','Read one phone’s host-reported connection status.',{phone_id:phone},['phone_id']],
  ['screenshot','Return a fresh JPEG, long edge at most 1344 pixels. Subsequent pointer coordinates are pixels in this image.',{phone_id:phone},['phone_id']],
  ['tap','Tap image pixel coordinates. Take a screenshot first and verify the result afterward.',point,['phone_id','x','y']],
  ['double_tap','Double tap image pixel coordinates.',point,['phone_id','x','y']],
  ['triple_tap','Triple tap image pixel coordinates.',point,['phone_id','x','y']],
  ['long_press','Hold image pixel coordinates; duration is milliseconds.',{...point,duration:{type:'number',minimum:30,maximum:2000}},['phone_id','x','y']],
  ['flick','Flick from image pixel coordinates in the direction of finger movement. Up reveals content lower in a list.',{...point,direction:{type:'string',enum:['up','down','left','right']}},['phone_id','x','y','direction']],
  ['drag','Drag between image pixel coordinates.',drag,['phone_id','from_x','from_y','to_x','to_y']],
  ['hold_and_drag','Hold, then drag between image pixel coordinates.',{...drag,hold_duration_ms:{type:'number',minimum:0,maximum:2000}},['phone_id','from_x','from_y','to_x','to_y']],
  ['type_text','Type at most 100 ASCII characters into a focused field.',{phone_id:phone,text:{type:'string',maxLength:100}},['phone_id','text']],
  ['press_key','Press a named key with optional modifiers.',{phone_id:phone,key:{type:'string',enum:['enter','escape','backspace','arrow_up','arrow_down','arrow_left','arrow_right']},modifiers:{type:'array',items:{type:'string',enum:['control','shift','alternate','command']}},repeat:{type:'integer',minimum:1,maximum:20}},['phone_id','key']],
  ['press_home','Send the host’s Button 3 mapped to Home.',{phone_id:phone},['phone_id']],
];
export function createRelayTools(request, {clock=Date.now}={}) {
const frames=new Map();
const server=new Server({name:'open-phone-relay',version:'0.1.0'},{capabilities:{tools:{}}});
server.setRequestHandler(ListToolsRequestSchema,async()=>({tools:tools.map(([name,description,properties,required])=>({name,description,inputSchema:{type:'object',properties,required,additionalProperties:false}}))}));
async function execute({params}) {
  const name=params.name;const args={...(params.arguments || {})};
  if(!tools.some(t=>t[0]===name))throw new Error('Unknown tool');
  try{
    if(name==='list_phones')return {content:[{type:'text',text:JSON.stringify(await request('/v1/phones'))}]};
    const id=args.phone_id;if(typeof id!=='string' || !/^[A-Za-z0-9_-]{1,80}$/.test(id))throw new Error('Invalid phone ID');
    const path='/v1/phones/'+id;delete args.phone_id;
    if(name==='get_phone_status')return {content:[{type:'text',text:JSON.stringify(await request(path+'/status'))}]};
    if(name==='screenshot'){
      frames.delete(id);
      const job=await request(path+'/action',{method:'screenshot',params:{format:'jpeg',max_dimension:1344}});
      if(job.status!=='completed')throw new Error(job.result?.error || 'Screenshot failed');
      const {data,mime_type,...metadata}=job.result;
      if(typeof data!=='string' || mime_type!=='image/jpeg')throw new Error('Relay did not return a JPEG');
      if(!Number.isInteger(metadata.width)||!Number.isInteger(metadata.height)||metadata.width<2||metadata.height<2||!Number.isInteger(metadata.screen_width)||!Number.isInteger(metadata.screen_height)||metadata.screen_width<2||metadata.screen_height<2)throw new Error('Invalid screenshot dimensions');
      frames.set(id,{...metadata,capturedAt:clock()});
      return {content:[{type:'image',data,mimeType:mime_type},{type:'text',text:JSON.stringify(metadata)}]};
    }
    if(['tap','double_tap','triple_tap','long_press','flick','drag','hold_and_drag'].includes(name)){
      const frame=frames.get(id);if(!frame || clock()-frame.capturedAt>30000)throw new Error('Take a fresh screenshot of this phone before a pointer action');
      for(const axis of ['x','y','from_x','from_y','to_x','to_y'])if(axis in args){
        const dimension=axis.endsWith('x')?'width':'height';const value=args[axis];
        if(!Number.isInteger(value) || value<0 || value>=frame[dimension])throw new Error('Coordinate outside the last screenshot');
        args[axis]=Math.min(frame['screen_'+dimension]-1,Math.round(value*(frame['screen_'+dimension]-1)/(frame[dimension]-1)));
      }
    }
    const actions={double_tap:'double-tap',triple_tap:'triple-tap',long_press:'tap-and-hold',hold_and_drag:'hold-and-drag',type_text:'type',press_key:'keypress',press_home:'home'};
    if(name==='long_press' && 'duration' in args){args.duration_ms=args.duration;delete args.duration}
    const frame=frames.get(id);frames.delete(id);
    const pointer=['tap','double_tap','triple_tap','long_press','flick','drag','hold_and_drag'].includes(name);
    const job=pointer?await request(path+'/action',{method:'native_gesture',params:{action:actions[name]||name,params:args,width:frame.screen_width,height:frame.screen_height}}):await request(path+'/'+(actions[name] || name),args);
    if(job.status!=='completed')throw new Error(job.result?.error || 'Action did not complete');
    return {content:[{type:'text',text:JSON.stringify(job)}]};
  }catch(error){return {content:[{type:'text',text:error.message}],isError:true}}
}
let busy=false,cacheBytes=0;
const seen=new Map(),results=new Map();
server.setRequestHandler(CallToolRequestSchema,async(request,extra)=>{
  const id=JSON.stringify(extra.requestId);
  const fingerprint=createHash('sha256').update(JSON.stringify(request.params)).digest('hex');
  if(seen.has(id)){
    if(seen.get(id)!==fingerprint)return toolError('Request ID was already used for a different tool call');
    return results.get(id)?.value || toolError('Request already accepted; result pending or evicted. It will not be executed again.');
  }
  if(seen.size>=4096)return toolError('Session call history is full; open a new session');
  seen.set(id,fingerprint);
  let value;
  if(busy)value=toolError('Another tool call is running in this session; take a fresh screenshot after it completes');
  else{
    busy=true;
    try{validate(request.params);value=await execute(request)}
    catch(error){value=toolError(error.message)}
    finally{busy=false}
  }
  const bytes=Buffer.byteLength(JSON.stringify(value));
  if(bytes<=8*1024*1024){results.set(id,{value,bytes});cacheBytes+=bytes;}
  while(results.size>64||cacheBytes>8*1024*1024){const key=results.keys().next().value;cacheBytes-=results.get(key).bytes;results.delete(key);}
  return value;
});
return server;
}
function toolError(message){return {content:[{type:'text',text:message}],isError:true};}
function validate({name,arguments:args={}}){
  const definition=tools.find(tool=>tool[0]===name);if(!definition)throw new Error('Unknown tool');
  if(!args||Array.isArray(args)||typeof args!=='object')throw new Error('Tool arguments must be an object');
  const properties=definition[2];
  if(definition[3].some(key=>!Object.hasOwn(args,key))||Object.keys(args).some(key=>!Object.hasOwn(properties,key)))throw new Error('Missing or unexpected tool arguments');
  for(const [key,value] of Object.entries(args)){
    const rule=properties[key];
    if(rule.type==='integer'&&!Number.isInteger(value)||rule.type==='number'&&(typeof value!=='number'||!Number.isFinite(value))||rule.type==='string'&&typeof value!=='string'||rule.type==='array'&&(!Array.isArray(value)||value.some(item=>typeof item!=='string'||!rule.items.enum.includes(item))))throw new Error('Invalid '+key);
    if(rule.enum&&!rule.enum.includes(value)||rule.pattern&&!new RegExp(rule.pattern).test(value)||rule.minimum!==undefined&&value<rule.minimum||rule.maximum!==undefined&&value>rule.maximum||rule.maxLength!==undefined&&value.length>rule.maxLength)throw new Error('Invalid '+key);
  }
}
