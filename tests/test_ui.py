import shutil
import subprocess
from pathlib import Path

import pytest


def run_ui(tmp_path, assertions):
    if not shutil.which('node'):
        pytest.skip('node missing')
    html = (Path(__file__).parents[1] / 'dialogue_tool/ui/index.html').read_text()
    script = html.split('<script>')[1].split('</script>')[0]
    harness = r'''
const vm=require('vm'), assert=require('assert');
const elements=new Map();
const context={console, assert, setTimeout:()=>{}, setInterval:()=>{},
 document:{getElementById:id=>{
  if(!elements.has(id))elements.set(id,{textContent:'',style:{},classList:{add(){},remove(){},toggle(){}}});
  return elements.get(id);
 },querySelectorAll:()=>[]},window:{addEventListener(){}}};
vm.createContext(context);vm.runInContext(SOURCE,context);
(async()=>{await vm.runInContext(ASSERTIONS,context)})().catch(e=>{console.error(e);process.exit(1)});
'''.replace('SOURCE', repr(script)).replace('ASSERTIONS', repr(assertions))
    path = tmp_path / 'ui-behavior.cjs'
    path.write_text(harness)
    result = subprocess.run(['node', str(path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_candidate_replaces_only_target_and_keeps_distinct_pair(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one',scan:{frames:[{id:1},{id:2},{id:3}]}},busy:false};
draft=[{id:'group',frame_a:1,frame_b:2,repaired_a:'old.png',repaired_b:'keep.png'}];
activeGroupId='group';targetSide='a';
api={save_groups:async()=>true};
renderGroups=()=>{};renderEditor=()=>{};renderFrames=()=>{};poll=async()=>{};
await useCandidate(2);
assert.equal(draft[0].frame_a,1,'same frame on both sides must be rejected');
await useCandidate(3);
assert.equal(draft[0].frame_a,3);
assert.equal(draft[0].frame_b,2);
assert.equal(draft[0].repaired_a,null,'replaced frame must not reuse old repair');
assert.equal(draft[0].repaired_b,'keep.png');
})()''')


def test_switching_group_waits_for_save_and_stays_on_failure(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};
draft=[{id:'first',prompt:'edited'},{id:'second',prompt:''}];
activeGroupId='first';markDirty();
api={save_groups:async()=>false};
renderGroups=()=>{};renderEditor=()=>{};renderFrames=()=>{};
await activateGroup('second');
assert.equal(activeGroupId,'first','failed save must preserve current editing context');
api={save_groups:async groups=>{assert.equal(groups[0].prompt,'edited');return true}};
await activateGroup('second');
assert.equal(activeGroupId,'second');
assert.equal(dirty,false);
})()''')


def test_remove_group_rebinds_editor_before_slow_or_failed_save(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};
draft=[{id:'first',prompt:'keep first'},{id:'second',prompt:'keep second'}];
activeGroupId='first';
let visibleGroup='first',finish;
renderGroups=()=>{};renderFrames=()=>{};renderEditor=()=>{visibleGroup=currentGroup()?.id};
api={save_groups:()=>new Promise(r=>{finish=r})};
const pending=removeGroup(0);
assert.equal(visibleGroup,activeGroupId,'visible editor must match the prompt write target during save');
finish(false);await pending;
assert.equal(visibleGroup,activeGroupId,'failed save must leave editor consistent with the draft');
})()''')


def test_stale_original_request_cannot_open_mask_after_frame_change(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};
draft=[{id:'first',frame_a:1,frame_b:2}];activeGroupId='first';
let finish,opened=false;
$('maskModal').classList.remove=()=>{opened=true};
globalThis.Image=class{set src(v){this.onload()}};paint=()=>{};
api={get_image:()=>new Promise(r=>{finish=r})};
const pending=openMask(0,'a');await Promise.resolve();
draft[0].frame_a=3;
finish({url:'test',width:100,height:100});await pending;
assert.equal(opened,false,'mask image must match the frame that repair will process');
})()''')


def test_project_transition_blocks_edits_to_the_old_editor(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};draft=[{id:'group',prompt:'keep'}];
activeGroupId='group';draftProjectId='one';
let finish;api={select_project:()=>new Promise(r=>{finish=r})};
applyBusy=()=>{};poll=async()=>{};
const pending=selectProject('two');await Promise.resolve();
editPrompt('wrong project edit');
assert.equal(draft[0].prompt,'keep','old editor must be locked until the new project is rendered');
finish();await pending;
})()''')


def test_manual_confirmation_moves_review_group_into_results(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={project:{id:'one'},busy:false};
draft=[{id:'group',needs_review:true,selected:false}];activeGroupId='group';groupFilter='review';
api={save_groups:async()=>true};renderGroups=()=>{};renderEditor=()=>{};renderFrames=()=>{};
await confirmGroup();
assert.equal(draft[0].needs_review,false);
assert.equal(draft[0].selected,true);
assert.equal(draft[0].validation_state,'confirmed');
assert.equal(groupFilter,'ready');
})()''')


def test_legacy_groups_are_not_labeled_as_second_verified(tmp_path):
    run_ui(tmp_path, "assert.equal(verificationLabel({needs_review:false}),'历史配组');assert.equal(verificationLabel({needs_review:false,validation_state:'ready'}),'二次检查通过')")


def test_in_progress_batch_updates_empty_workspace_without_rebuilding_editor(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
lastVersion=1;draft=[];activeGroupId=null;
const incoming={version:1,busy:true,message:'识别对话 5/160，已发现 1 组候选',progress:.2,error:null,
 project:{id:'one',groups:[],phase:'analyzing',scan:{frames:[]}}};
api={get_state:async()=>incoming};
render=()=>{throw new Error('status ticks must not rebuild the editor')};
await poll();
assert.ok($('emptyDescription').textContent.includes('5/160'),'running progress should be visible in the main workspace');
incoming.message='识别对话 6/160，已发现 2 组候选';await poll();
assert.ok($('emptyDescription').textContent.includes('6/160'));
})()''')


def test_opening_all_shots_is_read_only_and_does_not_start_analysis(tmp_path):
    run_ui(tmp_path, r'''(async()=>{
state={busy:false,project:{id:'full',scan:{shots:[{id:0,start:0,end:10,frame_ids:[0,1,2]},
 {id:1,start:10,end:20,frame_ids:[3,4,5]}],frames:[]}}};
draft=[{id:'keep',prompt:'do not touch'}];
const before=JSON.stringify(draft),read=[];
api={get_image:async id=>{read.push(id);return {url:'test'}}};
await openShots();
assert.equal(JSON.stringify(draft),before);
assert.ok($('shotsTitle').textContent.includes('2'));
assert.deepEqual(read,[1,4]);
})()''')


def test_typing_during_save_does_not_lose_latest_prompt(tmp_path):
    if not shutil.which('node'):
        pytest.skip('node missing')
    html = (Path(__file__).parents[1] / 'dialogue_tool/ui/index.html').read_text()
    script = html.split('<script>')[1].split('</script>')[0]
    harness = r'''
const vm=require('vm');
const context={console, setTimeout:()=>{}, setInterval:()=>{},
 document:{getElementById:()=>({textContent:'',classList:{add(){},remove(){}}})},
 window:{addEventListener(){}}};
vm.createContext(context);vm.runInContext(SOURCE,context);
vm.runInContext(`
state={project:{id:'one'}};draft=[{prompt:'old'}];markDirty();
let finish;const saved=[];
api={save_groups:groups=>{saved.push(JSON.stringify(groups));return new Promise(r=>{finish=r})}};
const pending=saveDraft();
draft[0].prompt='new';markDirty();
finish(true);
`,context);
setImmediate(async()=>{
 vm.runInContext('if(saved.length>1)finish(true)',context);
 await vm.runInContext('pending',context);
 const r=vm.runInContext('({dirty,saved})',context);
 if(r.dirty || !r.saved.some(s=>JSON.parse(s)[0].prompt==='new'))process.exit(2);
});
'''.replace('SOURCE', repr(script))
    path = tmp_path / 'save-race.cjs'
    path.write_text(harness)
    result = subprocess.run(['node', str(path)], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
