// Isolated client-logic checks. This is not a rendered-browser/hardware test.
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

class Element {
  constructor(){this.listeners=new Map();this.options=[];this.value='';this.classList={toggle(){}};}
  addEventListener(name,fn){this.listeners.set(name,fn);}
  replaceChildren(...items){this.options=items;}
  add(option){this.options.push(option);}
  getBoundingClientRect(){return {left:100,top:50,width:295,height:639};}
  setPointerCapture(){}
}
const source=await readFile(new URL('../../../src/openphone/services/assets/dashboard.js',import.meta.url),'utf8');
const elements=new Map();
const element=id=>{if(!elements.has(id))elements.set(id,new Element());return elements.get(id)};
let clock=100,requests=[],failure=false;
const context=vm.createContext({document:{getElementById:element,querySelectorAll:()=>[],hidden:true},
  Option:class{constructor(text,value){this.text=text;this.value=value}},
  performance:{now:()=>clock},location:{hash:'',pathname:'/'},history:{replaceState(){}},
  setTimeout(){return 1},URL,encodeURIComponent,
  fetch:async(path,options={})=>{
    requests.push({path,options});
    const value=path==='/api/status'?{address:null,capture_id:null}:path==='/api/devices'?{devices:[],usb_phones:[]}:path.startsWith('/api/apps')?{applications:[{name:'Fixture app',bundle_id:'org.example.fixture'}]}:{ok:true};
    return {ok:!failure,status:failure?409:200,json:async()=>failure?{error:'Partial write failed'}:value};
  }});
vm.runInContext(source,context);
await new Promise(resolve=>setImmediate(resolve));
requests=[];
const run=code=>vm.runInContext(code,context);
const fresh=()=>run("state.address='AA:BB:CC:DD:EE:FF';state.frame={width:1180,height:2556,loaded:performance.now()};state.busy=false;state.paused=false;controls()");

assert.equal(element('controls').disabled,true);
assert.equal(run('point({clientX:395,clientY:689},{width:1180,height:2556}).x'),1179);
assert.equal(run('point({clientX:395,clientY:689},{width:1180,height:2556}).y'),2555);
assert.equal(run('point({clientX:0,clientY:0},{width:1180,height:2556}).x'),0);
fresh();clock=4001;
await assert.rejects(run("action('press_button',{button:'home'})"),/fresh preview/);
assert.equal(requests.length,0);

fresh();
const down={button:0,pointerId:1,clientX:247.5,clientY:369.5,preventDefault(){}};
element('screen').listeners.get('pointerdown')(down);
clock+=100;
await element('screen').listeners.get('pointerup')({...down});
assert.equal(requests.length,1);
const tap=JSON.parse(requests[0].options.body);
assert.deepEqual(tap,{address:'AA:BB:CC:DD:EE:FF',method:'native_gesture',params:{action:'tap',params:{x:590,y:1278},width:1180,height:2556}});
assert.equal(run('state.frame'),null);
assert.equal(element('controls').disabled,true);
await assert.rejects(run("action('press_button',{button:'home'})"),/fresh preview/);
assert.equal(requests.length,1);

requests=[];fresh();failure=true;
await assert.rejects(run("exclusive(()=>action('press_button',{button:'home'}))"),/Partial write/);
assert.equal(requests.length,1);assert.equal(run('state.busy'),false);assert.equal(run('state.frame'),null);
failure=false;

requests=[];fresh();element('pointer-mode').value='double-tap';
element('screen').listeners.get('pointerdown')(down);clock+=50;
await element('screen').listeners.get('pointerup')({...down});
assert.equal(JSON.parse(requests[0].options.body).params.action,'double-tap');
requests=[];fresh();element('pointer-mode').value='hold-and-drag';
element('screen').listeners.get('pointerdown')(down);clock+=100;
await element('screen').listeners.get('pointerup')({...down,clientY:689});
const drag=JSON.parse(requests[0].options.body).params;
assert.equal(drag.action,'hold-and-drag');assert.equal(drag.params.hold_duration_ms,500);
assert.equal(drag.params.from_y,1278);assert.equal(drag.params.to_y,2555);

requests=[];fresh();
await element('load-apps').listeners.get('click')({preventDefault(){}});
assert.equal(element('app').options[1].value,'org.example.fixture');
assert.equal(element('app').options[1].text,'Fixture app');
console.log(JSON.stringify({dashboard_client:'passed',checks:7,scope:'isolated client logic, not a rendered browser'}));
