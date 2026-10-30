/** Execute the actual production benchmark entry without a WebGL context.
 * This is a startup/import regression, not a rendering-performance test. */
import assert from 'node:assert/strict';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import {resolve,dirname} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const manifest=JSON.parse(await readFile(resolve(root,'dist/.vite/manifest.json'),'utf8'));
const entry=manifest['benchmark.html'];assert(entry,'Production benchmark entry is missing');
const visited=new Set();const css=new Set();
function visit(key){if(visited.has(key))return;visited.add(key);const chunk=manifest[key];assert(chunk,`Missing chunk ${key}`);for(const file of chunk.css??[])css.add(file);for(const dependency of chunk.imports??[])visit(dependency);}
visit('benchmark.html');
assert(!visited.has('index.html'),'Benchmark transitively imports the studio entry');
assert.equal(css.size,0,'Benchmark inherits studio layout CSS');
const html=await readFile(resolve(root,'dist/benchmark.html'),'utf8');assert(!/rel="stylesheet"/.test(html),'Benchmark links studio CSS');
const nodes=new Map(['#canvas','#results','#start','#export','#mode','#duration'].map(key=>[key,{onclick:null,disabled:false,textContent:'',clientWidth:421,clientHeight:237,value:'',options:[]}]));
const mounts=[];
globalThis.document={querySelector:selector=>nodes.get(selector)??null,getElementById:id=>{mounts.push(id);return null;},createElement:()=>({relList:{supports:()=>true}})};
globalThis.window={addEventListener(){}};globalThis.location={search:''};
await import(pathToFileURL(resolve(root,'dist',entry.file)).href);
assert.equal(mounts.length,0,'Benchmark executed studio mounting code');
assert.equal(typeof nodes.get('#start').onclick,'function','Production benchmark Start handler did not initialize');
assert.equal(typeof nodes.get('#export').onclick,'function','Production benchmark export handler did not initialize');
const report={test:'production_benchmark_entry_startup',static_imports:[...visited],studio_entry_executed:false,studio_css_inherited:false,start_handler_bound:true,webgl_rendering_tested:false};
await mkdir(resolve(root,'reports'),{recursive:true});await writeFile(resolve(root,'reports/production-startup.json'),JSON.stringify(report,null,2)+'\n');
console.log('Production benchmark startup passed: isolated entry, no studio CSS, Start handler initialized.');
