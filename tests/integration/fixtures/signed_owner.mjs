// Actual Node owner/runtime and UI observation parser, with fixture-only stdin API.
import { readFileSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { resolve } from 'node:path';
import { createInterface } from 'node:readline';
const config=JSON.parse(readFileSync(process.argv[2],'utf8'));
const {createShadeExecutionRuntime}=await import(pathToFileURL(resolve(config.ui_root,'src/lib/shades/executionServer.js')));
const {currentShadeObservation}=await import(pathToFileURL(resolve(config.ui_root,'src/lib/shades/observation.js')));
const runtime=createShadeExecutionRuntime({profilePath:config.profile,upstream:config.openhab,authorization:config.authorization});
const authority=runtime.owner.authenticateManual(config.manual_authorization);
async function read(path){const r=await fetch(config.openhab+'/rest'+path,{headers:{Authorization:config.authorization}});if(!r.ok)throw Error('fixture_read_unavailable');return r.json();}
for await(const line of createInterface({input:process.stdin})){
 try{
  const input=JSON.parse(line);let result;
  if(input.action==='manual')result=await runtime.owner.requestManual(input.request,authority);
  else if(input.action==='batch')result=await runtime.owner.requestManualBatch(input.request,authority);
  else if(input.action==='policy')result=await runtime.owner.synchronizePolicy();
  else if(input.action==='automatic'){try{await runtime.owner.requestAutomatic(input.request);result={rejected:false};}catch{result={rejected:true};}}
  else if(input.action==='observe'){
   const [rows,things]=await Promise.all([read('/items?fields=name,state'),read('/things')]);
   const states=Object.fromEntries(rows.map(row=>[row.name,row.state]));
   const providers=Object.fromEntries(things.map(row=>[row.UID,row.statusInfo]));
   result={observations:runtime.installation().slots.map(slot=>({slot:slot.number,...currentShadeObservation(slot,states,'live',Date.now(),providers)})),automaticReleased:runtime.installation().automaticReleased};
  }else throw Error('unsupported_fixture_action');
  console.log(JSON.stringify({ok:true,result}));
 }catch(error){console.log(JSON.stringify({ok:false,error:error.code??error.message}));}
}
