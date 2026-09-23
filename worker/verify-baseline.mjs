import fs from 'node:fs';import {execFileSync} from 'node:child_process';
const manifest=JSON.parse(fs.readFileSync('TEST_SERVER_BASELINE.json','utf8'));
const head=execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim();if(head!==manifest.sourceRevision)throw Error('Source revision changed');
const changed=execFileSync('git',['diff','--name-only'],{encoding:'utf8'}).trim().split(/\r?\n/).filter(Boolean);
for(const path of changed)if(!manifest.testDelta.some(allowed=>path===allowed||allowed.endsWith('/')&&path.startsWith(allowed)))throw Error('Unexpected delta: '+path);
if(!fs.existsSync('dist/index.html'))throw Error('Build missing');console.log('Baseline revision and scoped diff PASS');
