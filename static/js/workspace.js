/* Progressive enhancements. Server validation, permissions and transactions remain authoritative. */
(() => {
  const densityControl = document.querySelector('[data-density-control]');
  if (densityControl) {
    densityControl.closest('label').hidden = false;
    let density = 'normal';
    try { if (localStorage.getItem('inventraiq-table-spacing') === 'compact') density = 'compact'; } catch (_) { /* Storage is optional. */ }
    document.documentElement.dataset.density = density;
    densityControl.value = density;
    densityControl.addEventListener('change', () => {
      const value = densityControl.value === 'compact' ? 'compact' : 'normal';
      document.documentElement.dataset.density = value;
      try { localStorage.setItem('inventraiq-table-spacing', value); } catch (_) { /* Keep session preference. */ }
    });
  }
  const catalogQuery = document.querySelector('[data-catalog-query]');
  if (catalogQuery) {
    catalogQuery.closest('.catalog-search').hidden = false;
    const entries = Array.from(document.querySelectorAll('[data-catalog-entry]'));
    const count = document.querySelector('[data-catalog-count]');
    const previous = document.querySelector('[data-catalog-prev]');
    const next = document.querySelector('[data-catalog-next]');
    const pageSize = 25;
    let catalogPage = 0;
    const filterCatalog = () => {
      const query = catalogQuery.value.trim().toLocaleLowerCase();
      const matches = entries.filter(entry => entry.dataset.catalogEntry.toLocaleLowerCase().includes(query));
      catalogPage = Math.min(catalogPage, Math.max(0, Math.ceil(matches.length / pageSize) - 1));
      const shown = new Set(matches.slice(catalogPage * pageSize, (catalogPage + 1) * pageSize));
      entries.forEach(entry => {
        entry.hidden = !shown.has(entry);
      });
      count.textContent = matches.length ? `${catalogPage * pageSize + 1}\u2013${Math.min((catalogPage + 1) * pageSize, matches.length)} of ${matches.length} matching products` : '0 products shown';
      previous.disabled = catalogPage === 0;
      next.disabled = (catalogPage + 1) * pageSize >= matches.length;
    };
    catalogQuery.addEventListener('input', () => { catalogPage = 0; filterCatalog(); });
    previous.addEventListener('click', () => { catalogPage -= 1; filterCatalog(); });
    next.addEventListener('click', () => { catalogPage += 1; filterCatalog(); });
    filterCatalog();
  }
  document.querySelectorAll('[data-policy-create]').forEach(form => {
    const kind = form.querySelector('[data-policy-kind]');
    const target = form.querySelector('[data-policy-target]');
    const update = () => {
      form.querySelectorAll('[data-policy-options]').forEach(group => {
        group.disabled = group.dataset.policyOptions !== kind.value;
        group.hidden = group.disabled;
      });
      const first = target.querySelector('optgroup:not([disabled]) option');
      if (first) first.selected = true;
    };
    kind.addEventListener('change', update);
    update();
  });
  document.querySelectorAll('[data-lot-mode]').forEach(form => {
    const mode = form.querySelector('[name="mode"]');
    const update = () => form.querySelectorAll('[data-lot-fields]').forEach(group => {
      const active = group.dataset.lotFields === mode.value;
      group.hidden = !active;
      group.querySelectorAll('input').forEach(input => { input.disabled = !active; input.required = active; });
    });
    mode.addEventListener('change', update);
    update();
  });
  const menu = document.querySelector('[data-nav-toggle]');
  const closeNavigation = document.querySelectorAll('[data-nav-close]');
  const sidebar = document.getElementById('primary-sidebar');
  const navigationBackground = document.querySelectorAll('[data-nav-background]');
  const mobileNavigation = matchMedia('(max-width: 1000px)');
  const focusableSelector = [
    'a[href]',
    'button:not([disabled])',
    'input:not([disabled]):not([type="hidden"])',
    'select:not([disabled])',
    'textarea:not([disabled])',
    'summary',
    '[tabindex]:not([tabindex="-1"])',
  ].join(',');
  const previousBackgroundState = new Map();
  const sidebarRole = sidebar?.getAttribute('role');
  const sidebarModal = sidebar?.getAttribute('aria-modal');
  let navigationOpener = null;

  function isVisible(element) {
    return element && element.isConnected && !element.closest('[inert], [hidden]')
      && element.getClientRects().length > 0;
  }

  function navigationControls() {
    return sidebar ? Array.from(sidebar.querySelectorAll(focusableSelector)).filter(isVisible) : [];
  }

  function focusNavigation() {
    const target = navigationControls()[0] || sidebar;
    target?.focus({ preventScroll: true });
  }

  function restoreAttribute(element, name, value) {
    if (value === null || value === undefined) element.removeAttribute(name);
    else element.setAttribute(name, value);
  }

  function setNavigation(requestedOpen, restoreFocus = false) {
    const wasOpen = document.body.classList.contains('nav-open');
    const open = Boolean(requestedOpen && mobileNavigation.matches && sidebar);

    if (open && !wasOpen) {
      navigationOpener = document.activeElement;
      navigationBackground.forEach(element => {
        previousBackgroundState.set(element, element.inert);
        element.inert = true;
      });
    }

    document.body.classList.toggle('nav-open', open);
    menu?.setAttribute('aria-expanded', String(open));
    menu?.setAttribute('aria-label', open ? 'Close navigation' : 'Open navigation');
    closeNavigation.forEach(button => {
      // The close control inside the sidebar also works without a pointer.
      if (!sidebar?.contains(button)) button.hidden = !open;
    });

    if (sidebar) {
      sidebar.inert = mobileNavigation.matches && !open;
      if (open) {
        sidebar.setAttribute('role', 'dialog');
        sidebar.setAttribute('aria-modal', 'true');
      } else {
        restoreAttribute(sidebar, 'role', sidebarRole);
        restoreAttribute(sidebar, 'aria-modal', sidebarModal);
      }
    }

    if (open && !wasOpen) focusNavigation();
    if (!open && wasOpen) {
      previousBackgroundState.forEach((inert, element) => { element.inert = inert; });
      previousBackgroundState.clear();
      if (restoreFocus) {
        const target = isVisible(navigationOpener) ? navigationOpener : menu;
        if (isVisible(target)) target.focus({ preventScroll: true });
        else focusNavigation();
      }
    }
  }

  menu?.addEventListener('click', () => {
    setNavigation(!document.body.classList.contains('nav-open'), true);
  });
  closeNavigation.forEach(button => {
    button.addEventListener('click', () => setNavigation(false, true));
  });
  mobileNavigation.addEventListener('change', () => {
    const focusedSidebar = sidebar?.contains(document.activeElement);
    const wasOpen = document.body.classList.contains('nav-open');
    setNavigation(false);
    if (focusedSidebar && mobileNavigation.matches && isVisible(menu)) menu.focus();
    else if (wasOpen && !mobileNavigation.matches) {
      // The mobile close button is hidden after a desktop resize.
      const currentPage = sidebar?.querySelector('[aria-current="page"]');
      (isVisible(currentPage) ? currentPage : navigationControls()[0])?.focus();
    }
  });
  setNavigation(false);

  document.addEventListener('keydown', event => {
    if (mobileNavigation.matches && document.body.classList.contains('nav-open')) {
      if (event.key === 'Escape') {
        event.preventDefault();
        setNavigation(false, true);
      } else if (event.key === 'Tab') {
        const controls = navigationControls();
        const first = controls[0];
        const last = controls[controls.length - 1];
        const active = document.activeElement;
        if (!first) {
          event.preventDefault();
          sidebar.focus();
        } else if (!sidebar.contains(active) || (event.shiftKey && active === first)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (!event.shiftKey && active === last) {
          event.preventDefault();
          first.focus();
        }
      }
      return;
    }

    if (event.key === 'Escape') {
      const disclosure = document.activeElement?.closest('details[open]');
      if (disclosure?.querySelector('.row-action-panel')) {
        event.preventDefault();
        disclosure.open = false;
        disclosure.querySelector('summary')?.focus();
      }
    }
  });
  document.addEventListener('focusin', event => {
    if (mobileNavigation.matches && document.body.classList.contains('nav-open')
        && sidebar && !sidebar.contains(event.target)) focusNavigation();
  });
  document.addEventListener('click', event => {
    document.querySelectorAll('details[open]').forEach(disclosure => {
      if (disclosure.querySelector('.row-action-panel') && !disclosure.contains(event.target)) {
        disclosure.open = false;
      }
    });
  });

  const assistant = document.getElementById('assistant-panel');
  let assistantOpener = null;
  document.querySelectorAll('[data-assistant-open]').forEach(button => {
    button.addEventListener('click', () => {
      if (!assistant || assistant.open) return;
      assistantOpener = button;
      if (document.body.classList.contains('nav-open')) {
        setNavigation(false);
        assistantOpener = menu;
      }
      assistant.showModal();
      button.setAttribute('aria-expanded', 'true');
      assistant.querySelector('[data-assistant-close]')?.focus();
      const loading = assistant.querySelector('[data-assistant-loading]');
      const unavailable = assistant.querySelector('[data-assistant-unavailable]');
      // Opening a local dialog is immediate. Conversation reads own their real
      // loading/empty states; a welcome panel must not overwrite restored history.
      assistant.dataset.assistantState = 'ready';
      if (loading) loading.hidden = true;
      if (unavailable) unavailable.hidden = false;
    });
  });
  assistant?.querySelector('[data-assistant-close]')?.addEventListener('click', () => assistant.close());
  assistant?.addEventListener('keydown', event => {
    if (!assistant.open || event.key !== 'Tab') return;
    const controls = Array.from(assistant.querySelectorAll(focusableSelector)).filter(isVisible);
    const first = controls[0];
    const last = controls[controls.length - 1];
    const active = document.activeElement;
    if (!first) {
      event.preventDefault();
      assistant.focus();
    } else if (!controls.includes(active) || (event.shiftKey && active === first)) {
      event.preventDefault();
      (event.shiftKey ? last : first).focus();
    } else if (!event.shiftKey && active === last) {
      // Keep Tab inside the modal rather than moving to browser chrome.
      event.preventDefault();
      first.focus();
    }
  });
  assistant?.addEventListener('close', () => {
    // Native dialog Escape and the explicit close control share this return path.
    assistantOpener?.setAttribute('aria-expanded', 'false');
    if (isVisible(assistantOpener)) assistantOpener.focus({ preventScroll: true });
    assistantOpener = null;
  });

  document.querySelectorAll('[data-password-toggle]').forEach(button => {
    button.addEventListener('click', () => {
      const input = document.getElementById(button.dataset.passwordToggle);
      if (!input) return;
      const show = input.type === 'password';
      input.type = show ? 'text' : 'password';
      button.setAttribute('aria-label', show ? 'Hide password' : 'Show password');
      button.textContent = show ? 'Hide' : 'Show';
    });
  });
  document.querySelectorAll('[data-print]').forEach(button => {
    button.addEventListener('click', () => window.print());
  });

  const fieldErrors = new WeakMap();
  const summaryForms = new WeakMap();
  let errorSequence = 0;

  function addDescription(element, id) {
    const descriptions = new Set((element.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean));
    descriptions.add(id);
    element.setAttribute('aria-describedby', Array.from(descriptions).join(' '));
  }

  function removeDescription(element, id) {
    const descriptions = (element.getAttribute('aria-describedby') || '').split(/\s+/)
      .filter(value => value && value !== id);
    if (descriptions.length) element.setAttribute('aria-describedby', descriptions.join(' '));
    else element.removeAttribute('aria-describedby');
  }

  function associateSummary(summary, form) {
    if (!summary || !form) return;
    if (!summary.id) summary.id = `form-validation-${++errorSequence}`;
    const previousForm = summaryForms.get(summary);
    if (previousForm && previousForm !== form) removeDescription(previousForm, summary.id);
    addDescription(form, summary.id);
    summaryForms.set(summary, form);
  }

  document.querySelectorAll('.validation-summary').forEach(summary => {
    const form = summary.closest('form') || summary.parentElement?.querySelector('form');
    associateSummary(summary, form);
  });

  document.addEventListener('invalid', event => {
    // A field in a collapsed optional line must be reachable for correction.
    for (let detail = event.target.closest('details'); detail; detail = detail.parentElement?.closest('details')) detail.open = true;
    const control = event.target;
    if (!control.form || !control.willValidate) return;
    let owned = fieldErrors.get(control);
    if (!owned) {
      const message = document.createElement('span');
      message.className = 'field-error';
      message.id = `field-validation-${++errorSequence}`;
      message.setAttribute('aria-live', 'polite');
      owned = { message, previousInvalid: control.getAttribute('aria-invalid') };
      fieldErrors.set(control, owned);
      control.insertAdjacentElement('afterend', message);
      addDescription(control, message.id);
    }
    owned.message.textContent = control.validationMessage || 'Review this field.';
    control.setAttribute('aria-invalid', 'true');
  }, true);

  function clearCorrectedField(event) {
    const control = event.target;
    const owned = fieldErrors.get(control);
    if (!owned || !control.validity.valid) return;
    removeDescription(control, owned.message.id);
    restoreAttribute(control, 'aria-invalid', owned.previousInvalid);
    owned.message.remove();
    fieldErrors.delete(control);
  }
  document.addEventListener('input', clearCorrectedField);
  document.addEventListener('change', clearCorrectedField);

  const submissionStates = new WeakMap();
  const unknown = 'The operation status is unknown. Check inventory, sales and audit records before retrying. '
    + 'This form is locked; reloading does not prove the operation failed.';

  document.addEventListener('submit', async event => {
    const form = event.target;
    if (event.defaultPrevented || !(form instanceof HTMLFormElement)) return;
    if (form.hasAttribute('data-enhance-form') && submissionStates.has(form)) {
      event.preventDefault();
      return;
    }
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      event.preventDefault();
      return;
    }
    if (!form.hasAttribute('data-enhance-form')) return;
    event.preventDefault();
    if (!form.checkValidity()) {
      form.reportValidity();
      return;
    }

    const buttons = Array.from(form.querySelectorAll('button[type="submit"],input[type="submit"],button:not([type])'));
    const previous = buttons.map(button => button.disabled);
    const message = document.getElementById('global-message');
    const body = new FormData(form);
    submissionStates.set(form, 'pending');
    form.setAttribute('aria-busy', 'true');
    buttons.forEach(button => { button.disabled = true; });
    if (message) {
      message.hidden = true;
      const previousForm = summaryForms.get(message);
      if (previousForm) removeDescription(previousForm, message.id);
      summaryForms.delete(message);
    }
    let confirmedFailure = false;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 30000);

    const show = text => {
      if (!message) return;
      message.textContent = text;
      message.hidden = false;
      if (!message.id) message.id = 'global-message';
      associateSummary(message, form);
      message.focus({ preventScroll: true });
      message.scrollIntoView({ block: 'center' });
    };

    try {
      // Named controls (for example administration's input[name=action]) mask
      // native form properties. Attributes preserve the intended transport URL.
      const response = await fetch(form.getAttribute('action') || location.href, {
        method: form.getAttribute('method') || 'POST',
        headers: { 'X-InventraIQ-Form': '1' },
        body,
        credentials: 'same-origin',
        signal: controller.signal,
      });
      if (response.redirected && response.ok) {
        location.assign(response.url);
        return;
      }
      if (response.ok && (response.headers.get('content-type') || '').includes('application/json')) {
        const result = await response.json();
        if (typeof result.redirect_to === 'string') {
          const destination = new URL(result.redirect_to, location.href);
          if (destination.origin === location.origin) {
            location.assign(destination.href);
            return;
          }
        }
      }
      // Only a definite client rejection permits another submission. A 5xx or
      // unreadable response may follow a committed write, including a failed redirect.
      confirmedFailure = !response.redirected && response.status >= 400 && response.status < 500;
      if (!confirmedFailure) {
        show(unknown);
        return;
      }
      let text = 'The request was rejected. Review the current records and correct the form.';
      if ((response.headers.get('content-type') || '').includes('application/json')) {
        const result = await response.json();
        text = result.error || text;
      } else {
        const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
        const summary = doc.querySelector('.validation-summary');
        if (summary) {
          const copy = document.importNode(summary, true);
          const existing = form.parentElement.querySelector('.validation-summary');
          if (existing?.id && !copy.id) copy.id = existing.id;
          if (existing) existing.replaceWith(copy);
          else form.before(copy);
          associateSummary(copy, form);
          copy.focus();
          return;
        }
      }
      show(text);
    } catch {
      confirmedFailure = false;
      show(unknown);
    } finally {
      clearTimeout(timer);
      form.removeAttribute('aria-busy');
      if (confirmedFailure) {
        submissionStates.delete(form);
        buttons.forEach((button, index) => { button.disabled = previous[index]; });
      } else {
        submissionStates.set(form, 'review-required');
        form.dataset.submissionState = 'review-required';
      }
    }
  });
})();
