const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor(tag='div') {this.tagName=tag;this.children=[];this.listeners={};this.value='';this.checked=true;this.dataset={};this.classList={toggle(){}};this._html='';}
  set innerHTML(v){this._html=v;this.children=[];} get innerHTML(){return this._html;}
  appendChild(x){this.children.push(x);return x;}
  replaceChildren(...xs){this.children=xs;this._html='';}
  addEventListener(e,fn){this.listeners[e]=fn;}
  querySelectorAll(){return [];}
  reset(){} scrollIntoView(){}
  closest(){return this;}
  fire(e,extra={}){return this.listeners[e]({preventDefault(){},...extra});}
}
(async()=>{
 const elements={}; const el=id=>elements[id]||(elements[id]=new Element());
 const storage=new Map([['smokebot-discord-access-token-v2','synthetic']]);
 const calls=[]; let pending;
 const harmless='<b>format marker</b>';
 const triggers={[harmless]:{event:'message',pattern:harmless,code:'pass',channel_ids:['123456789012345678']}};
 const fetch=async(url,opts={})=>{calls.push({url,opts});if(pending&&url.endsWith('/2')) return new Promise(r=>{pending.resolve=r;});return {ok:true,json:async()=>url.includes('users/@me')?{username:'synthetic'}:url.endsWith('/guilds')?{guilds:[{id:'1',name:'A'},{id:'2',name:'B'}]}:{triggers}};};
 let ready;const context={document:{getElementById:el,createElement:t=>new Element(t),addEventListener:(e,fn)=>{ready=fn;}},window:{location:{origin:'https://example.com',pathname:'/scripts/',hash:''},confirm:()=>true},localStorage:{getItem:k=>storage.get(k)||null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)},fetch,URL,URLSearchParams,history:{replaceState(){}},console};
 vm.runInNewContext(fs.readFileSync('docs/assets/js/script-manager.js','utf8'),context);ready();
 const tick=()=>new Promise(r=>setImmediate(r));await tick();await tick();
 const list=el('script-list');
 assert.equal(list.children.length,1);
 assert(!JSON.stringify(list.children).includes('data-name=\"'+harmless),'metadata entered HTML/attribute markup');
 const walk=n=>[n,...n.children.flatMap(walk)];
 assert(walk(list).some(n=>n.textContent===harmless),'script title must be a text node');
 assert(walk(list).every(n=>!n.innerHTML.includes(harmless)),'metadata must never enter innerHTML');
 await list.fire('click',{target:{closest:()=>({dataset:{action:'edit',name:harmless}})}});
 el('channel_ids').value='123456789012345678';el('name').value='test';el('pattern').value='hi';el('code').value='pass';
 await el('script-form').fire('submit');
 const put=calls.find(c=>c.opts.method==='PUT');assert.deepEqual(JSON.parse(put.opts.body).channel_ids,['123456789012345678']);
 el('guild_id').value='2';await el('guild_id').fire('change');
 const before=calls.filter(c=>c.opts.method==='PUT'||c.opts.method==='DELETE').length;
 el('name').value='stale';await el('script-form').fire('submit');
 assert.equal(calls.filter(c=>c.opts.method==='PUT'||c.opts.method==='DELETE').length,before,'switching guild must block stale save');
 pending={};
 const loading=el('api-config-form').fire('submit');
 await tick();
 el('guild_id').value='1';await el('guild_id').fire('change');
 pending.resolve({ok:true,json:async()=>({triggers:{stale:{code:'old'}}})});
 await loading;
 assert.equal(list.children.length,0,'late server response must not refill the new workspace');
 el('quick_template').value='welcome_message';await el('quick_template').fire('change');
 assert(el('code').value.includes('channel_id=WELCOME_CHANNEL_ID'));
 assert(!el('code').value.includes('send_in_thread'));
 console.log('script manager DOM regressions passed');
})().catch(e=>{console.error(e);process.exitCode=1;});
