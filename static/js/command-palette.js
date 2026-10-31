/* Navigation is derived from the role-authorized server-rendered sidebar. */
(() => {
  const dialog = document.getElementById('command-palette');
  if (!dialog || typeof dialog.showModal !== 'function') return;
  const input = dialog.querySelector('#command-query');
  const results = dialog.querySelector('#command-results');
  const empty = dialog.querySelector('[data-command-empty]');
  const openers = document.querySelectorAll('[data-command-open]');
  let opener = null;
  let selected = 0;
  let visible = [];
  const destinations = Array.from(document.querySelectorAll('nav[aria-label="Main navigation"] a[href]')).map(link => ({
    label: link.textContent.trim(), href: link.getAttribute('href'), icon: link.querySelector('svg'),
    current: link.getAttribute('aria-current') === 'page',
  }));
  const select = index => {
    selected = Math.max(0, Math.min(index, visible.length - 1));
    visible.forEach((link, i) => {
      link.classList.toggle('is-selected', i === selected);
      link.setAttribute('aria-selected', String(i === selected));
    });
    if (visible[selected]) input.setAttribute('aria-activedescendant', visible[selected].id);
    else input.removeAttribute('aria-activedescendant');
    visible[selected]?.scrollIntoView({ block: 'nearest' });
  };
  const render = () => {
    results.replaceChildren();
    const query = input.value.trim().toLocaleLowerCase();
    destinations.filter(item => item.label.toLocaleLowerCase().includes(query)).forEach(item => {
      const link = document.createElement('a');
      link.className = 'command-result';
      link.id = `command-result-${results.children.length}`;
      link.setAttribute('role', 'option');
      link.href = item.href;
      if (item.icon) link.append(item.icon.cloneNode(true));
      const label = document.createElement('span');
      label.textContent = item.label;
      link.append(label);
      if (item.current) {
        const status = document.createElement('small');
        status.textContent = 'Current page';
        link.append(status);
      }
      results.append(link);
    });
    visible = Array.from(results.querySelectorAll('a'));
    empty.hidden = visible.length > 0;
    select(0);
  };
  const open = button => {
    if (dialog.open || document.querySelector('dialog[open]') || document.body.classList.contains('nav-open')) return;
    opener = button || document.activeElement;
    input.value = '';
    render();
    dialog.showModal();
    input.focus();
  };
  openers.forEach(button => {
    button.hidden = false;
    button.addEventListener('click', () => open(button));
  });
  if (/Mac|iPhone|iPad/.test(navigator.platform)) {
    document.querySelectorAll('[data-command-shortcut]').forEach(label => { label.textContent = '⌘ K'; });
  }
  dialog.querySelector('[data-command-close]').addEventListener('click', () => dialog.close());
  dialog.addEventListener('close', () => { if (opener?.isConnected) opener.focus({ preventScroll: true }); });
  dialog.addEventListener('click', event => {
    if (event.target !== dialog) return;
    const bounds = dialog.getBoundingClientRect();
    if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.close();
  });
  input.addEventListener('input', render);
  input.addEventListener('keydown', event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      select(selected + (event.key === 'ArrowDown' ? 1 : -1));
    } else if (event.key === 'Enter' && visible[selected]) {
      event.preventDefault();
      visible[selected].click();
    }
  });
  document.addEventListener('keydown', event => {
    if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
      if (document.querySelector('dialog[open]') && !dialog.open) return;
      event.preventDefault();
      if (dialog.open) dialog.close();
      else open(document.activeElement);
    }
  });
})();
