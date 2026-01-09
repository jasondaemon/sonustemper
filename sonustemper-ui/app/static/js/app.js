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
