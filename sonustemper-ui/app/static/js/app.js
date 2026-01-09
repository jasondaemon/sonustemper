function setupUtilMenu(toggleId, dropdownId){
  const toggle = document.getElementById(toggleId);
  const dd = document.getElementById(dropdownId);
  if(!toggle || !dd) return;
  const close = ()=> dd.classList.add('hidden');
  toggle.addEventListener('click', (e)=>{
    e.stopPropagation();
    dd.classList.toggle('hidden');
  });
  document.addEventListener('click', (e)=>{
    if(!dd.contains(e.target) && e.target!==toggle){
      close();
    }
  });
}

function showToast(msg){
  const el = document.getElementById('toast');
  if(!el) return;
  el.textContent = msg || '';
  el.classList.add('show');
  el.classList.remove('hidden');
  clearTimeout(el._hideTimer);
  el._hideTimer = setTimeout(()=>{ el.classList.remove('show'); }, 1800);
}

// HTMX hooks for quick feedback on deletes
document.addEventListener('htmx:afterSwap', function(evt){
  const elt = evt.target;
  if(!elt) return;
  const isDeleteForm = elt.closest && elt.closest('form.delete-selected-form');
  if(isDeleteForm){
    showToast('Updated');
  }
});

document.addEventListener('htmx:onError', function(evt){
  showToast('Action failed');
});

document.addEventListener('htmx:sendError', function(evt){
  showToast('Network error');
});
