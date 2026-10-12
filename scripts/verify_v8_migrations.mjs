// Run after npm install --no-save @electric-sql/pglite
// PostgreSQL WASM executes actual SQL; production multi-session locks are
// additionally covered by a real PostgreSQL integration test when configured.
import { PGlite } from '@electric-sql/pglite';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'../migrations');
const db=new PGlite();
await db.exec(`CREATE SCHEMA hefesto_product;
CREATE TABLE hefesto_product.schema_migrations(version text PRIMARY KEY, checksum char(64), applied_at timestamptz DEFAULT now());`);
const files=fs.readdirSync(root).filter(f=>f.endsWith('.sql')).sort();
for(const f of files){
  await db.exec(fs.readFileSync(path.join(root,f),'utf8'));
  console.log(`PASS migration ${f}`);
}
const expected=['app_credential','app_sessions','schema_migrations','uploads','v8_operations','v8_projects'];
async function checkTables(){
  const result=await db.query("SELECT tablename FROM pg_tables WHERE schemaname='hefesto_product' ORDER BY tablename");
  if(JSON.stringify(result.rows.map(r=>r.tablename))!==JSON.stringify(expected))throw Error('Unexpected schema');
}
await checkTables();
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
// Model a populated previous installation: retired tables and FK dependencies,
// including experimental matrix leftovers that still exist in production.
await db.exec(`CREATE TABLE hefesto_product.runs(
  execution_run_id uuid PRIMARY KEY);
INSERT INTO hefesto_product.runs VALUES('11111111-1111-4111-8111-111111111111');
CREATE TABLE hefesto_product.paragraphs(
  execution_run_id uuid NOT NULL,
  paragraph_id char(64) NOT NULL,
  PRIMARY KEY (execution_run_id, paragraph_id),
  FOREIGN KEY (execution_run_id) REFERENCES hefesto_product.runs(execution_run_id));
INSERT INTO hefesto_product.paragraphs VALUES('11111111-1111-4111-8111-111111111111','p1');
CREATE TABLE hefesto_product.sentences(
  execution_run_id uuid NOT NULL,
  paragraph_id char(64) NOT NULL,
  sentence_id integer NOT NULL,
  FOREIGN KEY (execution_run_id, paragraph_id)
    REFERENCES hefesto_product.paragraphs(execution_run_id, paragraph_id));
INSERT INTO hefesto_product.sentences VALUES('11111111-1111-4111-8111-111111111111','p1',1);
CREATE TABLE hefesto_product.editorial_matrix_batches(
  execution_run_id uuid NOT NULL,
  batch_id char(64) NOT NULL,
  PRIMARY KEY (execution_run_id, batch_id),
  FOREIGN KEY (execution_run_id) REFERENCES hefesto_product.runs(execution_run_id));
INSERT INTO hefesto_product.editorial_matrix_batches VALUES('11111111-1111-4111-8111-111111111111','b1');
CREATE TABLE hefesto_product.editorial_matrix_coverage(
  execution_run_id uuid NOT NULL,
  batch_id char(64) NOT NULL,
  passage_id char(64) NOT NULL,
  paragraph_id char(64) NOT NULL,
  PRIMARY KEY (execution_run_id, batch_id, passage_id),
  FOREIGN KEY (execution_run_id, batch_id)
    REFERENCES hefesto_product.editorial_matrix_batches(execution_run_id, batch_id),
  FOREIGN KEY (execution_run_id, paragraph_id)
    REFERENCES hefesto_product.paragraphs(execution_run_id, paragraph_id));
INSERT INTO hefesto_product.editorial_matrix_coverage
  VALUES('11111111-1111-4111-8111-111111111111','b1','pass1','p1');
CREATE TABLE hefesto_product.editorial_claims(
  execution_run_id uuid NOT NULL,
  claim_id char(64) NOT NULL,
  PRIMARY KEY (execution_run_id, claim_id));
INSERT INTO hefesto_product.editorial_claims VALUES('11111111-1111-4111-8111-111111111111','c1');
CREATE TABLE hefesto_product.editorial_claim_evidence(
  execution_run_id uuid NOT NULL,
  claim_id char(64) NOT NULL,
  passage_id char(64) NOT NULL,
  paragraph_id char(64) NOT NULL,
  PRIMARY KEY (execution_run_id, claim_id, passage_id),
  FOREIGN KEY (execution_run_id, claim_id)
    REFERENCES hefesto_product.editorial_claims(execution_run_id, claim_id),
  FOREIGN KEY (execution_run_id, paragraph_id)
    REFERENCES hefesto_product.paragraphs(execution_run_id, paragraph_id));
INSERT INTO hefesto_product.editorial_claim_evidence
  VALUES('11111111-1111-4111-8111-111111111111','c1','pass1','p1');
CREATE TABLE hefesto_product.editorial_matrix_work(
  execution_run_id uuid NOT NULL REFERENCES hefesto_product.runs(execution_run_id),
  unit_id char(64) NOT NULL,
  PRIMARY KEY (execution_run_id, unit_id));
INSERT INTO hefesto_product.editorial_matrix_work
  VALUES('11111111-1111-4111-8111-111111111111','u1');
INSERT INTO hefesto_product.app_sessions(token_hash,owner_id,expires_at)
  VALUES('preserved','u',now()+interval '1 day');`);
for(const f of files)await db.exec(fs.readFileSync(path.join(root,f),'utf8'));
await checkTables();
if((await db.query('SELECT * FROM hefesto_product.app_sessions')).rows.length!==1)throw Error('Session lost');
if((await db.query('SELECT * FROM hefesto_product.v8_projects')).rows.length!==1)throw Error('Project lost');
if((await db.query('SELECT payload FROM hefesto_product.v8_operations')).rows[0].payload.raw!=='preserved malformed {')throw Error('Journal lost');
console.log('PASS fresh install, populated retirement, idempotence, FK, revision and journal/session preservation');
await db.close();
