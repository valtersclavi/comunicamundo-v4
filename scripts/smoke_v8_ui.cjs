// Offline browser smoke. Requires playwright + Chromium; all HTTP/model paths
// are fixtures, no provider credentials or editorial calls are used.
const {chromium}=require('playwright');
const fs=require('node:fs');const path=require('node:path');const assert=require('node:assert/strict');
(async()=>{
  const browser=await chromium.launch({headless:true});
  const page=await browser.newPage({viewport:{width:1024,height:768}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  let auth=false,submission=null;
  let state={execution_id:'11111111-1111-4111-8111-111111111111',status:'queued',projects:[{version:1,document:'Central question, narrative movement and an ending.'}],sources:[{source_id:'source-a',title:'Generic original',tokens:100,locations:[{location_id:'p1',page:1}]}],usage:{call_count:1,input_tokens:100,output_tokens:20,cost_usd:'0.001',latency_ms:100}};
  await page.route('http://localhost/**',async route=>{
    const url=new URL(route.request().url());const method=route.request().method();
    const reply=(data,status=200)=>route.fulfill({status,contentType:'application/json',body:JSON.stringify(data)});
    if(url.pathname==='/v8')return route.fulfill({contentType:'text/html',body:fs.readFileSync(path.join(__dirname,'../src/comunicamundo_v4/web/v8.html'),'utf8')});
    if(url.pathname.startsWith('/assets/'))return route.fulfill({contentType:url.pathname.endsWith('.js')?'text/javascript':'text/css',body:fs.readFileSync(path.join(__dirname,'../src/comunicamundo_v4/web',url.pathname),'utf8')});
    if(url.pathname==='/v1/me')return reply(auth?{username:'fixture'}:{detail:'login required'},auth?200:401);
    if(url.pathname==='/v1/login'){auth=true;return reply({username:'fixture'});}
    if(url.pathname==='/v8/projects'&&method==='GET')return reply({projects:[]});
    if(url.pathname==='/v8/projects'&&method==='POST'){submission=route.request().postDataJSON();return reply(state,202);}
    if(method==='POST'){
      if(url.pathname.endsWith('/resume')){state.status='project_proposed';return reply(state,202);}
      const data=route.request().postDataJSON();assert.equal(data.version,state.projects.at(-1).version);
      if(url.pathname.endsWith('/revise')){assert.ok(data.message);state.projects.push({version:2,document:'A reconsidered perspective.'});state.status='project_proposed';}
      if(url.pathname.endsWith('/approve')){state.projects.at(-1).approved_at='now';state.status='project_approved';}
      if(url.pathname.endsWith('/produce')){state.status='text_ready';state.work={title:'Generic work',body:'A grounded statement.',support:[{claim:'A grounded statement.',references:[{source_id:'source-a',location_id:'p1'}]}]};}
      return reply(state,202);
    }
    return reply(state);
  });
  await page.goto('http://localhost/v8');
  await page.locator('[name=username]').fill('fixture');await page.locator('[name=password]').fill('fixture');await page.getByRole('button',{name:'Entrar',exact:true}).click();
  await page.locator('#workspace').waitFor({state:'visible'});
  await page.locator('[name=theme]').fill('Generic subject');await page.locator('[name=approach]').fill('Explain a contrast');await page.locator('[name=audience]').fill('General audience');await page.locator('#source-mode').selectOption('search');
  await page.getByRole('button',{name:'Preparar Projeto Editorial',exact:true}).click();
  await page.locator('#resume').waitFor({state:'visible'});await page.locator('#resume').click();
  await page.locator('#proposal').waitFor({state:'visible'});assert.deepEqual(submission.sources,[]);
  await page.locator('#message').fill('Reconsider the central question.');await page.locator('#revise').click();
  await page.waitForFunction(()=>document.querySelector('#proposal').textContent==='A reconsidered perspective.');
  await page.locator('#versions').selectOption('1');assert.equal(await page.locator('#approve').isDisabled(),true);
  await page.locator('#versions').selectOption('2');await page.locator('#approve').click();await page.locator('#produce').waitFor({state:'visible'});await page.locator('#produce').click();
  await page.locator('#work').waitFor({state:'visible'});assert.equal(await page.locator('#body').textContent(),'A grounded statement.');
  assert.match(await page.locator('#support').textContent(),/página 1/);assert.deepEqual(errors,[]);
  await page.setViewportSize({width:768,height:1024});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
  console.log('PASS V8 browser login, zero sources, restart resume, PE versions, remoque, approval, production, grounding and iPad viewport; zero model calls');
  await browser.close();
})().catch(e=>{console.error(e);process.exit(1)});
