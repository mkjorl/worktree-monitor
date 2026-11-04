const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const html=fs.readFileSync(new URL('../index.html',`file://${__filename}`),'utf8');
const source=html.slice(html.indexOf('const phoneToggle='),html.indexOf("document.querySelector('#open-browser').addEventListener"));
const elements=new Map(),events={},sent=[];
function element(selector){if(!elements.has(selector))elements.set(selector,{value:'',disabled:false,hidden:false,textContent:'',handlers:{},classList:{toggle(){}},addEventListener(type,callback){this.handlers[type]=callback},select(){this.selected=true}});return elements.get(selector)}
const state={csrf:'token',document:{querySelector:element},window:{webkit:{messageHandlers:{app:{}}},addEventListener(type,callback){events[type]=callback}},nativePost:message=>sent.push(message),async fetch(url,options){state.request={url,options};return {ok:true,json:async()=>({enabled:true,pairing_url:'http://127.0.0.1:123/deck#pair=test',csrf:'new'})}},navigator:{clipboard:{async writeText(text){state.copied=text}}}};
vm.createContext(state);vm.runInContext(source,state);
(async()=>{
 await state.loadPhoneAccess();assert.equal(state.request.url,'/api/phone');assert.equal(element('#phone-toggle').textContent,'Turn off phone access');assert.equal(element('#phone-qr').disabled,false);
 await state.changePhoneAccess('pair');assert.deepEqual(JSON.parse(state.request.options.body),{action:'pair'});assert.equal(state.request.options.headers['X-CSRF-Token'],'new');
 element('#phone-qr').handlers.click();assert.equal(sent.at(-1).action,'showPhonePairing');
 await element('#phone-copy').handlers.click();assert.match(state.copied,/#pair=test$/);
 let prevented=false;element('#deck-preview').handlers.click({preventDefault(){prevented=true}});assert.ok(prevented);assert.equal(sent.at(-1).action,'openDeck');
 state.fetch=async()=>({ok:false,json:async()=>({error:'No network'})});await state.changePhoneAccess('enable');assert.equal(element('#phone-status').textContent,'No network');assert.equal(element('#phone-toggle').disabled,false);
 state.applyPhoneAccess({enabled:false});assert.equal(element('#phone-link').value,'');assert.equal(element('#phone-link-row').hidden,true);assert.equal(element('#phone-qr').disabled,true);
 console.log('Phone settings tests passed');
})().catch(error=>{console.error(error);process.exitCode=1});
