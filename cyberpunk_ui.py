# Cyberpunk UI template for trading system
# This replaces HTML_USER in web_ui.py

HTML_USER = r"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>量化交易系统</title>
<script src="/chart.js"></script>
<style>
:root {
  --bg: #060606; --card: #0d0d0d; --border: rgba(0,255,65,0.12);
  --text: #999; --title: #e0e0e0; --accent: #00ff41; --green: #00ff41;
  --red: #ff4466; --orange: #ff9500; --glow: 0 0 12px rgba(0,255,65,0.08);
}
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family: 'JetBrains Mono','Consolas','Microsoft YaHei',monospace; background:var(--bg); color:var(--text); display:flex; flex-direction:column; height:100vh; overflow:hidden; }
/* Top Nav */
.topnav { display:flex; align-items:center; padding:0 20px; height:48px; background:#080808; border-bottom:1px solid var(--border); z-index:100; flex-shrink:0; }
.topnav .logo { font-size:15px; font-weight:bold; color:var(--accent); margin-right:24px; text-shadow:0 0 10px rgba(0,255,65,0.3); }
.topnav a { color:var(--text); text-decoration:none; font-size:12px; padding:0 14px; height:48px; display:flex; align-items:center; border-bottom:2px solid transparent; transition:all .2s; }
.topnav a:hover, .topnav a.active { color:var(--accent); border-bottom-color:var(--accent); }
.topnav .spacer { flex:1; }
.topnav .user-area { display:flex; align-items:center; gap:10px; cursor:pointer; position:relative; }
.topnav .user-area img { width:30px; height:30px; border-radius:50%; border:1px solid var(--accent); object-fit:cover; }
.topnav .user-area .nick { font-size:12px; color:var(--title); }
.topnav .user-area:hover .nick { color:var(--accent); }
.user-dropdown { display:none; position:absolute; top:42px; right:0; background:var(--card); border:1px solid var(--accent); border-radius:6px; min-width:150px; z-index:200; box-shadow:0 0 20px rgba(0,255,65,0.1); }
.user-dropdown.show { display:block; }
.user-dropdown a { display:block; padding:10px 16px; font-size:11px; color:var(--text); text-decoration:none; border:none; height:auto; }
.user-dropdown a:hover { background:rgba(0,255,65,0.05); color:var(--accent); border:none; }
/* Content */
.content { flex:1; overflow-y:auto; padding:20px; }
/* Cards */
.stat-cards { display:grid; grid-template-columns:repeat(auto-fill,minmax(180px,1fr)); gap:10px; margin-bottom:16px; }
.stat-card { background:var(--card); border:1px solid var(--border); border-radius:6px; padding:12px 14px; box-shadow:var(--glow); transition:all .2s; }
.stat-card:hover { border-color:var(--accent); box-shadow:0 0 20px rgba(0,255,65,0.15); }
.stat-card .label { font-size:10px; color:var(--text); margin-bottom:4px; text-transform:uppercase; letter-spacing:1px; }
.stat-card .value { font-size:22px; font-weight:bold; color:var(--title); }
.stat-card .sub { font-size:10px; margin-top:2px; }
.green { color:var(--green); } .red { color:var(--red); }
.card { background:var(--card); border:1px solid var(--border); border-radius:6px; padding:16px; margin-bottom:16px; box-shadow:var(--glow); }
.card h3 { font-size:13px; color:var(--accent); margin-bottom:12px; text-transform:uppercase; letter-spacing:2px; }
.card h3::before { content:'> '; opacity:.5; }
.grid2 { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
@media(max-width:900px){.grid2{grid-template-columns:1fr}}
table { width:100%; border-collapse:collapse; font-size:11px; }
th { background:#0f0f0f; color:var(--accent); padding:8px 10px; text-align:left; font-weight:500; text-transform:uppercase; letter-spacing:1px; border-bottom:1px solid var(--border); }
td { padding:7px 10px; border-bottom:1px solid rgba(255,255,255,.03); }
tr:hover td { background:rgba(0,255,65,.02); }
input,select { width:100%; padding:8px 10px; background:#0a0a0a; border:1px solid var(--border); border-radius:4px; color:var(--title); font-size:12px; font-family:inherit; }
input:focus,select:focus { outline:none; border-color:var(--accent); box-shadow:0 0 8px rgba(0,255,65,.1); }
label { display:block; font-size:11px; color:var(--text); margin-bottom:4px; margin-top:12px; text-transform:uppercase; letter-spacing:1px; }
.btn { padding:8px 16px; border:none; border-radius:4px; font-size:12px; cursor:pointer; font-weight:500; font-family:inherit; transition:all .2s; }
.btn-primary { background:var(--accent); color:#000; text-shadow:none; }
.btn-primary:hover { box-shadow:0 0 16px rgba(0,255,65,.3); }
.btn-green { background:var(--green); color:#000; }
.btn-red { background:var(--red); color:#fff; }
.btn-outline { background:transparent; border:1px solid var(--border); color:var(--text); }
.btn-outline:hover { border-color:var(--accent); color:var(--accent); }
.btn-sm { padding:4px 10px; font-size:10px; }
.btn:hover { opacity:.9; }
.form-row { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.tag { display:inline-block; padding:2px 8px; border-radius:2px; font-size:10px; margin:1px; text-transform:uppercase; }
.tag-info { background:rgba(0,255,65,.1); color:var(--accent); border:1px solid rgba(0,255,65,.2); }
.tag-success { background:rgba(0,255,65,.1); color:var(--green); }
.tag-warn { background:rgba(255,149,0,.1); color:var(--orange); }
.tag-error { background:rgba(255,68,102,.1); color:var(--red); }
.log-line { font-family:'JetBrains Mono','Consolas',monospace; font-size:10px; padding:3px 0; border-bottom:1px solid rgba(255,255,255,.02); line-height:1.5; }
.chart-wrap { position:relative; height:280px; }
.chart-wrap canvas { width:100%!important; height:100%!important; }
.inline-input { display:flex; gap:6px; align-items:center; }
.inline-input input { flex:1; }
::-webkit-scrollbar { width:4px; }
::-webkit-scrollbar-track { background:var(--bg); }
::-webkit-scrollbar-thumb { background:#1a1a1a; border-radius:2px; }
.watch-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(160px,1fr)); gap:8px; margin-bottom:16px; }
.watch-card { background:var(--card); border:1px solid var(--border); border-radius:6px; padding:10px 12px; box-shadow:var(--glow); }
.watch-card .pair { font-size:12px; color:var(--accent); font-weight:bold; text-transform:uppercase; }
.watch-card .price { font-size:18px; color:var(--title); margin:4px 0; }
.watch-card .change { font-size:10px; }
.watch-card .remove { float:right; color:var(--red); cursor:pointer; font-size:14px; opacity:.5; }
.watch-card .remove:hover { opacity:1; }
.modal-overlay { display:none; position:fixed; top:0; left:0; width:100%; height:100%; background:rgba(0,0,0,.85); z-index:1000; align-items:center; justify-content:center; }
.modal-overlay.show { display:flex; }
.modal { background:var(--card); border:1px solid var(--accent); border-radius:8px; padding:24px; width:380px; max-width:90vw; box-shadow:0 0 30px rgba(0,255,65,0.1); }
.modal h3 { font-size:14px; color:var(--accent); margin-bottom:16px; text-transform:uppercase; letter-spacing:2px; }
.modal input { margin-bottom:12px; }
.modal .btn-row { display:flex; gap:8px; justify-content:flex-end; margin-top:8px; }
.modal .error-msg { color:var(--red); font-size:10px; margin-top:4px; display:none; }
.badge { padding:3px 8px; border-radius:10px; font-size:10px; text-transform:uppercase; }
.badge.on { background:rgba(0,255,65,.1); color:var(--green); border:1px solid rgba(0,255,65,.3); }
.badge.off { background:rgba(255,68,102,.1); color:var(--red); border:1px solid rgba(255,68,102,.3); }
.tab-nav { display:flex; gap:0; margin-bottom:12px; border-bottom:1px solid var(--border); }
.tab-nav button { padding:8px 16px; border:none; background:transparent; color:var(--text); font-size:11px; cursor:pointer; border-bottom:2px solid transparent; transition:all .2s; font-family:inherit; text-transform:uppercase; letter-spacing:1px; }
.tab-nav button.active { color:var(--accent); border-bottom-color:var(--accent); }
.tab-content { display:none; }
.tab-content.active { display:block; }
.table-wrap { overflow-x:auto; }
.table-wrap table { min-width:600px; }
@media(max-width:768px){
  .topnav a { padding:0 8px; font-size:10px; }
  .topnav .logo { margin-right:8px; font-size:13px; }
  .stat-cards { grid-template-columns:repeat(auto-fill,minmax(140px,1fr)); }
}
</style>
</head>
<body>

<!-- Top Navigation -->
<nav class="topnav">
  <span class="logo">◈ QUANT</span>
  <a href="#dashboard" data-page="dashboard" class="active">仪表盘</a>
  <a href="#strategy_op" data-page="strategy_op">策略</a>
  <a href="#monitor" data-page="monitor">监控</a>
  <a href="#analysis" data-page="analysis">分析</a>
  <a href="#logs" data-page="logs">日志</a>
  <a href="#settings" data-page="settings">设置</a>
  <span class="spacer"></span>
  <span id="botStatus" class="badge off">离线</span>
  <span id="liveTime" style="font-size:10px;color:var(--text);margin:0 10px;">--</span>
  <div class="user-area" onclick="toggleUserMenu()">
    <img id="avatarTop" src="" alt="?" onerror="this.style.display='none'" style="display:none;">
    <span class="nick" id="nickTop">用户</span>
    <div class="user-dropdown" id="userDropdown">
      <a href="#" onclick="showProfileModal();return false;">编辑资料</a>
      <a href="#" onclick="showPwdModal2();return false;">修改密码</a>
      <a href="#" onclick="doLogout();return false;">退出登录</a>
    </div>
  </div>
</nav>

<!-- Main Content -->
<div class="content" id="mainContent"></div>

<!-- Profile Edit Modal -->
<div class="modal-overlay" id="profileModal">
  <div class="modal">
    <h3>编辑个人资料</h3>
    <label>昵称</label>
    <input type="text" id="profileNickname" maxlength="20" placeholder="输入昵称">
    <label>头像</label>
    <input type="file" id="profileAvatar" accept="image/jpeg,image/png" style="padding:4px;">
    <button class="btn btn-outline btn-sm" onclick="uploadAvatar()">上传头像</button>
    <div class="btn-row">
      <button class="btn btn-outline" onclick="closeProfileModal()">取消</button>
      <button class="btn btn-primary" onclick="saveProfile()">保存</button>
    </div>
  </div>
</div>

<!-- Password Change Modal -->
<div class="modal-overlay" id="pwdModal2">
  <div class="modal">
    <h3>修改密码</h3>
    <label>旧密码</label>
    <input type="password" id="oldPwd" placeholder="当前密码">
    <label>新密码</label>
    <input type="password" id="newPwd" placeholder="新密码（至少4位）">
    <div class="error-msg" id="pwdError2"></div>
    <div class="btn-row">
      <button class="btn btn-outline" onclick="closePwdModal2()">取消</button>
      <button class="btn btn-primary" onclick="changePassword()">确认</button>
    </div>
  </div>
</div>

<script>
// ── Auth Check ──
if(!localStorage.getItem('token')){ window.location.href='/'; }
let currentPage='dashboard';

// ── Navigation ──
document.querySelectorAll('.topnav a[data-page]').forEach(a=>{
  a.addEventListener('click',e=>{e.preventDefault();navigate(a.dataset.page);});
});

function navigate(page){
  currentPage=page;
  document.querySelectorAll('.topnav a[data-page]').forEach(a=>a.classList.toggle('active',a.dataset.page===page));
  if(refreshTimer){clearInterval(refreshTimer);refreshTimer=null;}
  loadPage(page);
}

// ── User Menu ──
function toggleUserMenu(){
  document.getElementById('userDropdown').classList.toggle('show');
}
document.addEventListener('click',e=>{
  if(!e.target.closest('.user-area')) document.getElementById('userDropdown').classList.remove('show');
});

// ── API ──
async function api(path,opts={}){
  const init={};
  if(opts.method==='POST'){init.method='POST';init.headers={'Content-Type':'application/json'};init.body=opts.body;}
  const token=localStorage.getItem('token')||'';
  let url='/api'+path;
  if(token){url+=(url.includes('?')?'&':'?')+'token='+encodeURIComponent(token);}
  const r=await fetch(url,init);
  if(r.status===401){localStorage.removeItem('token');localStorage.removeItem('username');window.location.href='/';return{error:'unauthorized'};}
  return r.json();
}

// ── Logout ──
async function doLogout(){
  await api('/auth/logout',{method:'POST',body:'{}'});
  localStorage.clear();
  window.location.href='/';
}

// ── Profile ──
async function loadProfileUI(){
  const p=await api('/profile');
  document.getElementById('nickTop').textContent=p.nickname||'用户';
  const avatar=await api('/avatar');
  const img=document.getElementById('avatarTop');
  if(avatar.data){img.src=avatar.data;img.style.display='';}else{img.style.display='none';}
}
function showProfileModal(){
  document.getElementById('userDropdown').classList.remove('show');
  document.getElementById('profileModal').classList.add('show');
  api('/profile').then(p=>document.getElementById('profileNickname').value=p.nickname||'');
}
function closeProfileModal(){document.getElementById('profileModal').classList.remove('show');}
async function uploadAvatar(){
  const file=document.getElementById('profileAvatar').files[0];
  if(!file)return alert('请选择图片');
  const reader=new FileReader();
  reader.onload=async(e)=>{
    const r=await api('/avatar/upload',{method:'POST',body:JSON.stringify({image:e.target.result})});
    alert(r.ok?'头像已上传':'上传失败');
    if(r.ok)loadProfileUI();
  };
  reader.readAsDataURL(file);
}
async function saveProfile(){
  const nick=document.getElementById('profileNickname').value.trim();
  await api('/profile/save',{method:'POST',body:JSON.stringify({nickname:nick})});
  closeProfileModal();
  loadProfileUI();
}

// ── Password Change ──
function showPwdModal2(){document.getElementById('pwdModal2').classList.add('show');}
function closePwdModal2(){document.getElementById('pwdModal2').classList.remove('show');}
async function changePassword(){
  const oldPwd=document.getElementById('oldPwd').value;
  const newPwd=document.getElementById('newPwd').value;
  const errEl=document.getElementById('pwdError2');
  if(newPwd.length<4){errEl.textContent='新密码至少4位';errEl.style.display='block';return;}
  const r=await api('/auth/reset-password',{method:'POST',body:JSON.stringify({oldPassword:oldPwd,newPassword:newPwd})});
  if(r.ok){alert('密码已修改');closePwdModal2();}
  else{errEl.textContent=r.error||'修改失败';errEl.style.display='block';}
}

// ── Page Loading ──
async function loadPage(page){
  const mc=document.getElementById('mainContent');
  const html=await(await fetch('/page/'+page)).text();
  mc.innerHTML=html;
  Object.values(chartInstances||{}).forEach(c=>c.destroy?.());
  chartInstances={};
  if(page==='dashboard'){initDashboard();refreshTimer=setInterval(refreshDashboard,5000);}
  if(page==='strategy_op')initStrategyOp();
  if(page==='monitor'){initMonitor();refreshTimer=setInterval(refreshMonitor,8000);}
  if(page==='analysis')initAnalysis();
  if(page==='logs')initLogs();
  if(page==='settings')initSettings();
}

// ── Clock ──
function updateClock(){
  document.getElementById('liveTime').textContent=new Date().toLocaleString('zh-CN');
}
setInterval(updateClock,1000);updateClock();

// ── Bot Status ──
async function updateBotStatus(){
  const d=await api('/status');
  const el=document.getElementById('botStatus');
  if(el){el.className='badge '+(d.running?'on':'off');el.textContent=d.running?'在线':'离线';}
}
setInterval(updateBotStatus,10000);updateBotStatus();

// ── Init ──
loadProfileUI();
navigate('dashboard');
</script>
</body>
</html>
"""
