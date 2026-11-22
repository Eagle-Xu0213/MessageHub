const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const style = {setProperty(){}};
const context = {window:{addEventListener(){}}, document:{documentElement:{style},addEventListener(){},hidden:false}, localStorage:{getItem(){return null}}, location:{hostname:'test',search:''}, URLSearchParams, Date, setInterval(){}};
vm.runInNewContext(fs.readFileSync('src/messagehub/static/ambient.js','utf8'), context);
const calculate=context.window.MessageHubAmbient.calculate;
for(const boundary of [0,6,10,14,17,20,24]){
 const left=calculate(boundary-1/60),right=calculate(boundary+1/60);
 assert(Math.abs(left.x-right.x)<.1);
 assert(left.first.every((v,i)=>Math.abs(v-right.first[i])<=1));
 assert(left.second.every((v,i)=>Math.abs(v-right.second[i])<=1));
}
assert.deepStrictEqual(JSON.stringify(calculate(0)),JSON.stringify(calculate(24)));
console.log('Ambient interpolation: 8 checks passed');
