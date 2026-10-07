// Run after npm install --no-save @electric-sql/pglite @electric-sql/pglite-pgvector
// PostgreSQL WASM executes actual SQL; production multi-session locks are
// additionally covered by a real PostgreSQL integration test when configured.
import { PGlite } from '@electric-sql/pglite';
import { vector } from '@electric-sql/pglite-pgvector';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../migrations');
const db=new PGlite({extensions:{vector}});
for(const f of fs.readdirSync(root).filter(f=>f.endsWith('.sql')).sort()){
  await db.exec(fs.readFileSync(path.join(root,f),'utf8'));
  console.log(`PASS migration ${f}`);
}
await db.exec(fs.readFileSync(path.join(root,'1008_hefesto_v8.sql'),'utf8'));
const id='11111111-1111-4111-8111-111111111111';
await db.query('INSERT INTO hefesto_product.v8_projects(execution_id,owner_id,status,payload) VALUES($1,$2,$3,$4::jsonb)',[id,'u','queued','{}']);
await db.query('INSERT INTO hefesto_product.v8_operations(execution_id,operation_key,payload) VALUES($1,$2,$3::jsonb)',[id,'a'.repeat(64),JSON.stringify({raw:'preserved malformed {',complete:false})]);
const read=await db.query('SELECT payload FROM hefesto_product.v8_operations');
if(read.rows[0].payload.raw!=='preserved malformed {')throw Error('Raw response lost');
const updated=await db.query('UPDATE hefesto_product.v8_projects SET revision=1 WHERE execution_id=$1 AND revision=0 RETURNING revision',[id]);
const stale=await db.query('UPDATE hefesto_product.v8_projects SET revision=2 WHERE execution_id=$1 AND revision=0 RETURNING revision',[id]);
if(updated.rows.length!==1||stale.rows.length!==0)throw Error('Revision CAS failed');
try{await db.query('INSERT INTO hefesto_product.v8_operations VALUES($1,$2,$3::jsonb,now())',['22222222-2222-4222-8222-222222222222','b'.repeat(64),'{}']);throw Error('Missing FK accepted');}catch(e){if(!String(e).includes('foreign key'))throw e;}
console.log('PASS additive migration idempotence, FK, stale revision and raw journal persistence');
await db.close();
