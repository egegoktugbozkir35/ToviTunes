// Offline behavioral checks for the existing lightweight frontend; no browser dependency.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const nodes = new Map();
const node = selector => {
  if (!nodes.has(selector)) nodes.set(selector, {
    innerHTML:"", textContent:"", value:"", hidden:false, disabled:false,
    classList:{toggle(){}}, setAttribute(){},
  });
  return nodes.get(selector);
};
const buttons = [node("draft"), node("render"), node("publish")];
let requests = [], respond;
const context = vm.createContext({
  console, setTimeout, clearTimeout, AbortSignal, Intl, Date,
  document:{
    querySelector:node,
    querySelectorAll:selector => selector.startsWith("[data-create]") ? buttons : [],
  },
  fetch:(path, options) => {
    requests.push(path);
    assert.ok(options.signal, "all requests must have a bounded response wait");
    return respond(path);
  },
  assert,
});
const script = fs.readFileSync("src/tovitunes/web/static/app.js", "utf8");
vm.runInContext(script.slice(0, script.indexOf('document.addEventListener("click"')), context);
const run = source => vm.runInContext(source, context);
const response = payload => ({ok:true, headers:{get:()=>"application/json"}, json:async()=>payload});

(async () => {
  let release;
  respond = () => new Promise(resolve => { release = resolve; });
  const first = run("refreshJobs()");
  const second = run("refreshJobs()");
  assert.equal(first, second, "overlapping refreshes must coalesce");
  release(response([{job_id:"retained", operation:"studio", status:"running", target:"render",
    episode_key:"original-episode", execution:2, current_stage:"VISUAL_ASSETS",
    completed_steps:8, steps:["CREATIVE","VISUAL_ASSETS"], progress_percent:60}]));
  await first;
  assert.deepEqual(requests, ["/api/jobs"], "no duplicate single-job projection");
  assert.match(node("#current-job").innerHTML, /original-episode/);
  assert.match(node("#current-job").innerHTML, /Execution 2/);
  assert.match(node("#current-job").innerHTML, /no provider percentage/);
  assert.ok(buttons.every(button => button.disabled));

  const retained = node("#current-job").innerHTML;
  respond = () => Promise.reject(new Error("temporary disconnect"));
  await assert.rejects(run("refreshJobs()"));
  assert.equal(node("#current-job").innerHTML, retained);
  assert.equal(node("#job-connection").hidden, false);
  assert.ok(buttons.every(button => button.disabled));

  respond = () => Promise.resolve(response([]));
  await run("refreshJobs()");
  assert.equal(node("#job-connection").hidden, true);
  assert.ok(buttons.every(button => !button.disabled));
  assert.equal(node("#sidebar-status").textContent, "Studio API reachable");

  run(`state.videos = [{episode_key:"saved", title:"Saved title", category:"renders",
    render_ready:true, draft_ready:true, technical_qa:"passed", publication:{outcome:"not_published"},
    video_url:"/api/media/valid-artifact"}]; renderVideos();`);
  assert.match(node("#videos-body").innerHTML, /Completed render/);
  assert.match(node("#videos-body").innerHTML, /Technical QA: Passed/);
  assert.match(node("#videos-body").innerHTML, /data-watch="saved"/);
  assert.match(node("#videos-body").innerHTML, /Not Published/);
  console.log("Studio frontend behavioral checks passed");
})().catch(error => { console.error(error); process.exitCode = 1; });
