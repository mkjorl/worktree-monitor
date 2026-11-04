"""Check a relocated release with a fresh home and a fake local adapter."""
import fcntl,json,plistlib,re,select,shutil,socket,subprocess,sys,tempfile,time,threading
from pathlib import Path
from urllib.request import Request,urlopen

with tempfile.TemporaryDirectory(prefix='worktree-release-qa-',dir='/private/tmp') as folder:
 root=Path(folder); app=root/'Another Folder/Worktree Monitor.app'
 shutil.copytree(Path(sys.argv[1]).resolve(),app,symlinks=True)
 with (app/'Contents/Info.plist').open('rb') as source:info=plistlib.load(source)
 assert (app/'Contents/Frameworks/Sparkle.framework').exists()
 assert info.get('SURequireSignedFeed') is True and info.get('SUVerifyUpdateBeforeExtraction') is True
 assert info['SUFeedURL']=='https://github.com/mkjorl/worktree-monitor/releases/latest/download/appcast.xml'
 native=app/'Contents/MacOS'/info['CFBundleExecutable']
 build_versions=subprocess.check_output(['xcrun','vtool','-show-build',str(native)],text=True)
 minimums=re.findall(r'^\s*minos\s+(\S+)',build_versions,re.MULTILINE)
 assert minimums, f'No Mach-O deployment target found: {build_versions}'
 def version(value):
  parts=tuple(map(int,value.split('.')));return parts+(0,)*(3-len(parts))
 assert all(version(value)<=version(info['LSMinimumSystemVersion']) for value in minimums), f'Mach-O minimums {minimums} exceed manifest minimum {info["LSMinimumSystemVersion"]}'
 backend=app/'Contents/Resources/backend/worktree-backend'
 home=root/'Fresh Home';home.mkdir()
 bin_dir=root/'fixture-bin'
 env={'HOME':str(home),'PATH':str(bin_dir)+':/usr/bin:/bin:/usr/sbin:/sbin','TMPDIR':str(root),'DASHBOARD_PORT':'0'}
 subprocess.run([str(backend),'--self-check'],cwd=root,env=env,check=True)
 server=subprocess.Popen([str(backend)],cwd=root,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
 try:
  assert select.select([server.stdout],[],[],20)[0], 'Backend did not start'
  line=server.stdout.readline().strip();assert line.startswith('Workspace dashboard: http://127.0.0.1:'),line
  origin=line.removeprefix('Workspace dashboard: ')
  def request(path,body=None,token=''):
   headers={'Origin':origin,'X-CSRF-Token':token,'Content-Type':'application/json'}
   with urlopen(Request(origin+path,data=json.dumps(body).encode() if body is not None else None,headers=headers),timeout=20) as response:return json.load(response)
  settings=request('/api/settings');token=settings['csrf']
  assert settings['directories']==[] and settings['showpass_checkout']=='' and settings['docker_socket']==''
  assert settings['appearance']=='system'
  assert request('/api/settings',{'appearance':'dark'},token)['appearance']=='dark'
  with urlopen(origin+'/',timeout=5) as response: assert 'data-appearance="dark"' in response.read().decode()
  assert request('/api/herdr')['status']=='unavailable'
  assert request('/api/phone')['enabled'] is False
  assert request('/api/activity')=={'busy':False}
  with urlopen(origin+'/deck',timeout=5) as response:
   deck=response.read().decode();assert 'Chat Deck' in deck and 'orientation:landscape' in deck
  for asset in ['/deck/icon-180.png','/deck/icon-512.png','/deck/manifest.webmanifest']:
   with urlopen(origin+asset,timeout=5) as response:
    assert response.status==200 and response.read()
  herdr_folder=home/'.config/herdr';herdr_folder.mkdir(parents=True)
  with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as herdr:
   herdr.bind(str(herdr_folder/'herdr.sock'));herdr.listen(2)
   observed=[]
   def serve_herdr():
    for _ in range(7):
     connection,_=herdr.accept()
     with connection:
      message=json.loads(connection.makefile('rb').readline());observed.append(message['method'])
      if message['method']=='session.snapshot':
       payload={'type':'session_snapshot','snapshot':{'agents':[{'pane_id':'w1:p1','agent':'codex','agent_status':'blocked','workspace_id':'w1','tab_id':'w1:t1'},{'pane_id':'w1:p2','agent':'codex','agent_status':'done','workspace_id':'w1','tab_id':'w1:t1'},{'pane_id':'w1:p3','agent':'codex','agent_status':'working','workspace_id':'w1','tab_id':'w1:t1'}],'workspaces':[{'workspace_id':'w1','label':'Fixture workspace'}],'tabs':[{'tab_id':'w1:t1','label':'Fixture chat'}]}}
      else:payload={'type':'ok'}
      connection.sendall(json.dumps({'id':message['id'],'result':payload}).encode()+b'\n')
   herdr_worker=threading.Thread(target=serve_herdr,daemon=True);herdr_worker.start()
   status=request('/api/herdr');assert status['waiting']==1 and status['ready']==1
   assert len(status['all_chats'])==3 and status['all_chats'][1]['unread'] is True
   deck_status=request('/deck/api/status');assert len(deck_status['all_chats'])==3 and deck_status['csrf']==token
   assert status['chats'][0]['workspace']=='Fixture workspace'
   assert request('/api/herdr/focus',{'id':'w1:p1'},token)['message']=='Chat selected in Herdr.'
   assert request('/api/herdr/pin',{'id':'w1:p3','pinned':True},token)=={'pinned':True}
   pinned_status=request('/deck/api/status')
   assert next(c for c in pinned_status['all_chats'] if c['id']=='w1:p3')['pinned'] is True
   assert request('/deck/api/pin',{'id':'w1:p3','pinned':False},token)=={'pinned':False}
   assert not next(c for c in request('/api/herdr')['all_chats'] if c['id']=='w1:p3')['pinned']
   herdr_worker.join(timeout=5);assert not herdr_worker.is_alive()
   assert observed==['session.snapshot','session.snapshot','session.snapshot','agent.focus','session.snapshot','session.snapshot','session.snapshot']
  (herdr_folder/'herdr.sock').unlink()
  initial=request('/api');assert initial['workspaces']==[]
  assert initial['docker']['desktop']['actions']==[]
  bin_dir.mkdir();docker=bin_dir/'docker'
  shutil.copyfile(Path(__file__).parent/'fixtures/docker',docker);docker.chmod(0o755)
  containers=request('/api')['docker']['containers'];assert len(containers)==1
  container_id=containers[0]['id'];assert container_id=='a'*64
  def docker_action(scope,action):
   payload={'scope':scope,'action':action}
   if scope=='container':payload['id']=container_id
   job=request('/api/docker/action',payload,token)['job']
   for _ in range(100):
    outcome=request('/api/jobs/'+job)
    if outcome['state']!='running':break
    time.sleep(.05)
   assert outcome['state']=='done',outcome
  for action,state in [('pause','paused'),('unpause','running'),('restart','running'),('stop','exited'),('start','running')]:
   docker_action('container',action)
   assert request('/api')['docker']['containers'][0]['state']==state
  for action,state in [('stop','stopped'),('start','running'),('restart','running')]:
   docker_action('desktop',action)
   assert request('/api')['docker']['desktop']['state']==state
  projects=root/'Projects';repo=projects/'example';repo.mkdir(parents=True)
  subprocess.run(['/usr/bin/git','-c','core.hooksPath=/dev/null','init','--initial-branch=main',str(repo)],env=env,check=True,capture_output=True)
  request('/api/settings',{'directories':[str(projects)],'showpass_checkout':'','docker_socket':''},token)
  rows=request('/api')['workspaces'];assert len(rows)==1 and rows[0]['name']=='example' and not rows[0]['managed'],rows
  assert rows[0]['cpu_percent'] is None and not rows[0]['can_start']
  base=root/'Optional Checkout';tool=base/'scripts/workspace';tool.mkdir(parents=True)
  (base/'.git').mkdir();(tool/'cli').write_text('# fixture only\n')
  (tool/'lifecycle.py').write_text('''from contextlib import contextmanager
from pathlib import Path
import fcntl
from dotenv import dotenv_values
from xml.sax.saxutils import escape
from xml.etree.ElementTree import Element
ProjectError = RuntimeError
def validate_project_name(name):
    if name != "demo": raise ValueError("Unexpected fixture")
    return name
def state_root():
    root = Path.home() / "fixture-state"
    root.mkdir(exist_ok=True)
    return root
def load_all_states():
    return [{"name":"demo", "status":"running", "project_root":str(Path.home()/"outside/demo")}]
@contextmanager
def locked_project(name, blocking=True):
    with (state_root() / ("."+name+".operation.lock")).open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
        yield
def stop_state(state):
    with (Path.home()/"fixture-stop-called").open("a") as output: output.write("called\\n")
def stop_command(name):
    with locked_project(name): stop_state({})
def health_check(state):
    with (Path.home()/"fixture-reset-ready").open("w") as output: output.write("ready")
def start_project(state):
    health_check(state)
def restart_command(name):
    with locked_project(name):
        stop_state({})
        start_project({})
''')
  request('/api/settings',{'directories':[str(projects)],'showpass_checkout':str(base),'docker_socket':''},token)
  cmd=[str(backend),'--workspace-action','stop','demo','--operation-id','e'*32,'--checkout',str(base)]
  worker=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True,timeout=15)
  assert worker.returncode==0,worker.stderr
  assert (home/'fixture-stop-called').read_text()=='called\n'
  assert request('/api/jobs/'+'e'*32)['state']=='done'
  with (home/'fixture-state/.demo.operation.lock').open('r') as lock:
   fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
   cmd[5]='f'*32
   retry=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True,timeout=15)
   assert retry.returncode!=0 and 'already in progress' in retry.stderr,retry.stderr
  assert (home/'fixture-stop-called').read_text()=='called\n'
  cmd[2]='reset';cmd[5]='a'*32
  reset=subprocess.run(cmd,cwd=root,env=env,capture_output=True,text=True,timeout=15)
  assert reset.returncode==0,reset.stderr
  assert (home/'fixture-reset-ready').read_text()=='ready'
  assert (home/'fixture-stop-called').read_text()=='called\ncalled\n'
  assert request('/api/jobs/'+'a'*32)['state']=='done'
  assert request('/api')['workspaces'][0]['name']=='example'
  subprocess.run(['codesign','--verify','--deep','--strict',str(app)],check=True)
  print('Relocated release: setup, appearance, simulated Docker controls, Git discovery, optional imports, frozen worker, job recovery, duplicate-lock rejection, and service reset passed.')
 finally:
  server.terminate();server.wait(timeout=10)
