// Offline browser smoke. Requires playwright + Chromium; all HTTP/model paths
// are fixtures, no provider credentials or editorial calls are used.
const {chromium}=require('playwright');
const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1024,height:768}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let auth=false,submission=null,submissions=[],approvals=0,approvalReads=0;
  const fields=central_idea=>({central_idea,narrative_development:'Develop a contrast through explanation and an objection.',intended_conclusion:'Reach a qualified understanding.'});
  let state={execution_id:'11111111-1111-4111-8111-111111111111',status:'queued',projects:[{version:1,fields:fields('Central question, narrative movement and an ending.'),planning_id:'planning-one',planning_version:1}],sources:[{source_id:'source-a',title:'Generic original',tokens:100,locations:[{location_id:'p1',page:1}]}],usage:{call_count:1,input_tokens:100,output_tokens:20,cost_usd:'0.001',latency_ms:100}};
  await page.route('http://localhost/**',async route=>{
    const url=new URL(route.request().url());const method=route.request().method();
    const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
    if(url.pathname==='/v8')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../src/comunicamundo_v4/web/v8.html'),'utf8')});
    if(url.pathname.startsWith('/assets/'))return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(path.join(__dirname,'../src/comunicamundo_v4/web',url.pathname),'utf8')});
    if(url.pathname==='/v1/me')return reply(auth?{username:'fixture'}:{detail:'login required'},auth?200:401);
    if(url.pathname==='/v1/login'){auth=true;return reply({username:'fixture'});}
    if(url.pathname==='/v8/projects'&&method==='GET')return reply({projects:[]});
    if(url.pathname==='/v8/projects'&&method==='POST'){submission=route.request().postDataJSON();submissions.push(submission);if(submissions.length===1)return reply({detail:'Temporary response interruption'},503);assert.deepEqual(submissions[0],submission);return reply(state,202);}
    if(method==='POST'){
      if(url.pathname.endsWith('/resume')){state.status='project_proposed';return reply(state,202);}
      const data=route.request().postDataJSON();assert.equal(data.version,state.projects.at(-1).version);
      if(url.pathname.endsWith('/revise')){assert.ok(data.message);state.projects.push({version:2,fields:fields('A reconsidered perspective.'),planning_id:'planning-two',planning_version:2});state.status='project_proposed';}
      if(url.pathname.endsWith('/approve')){approvals++;state.projects.at(-1).approved_at='now';state.status='writing';approvalReads=0;}
      assert.equal(url.pathname.endsWith('/produce'),false);
      return reply(state,202);
    }
    if(state.status==='writing'&&approvalReads++>0){state.status='text_ready';state.work={title:'Generic work',body:'A grounded statement.',support:[{claim:'A description of the documented idea.',references:[{source_id:'source-a',location_id:'p1'}]}]};}
    return reply(state);
  });
  await page.goto('http://localhost/v8');
  await page.locator('[name=username]').fill('fixture');await page.locator('[name=password]').fill('fixture');await page.getByRole('button',{name:'Entrar',exact:true}).click();
  await page.locator('#workspace').waitFor({state:'visible'});
  await page.locator('[name=theme]').fill('Generic subject');await page.locator('[name=approach]').fill('Explain a contrast');await page.locator('[name=audience]').fill('General audience');await page.locator('#source-mode').selectOption('search');
  await page.getByRole('button',{name:'Preparar Projeto Editorial',exact:true}).click();
  await page.waitForFunction(()=>document.querySelector('#error').textContent==='Temporary response interruption');
  await page.getByRole('button',{name:'Preparar Projeto Editorial',exact:true}).click();
  await page.locator('#resume').waitFor({state:'visible'});await page.locator('#resume').click();
  await page.locator('#proposal').waitFor({state:'visible'});assert.deepEqual(submission.sources,[]);
  await page.locator('#message').fill('Reconsider the central question.');await page.locator('#revise').click();
  await page.waitForFunction(()=>document.querySelector('#central-idea').textContent==='A reconsidered perspective.');
  await page.locator('#versions').selectOption('1');assert.equal(await page.locator('#approve').isDisabled(),true);
  await page.locator('#versions').selectOption('2');await page.locator('#approve').click();await page.locator('#status').getByText('Escrevendo a obra').waitFor();assert.equal(await page.locator('#produce').count(),0);
  await page.locator('#work').waitFor({state:'visible'});assert.equal(await page.locator('#body').textContent(),'A grounded statement.');
  assert.match(await page.locator('#support').textContent(),/página 1/);assert.deepEqual(errors,[]);assert.equal(approvals,1);assert.equal(await page.locator('#canonical-proposal h4').count(),3);assert.equal(await page.getByText('Internal planning secret').count(),0);
  await page.setViewportSize({width:768,height:1024});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  console.log('PASS V8 browser login, zero sources, idempotent submission retry, restart resume, PE versions, remoque, single approval with automatic production, three-field PE, grounding and iPad viewport; zero model calls');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
