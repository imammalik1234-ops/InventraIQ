/* Explain business inputs in place; server validation remains authoritative. */
(() => {
  const help = {
    reason: ['Explain why this action is needed so a reviewer can verify the decision.', 'Example: damaged carton'],
    business_reference: ['Connect this action to its order, delivery note, or internal ticket.', 'For example: DN-2026-1042'],
    external_reference: ['Use the delivery note number to identify this receipt and prevent duplicates.', 'For example: SUPPLIER-DN-1042'],
    evidence: ['Show reviewers what you observed on the goods or their labels.', 'Example: supplier label checked'],
    inspection_evidence: ['Describe the inspected condition so the return can be reviewed.', 'For example: unopened pack; expiry label checked'],
    lot_code: ['Give this batch an internal code so its stock and expiry can be traced.', 'For example: LOT-2026-1042'],
    supplier_lot_code: ['Copy the supplier batch number printed on the delivery or packaging.', 'For example: BATCH-1042'],
    quantity: ['Enter whole units; this is how much stock the action records.'],
    unit_cost: ['Enter the agreed supplier cost per unit in MYR.'],
    planned_arrival_utc: ['Include the arrival time and timezone so HQ can plan when stock becomes usable.'],
    expected_date: ['Enter the expected delivery date so incoming supply can be planned.'],
    sku_id: ['Choose the approved product this action applies to.'],
    expiry_date: ['Copy the actual label date. If a required date is missing, choose quarantine.'],
  };
  let sequence = 0;
  document.querySelectorAll('main form').forEach(form => {
    if (form.matches('.branch-switch')) return;
    form.querySelectorAll('input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), select, textarea').forEach(control => {
      const label = control.labels?.[0];
      if (!label) return;
      label.classList.add('entry-label');
      const badge = document.createElement('span');
      badge.className = `entry-requirement ${control.required ? 'is-required' : ''}`;
      badge.textContent = control.required ? 'Required' : 'Optional';
      if (control.name === 'expiry_date' && !control.required) badge.textContent = 'If required for product';
      if (control.readOnly) badge.textContent = 'Provided';
      if (control.required) badge.setAttribute('aria-hidden', 'true');
      if (label.contains(control)) label.insertBefore(badge, control);
      else label.append(badge);
      if (control.disabled) return;
      const detail = help[control.name];
      if (!detail) return;
      const hasHint = control.getAttribute('aria-describedby')?.split(/\s+/).some(id => document.getElementById(id)?.classList.contains('field-hint'));
      if (hasHint) return;
      if (!control.id) {
        let id;
        do { id = `entry-${++sequence}`; } while (document.getElementById(id));
        control.id = id;
      }
      const hint = document.createElement('span');
      hint.id = `${control.id}-purpose`;
      hint.className = 'entry-purpose';
      hint.textContent = detail[0];
      // Keep the hint out of the wrapping label's accessible name. The explicit
      // description reference still announces it once with the field.
      if (label.contains(control)) hint.setAttribute('aria-hidden','true');
      control.insertAdjacentElement('afterend', hint);
      const described = control.getAttribute('aria-describedby');
      control.setAttribute('aria-describedby', [described, hint.id].filter(Boolean).join(' '));
      if (detail[1] && control.tagName === 'INPUT' && ['text', 'search'].includes(control.type) && !control.placeholder) control.placeholder = detail[1];
      if (control.name === 'reason' || control.name === 'evidence' || control.name === 'inspection_evidence') {
        label.classList.add('decision-input');
        if (!label.contains(control)) label.parentElement.classList.add('decision-input-group');
      }
    });
  });
})();
