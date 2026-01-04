let branches = [];
let activeBranchId = null;
let activeBranch = null;

const el = (id) => document.getElementById(id);

async function api(path, method="GET", body=null){
  const res = await fetch(path, {
    method,
    headers: body ? {"Content-Type":"application/json"} : {},
    body: body ? JSON.stringify(body) : null
  });
  if(!res.ok){
    const txt = await res.text();
    throw new Error(txt);
  }
  return await res.json();
}

function buildTree(list){
  const byId = new Map();
  const children = new Map();
  list.forEach(b => {
    const id = b.branch_id;
    byId.set(id, b);
    const parent = (b.parent_branch_id || "").trim();
    if(!children.has(parent)) children.set(parent, []);
    children.get(parent).push(id);
  });
  for (const [k, ids] of children.entries()){
    ids.sort((a,b)=> (byId.get(a).created_at||"").localeCompare(byId.get(b).created_at||""));
  }
  return {byId, children};
}

function renderTree(){
  const root = el("tree");
  root.innerHTML = "";
  const {byId, children} = buildTree(branches);

  function renderNode(id, depth){
    const b = byId.get(id);
    const div = document.createElement("div");
    div.className = "branchItem" + (id === activeBranchId ? " active" : "");
    div.style.marginLeft = (depth * 14) + "px";
    div.onclick = () => openBranch(id);

    const title = (b.title || id);
    const fork = (b.fork_from_message || "");
    const parent = (b.parent_branch_id || "");
    div.innerHTML = `
      <div><b>${escapeHtml(title)}</b></div>
      <div class="branchMetaSmall">${escapeHtml(id)}${parent? " ← "+escapeHtml(parent):""}${fork? " @ M"+escapeHtml(String(fork)):""}</div>
    `;
    root.appendChild(div);

    (children.get(id) || []).forEach(childId => renderNode(childId, depth+1));
  }

  (children.get("") || []).forEach(r => renderNode(r, 0));
}

function escapeHtml(s){
  return (s||"")
    .replaceAll("&","&amp;")
    .replaceAll("<","&lt;")
    .replaceAll(">","&gt;");
}

function renderChat() {
  const chat = el("chat");
  chat.innerHTML = "";
  (activeBranch.messages || []).forEach(m => {
    const div = document.createElement("div");
    div.className = "msg";

    // Clean up the content by removing redundant line breaks
    const content = (m.content || "").replace(/\n{2,}/g, '\n');
    const htmlContent = marked.parse(content);
    
    div.innerHTML = `
      <div class="msgRole">M${m.m} • ${m.role}</div>
      <div class="msgContent markdown-body">${htmlContent}</div>
    `;
    chat.appendChild(div);
  });
  chat.scrollTop = chat.scrollHeight;
}

async function refresh(){
  branches = await api("/api/branches");
  renderTree();
}

async function openBranch(branchId){
  activeBranchId = branchId;
  activeBranch = await api(`/api/branch/${branchId}`);
  const meta = activeBranch.meta || {};
  el("branchTitle").innerText = meta.title || branchId;

  const parent = (meta.parent_branch_id || "").trim();
  const fork = meta.fork_from_message ? `M${meta.fork_from_message}` : "";
  el("branchMeta").innerText = parent ? `Forked from ${parent} @ ${fork}` : `Root branch`;

  el("composer").classList.remove("hidden");
  renderChat();
  renderTree();
}

async function createRoot(){
  const title = (el("newTitle").value || "").trim() || "Root";
  el("newTitle").value = "";
  const meta = await api("/api/create_root", "POST", {title});
  await refresh();
  await openBranch(meta.branch_id);
}

async function sendReply(){
  const text = (el("userText").value || "").trim();
  if(!text) return;
  el("userText").value = "";
  await api("/api/reply", "POST", {branch_id: activeBranchId, user_text: text});
  await openBranch(activeBranchId);
}

async function fork(){
  const title = (el("forkTitle").value || "").trim() || "Clarification";
  const from = parseInt((el("forkFrom").value || "").trim(), 10);
  if(!from || from < 1){
    alert("Fork from M# must be a number like 12");
    return;
  }
  el("forkTitle").value = "";
  el("forkFrom").value = "";
  const meta = await api("/api/fork", "POST", {from_branch_id: activeBranchId, from_message: from, title});
  await refresh();
  await openBranch(meta.branch_id);
}

async function deleteActive(){
  if(!activeBranchId) return;
  const ok = confirm("Delete this branch and its child branches? This deletes the Obsidian notes.");
  if(!ok) return;
  await api("/api/delete_branch", "POST", {branch_id: activeBranchId, delete_artifacts: true});
  activeBranchId = null;
  activeBranch = null;
  el("branchTitle").innerText = "Select a branch";
  el("branchMeta").innerText = "";
  el("chat").innerHTML = "";
  el("composer").classList.add("hidden");
  await refresh();
}

el("btnCreateRoot").onclick = createRoot;
el("btnSend").onclick = sendReply;
el("btnFork").onclick = fork;
el("btnDelete").onclick = deleteActive;

refresh();
