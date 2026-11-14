(() => {
  const key = 'sonustemper:theme';
  const system = matchMedia('(prefers-color-scheme: dark)');
  let preference = 'light';
  let dialog;

  function applyTheme() {
    document.documentElement.dataset.theme = preference === 'system'
      ? (system.matches ? 'dark' : 'light')
      : preference;
    if (dialog) dialog.querySelector(`input[value="${preference}"]`).checked = true;
  }

  function readPreference(value) {
    preference = value === 'system' || value === 'dark' ? value : 'light';
    applyTheme();
  }

  try { readPreference(localStorage.getItem(key)); }
  catch { applyTheme(); }
  system.addEventListener('change', applyTheme);
  window.addEventListener('storage', event => {
    if (event.key === key || event.key === null) readPreference(event.newValue);
  });

  document.addEventListener('click', event => {
    const opener = event.target instanceof Element && event.target.closest('[data-theme-settings]');
    if (!opener) return;
    if (!dialog) {
      dialog = document.createElement('dialog');
      dialog.className = 'theme-dialog';
      dialog.setAttribute('aria-labelledby', 'theme-settings-title');
      dialog.innerHTML = `
        <form method="dialog">
          <h2 id="theme-settings-title">Settings</h2>
          <fieldset>
            <legend>Appearance</legend>
            <div class="theme-options">
              <label><input type="radio" name="appearance" value="light"> Light</label>
              <label><input type="radio" name="appearance" value="dark"> Dark</label>
              <label><input type="radio" name="appearance" value="system"> System</label>
            </div>
          </fieldset>
          <button>Done</button>
        </form>`;
      dialog.addEventListener('change', event => {
        readPreference(event.target.value);
        try { localStorage.setItem(key, preference); }
        catch { /* The current tab still uses the selected theme. */ }
      });
      document.body.append(dialog);
    }
    applyTheme();
    dialog.showModal();
  });
})();
