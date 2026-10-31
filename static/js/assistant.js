/* Locally grounded evidence. User, model and source strings are always text. */
(() => {
  'use strict';
  const panel = document.getElementById('assistant-panel');
  const form = document.getElementById('assistant-form');
  if (!panel || !form) return;
  const input = document.getElementById('assistant-question');
  const status = document.getElementById('assistant-status');
  const messages = document.getElementById('assistant-messages');
  const send = document.getElementById('assistant-send');
  const cancel = document.getElementById('assistant-cancel');
  const forget = document.getElementById('assistant-forget');
  const empty = panel.querySelector('[data-assistant-empty]');
  let branchNames = {};
  try { branchNames = JSON.parse(panel.dataset.branchNames || '{}'); } catch (_) { /* Names are optional presentation. */ }
  const branchName = id => branchNames[String(id)] || `Branch ${id}`;
  let conversation = null, busy = false, cancelled = false, version = 0;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  const toolNames = {inventory_summary: 'Inventory', inventory_risks: 'Inventory risks', sales_summary: 'Recorded sales', compare_branches: 'Branch comparison', product_performance: 'Observed product performance', forecast_evidence: 'Forecast evidence', recommendation_preview: 'Advisory recommendations', report_artifact: 'Report download'};
  const errors = {
    privacy_rejected: 'Use an approved synthetic example. History and business evidence cannot be sent externally.',
    access_denied: 'This question requests information outside your current permissions.',
    conversation_unavailable: 'This conversation expired or your permissions changed. Send a question to start again.',
    provider_unavailable: 'The planner is unavailable. No business records were changed.',
    provider_configuration: 'External planning is not configured for this local demonstration.',
    provider_rate_limit: 'The provider limit was reached. No automatic retry will run.',
    provider_limit: 'The configured provider budget was reached. No automatic retry will run.',
    timeout: 'The read timed out. No conversation turn was saved.',
    cancelled: 'The request was cancelled. No conversation turn was saved.',
    history_limit: 'This conversation reached its history limit. Clear it to start another.',
    conversation_busy: 'A question is already running for your account.',
    invalid_arguments: 'This question could not be planned safely. Try one of the examples.',
    invalid_provider_response: 'The planner did not return a valid read-only plan. No automatic retry will run.',
    output_limit: 'There is too much information to display. Choose one branch or product and try again.'
  };
  function node(tag, text, className) {
    const element = document.createElement(tag);
    if (text !== undefined) element.textContent = text;
    if (className) element.className = className;
    return element;
  }
  function human(value) { return String(value ?? 'Unavailable').replaceAll('_', ' '); }
  function inputReason(code) {
    if (code === 'POLICY_VISIBILITY_RESTRICTED') return 'Supplier planning is restricted for your role; ask headquarters to review it.';
    if (code.startsWith('FORECAST_')) return 'A current demo forecast with the required source and date checks is needed to estimate an order.';
    if (code.includes('EXPIRY')) return 'Review the expiry policy so unusable stock is excluded.';
    if (code.includes('POLICY')) return 'Review supplier lead time, minimum order, pack size, and safety stock.';
    if (code.includes('LOT') || code.includes('STOCK') || code.includes('OPENING')) return 'Verify lot balances and expiry labels.';
    if (code.includes('INBOUND') || code.startsWith('PO_') || code.startsWith('TRANSFER_')) return 'Confirm incoming quantities and arrival dates to avoid ordering twice.';
    if (code === 'SYNTHETIC_REVIEW_ENVIRONMENT_REQUIRED') return 'Order quantities need the separate planning demo with reviewed forecast and supplier inputs.';
    return human(code).toLowerCase();
  }
  function planningBrief(article, entries) {
    const rows = entries.filter(e => e.result.status === 'ok' && e.result.tool === 'recommendation_preview').flatMap(e => e.result.data?.rows || []);
    if (!rows.length) return false;
    article.append(node('p', 'Review the proposed quantity and its evidence before choosing an operational action.'));
    for (const row of rows.slice(0, 6)) {
      const replenishment = row.recommendations.find(r => r.type === 'replenishment');
      const quantity = replenishment?.suggested_quantity;
      const card = node('section', undefined, 'assistant-decision-card');
      card.append(node('strong', `${branchName(row.branch_id)} · ${row.sku_code || 'Product'}`));
      card.append(node('h4', quantity === null || quantity === undefined ? 'Order quantity not ready' : `Review ${quantity} units for replenishment`));
      const reasons = [...new Set(row.reason_codes.map(inputReason))];
      if (reasons.length) {
        card.append(node('small', 'What needs your input', 'assistant-decision-label'));
        const list = node('ul'); reasons.forEach(reason => list.append(node('li', reason))); card.append(list);
      } else card.append(node('p', 'This demo quantity uses stored forecasts and configured stock, inbound, expiry, and supplier rules. Human review is required.'));
      if (Number.isInteger(row.branch_id) && Number.isInteger(row.sku_id)) {
        const link = node('a', 'Open this planning review →');
        link.href = '/ai-predictions?' + new URLSearchParams({branch_id: row.branch_id, sku_id: row.sku_id});
        card.append(link);
      }
      article.append(card);
    }
    if (rows.length > 6) article.append(node('p', `Showing 6 of ${rows.length} retrieved reviews. Open planning for the full selection.`, 'assistant-source-meta'));
    return true;
  }
  function factBrief(article, entries) {
    for (const entry of entries) {
      const result = entry.result;
      if (result.status !== 'ok' || result.tool === 'recommendation_preview') continue;
      const summary = result.data?.summary || {};
      const grid = node('dl', undefined, 'assistant-fact-grid');
      const fields = result.tool === 'sales_summary' ? [['recorded_gross', 'Gross MYR'], ['recorded_refunds', 'Refunds MYR'], ['recorded_net', 'Net MYR']] : result.tool === 'inventory_summary' ? [['available', 'Available units'], ['on_hand', 'On hand'], ['quarantined', 'Quarantined']] : [];
      for (const [key, label] of fields) {
        if (summary[key] === undefined || summary[key] === null) continue;
        const item = node('div'); item.append(node('dt', label), node('dd', String(summary[key]))); grid.append(item);
      }
      if (grid.children.length) article.append(grid);
      if (result.tool === 'compare_branches') {
        for (const row of result.data?.rows || []) {
          const card = node('section', undefined, 'assistant-decision-card');
          card.append(node('strong', branchName(row.branch_id)), node('p', `${row.available} available units · MYR ${row.recorded_net} net sales`)); article.append(card);
        }
      }
    }
  }
  function instant(value) {
    if (!value) return 'Unavailable';
    const date = new Date(value);
    return Number.isNaN(date.valueOf()) ? String(value) : date.toLocaleString(undefined, {timeZoneName: 'short'});
  }
  async function api(path, body, method = 'POST') {
    const options = {method, credentials: 'same-origin', headers: {'X-CSRF-Token': csrf}};
    if (method !== 'GET') { options.headers['Content-Type'] = 'application/json'; options.body = JSON.stringify(body); }
    const response = await fetch(path, options);
    let data;
    try { data = await response.json(); } catch (_) { throw new Error('The assistant response could not be verified. Check your session and try again.'); }
    if (!response.ok) {
      if ([401, 403, 404].includes(response.status)) conversation = null;
      const code = data.error?.code;
      const error = new Error(errors[code] || 'The question could not be completed safely. Check your session and branch selection.');
      error.code = code;
      throw error;
    }
    return data;
  }
  function source(entry, index) {
    const result = entry.result;
    const details = node('details', undefined, 'assistant-source');
    const summary = node('summary', `${entry.citation} · ${toolNames[result.tool] || 'Authorized source'} · ${result.status === 'ok' ? 'Read completed' : 'Unavailable'}`);
    details.id = `assistant-source-${index}-${entry.citation}`;
    details.append(summary);
    const rows = result.data?.rows || [];
    const complete = result.completeness || {};
    details.append(node('p', `${(result.scope?.branch_ids || []).map(branchName).join(', ') || 'Unconfirmed scope'} · ${instant(result.as_of)}`, 'assistant-source-meta'));
    details.append(node('p', `${rows.length} rows shown${complete.total_rows === null || complete.total_rows === undefined ? '' : ` of ${complete.total_rows}`}. ${result.status !== 'ok' ? 'Usable evidence was not established.' : complete.truncated ? 'This source is a partial page.' : 'This source is complete.'}`));
    if (result.status !== 'ok') details.append(node('p', 'This source could not establish usable evidence. It must not be interpreted as zero stock or no risk.', 'notice warning'));
    if (result.tool === 'forecast_evidence') details.append(node('p', 'Stored provisional synthetic evidence only. Unavailable predictions remain withheld.'));
    if (result.tool === 'recommendation_preview') details.append(node('p', 'Advisory preview only. These recommendations do not authorize an operation.'));
    if (rows.length) {
      if (result.tool === 'report_artifact') {
        for (const row of rows) {
          if (/^\/assistant\/artifacts\/[A-Za-z0-9_-]{32}$/.test(row.download_path || '')) {
            const link = node('a', `Download ${human(row.media_type)} report`);
            link.href = row.download_path; details.append(link);
            details.append(node('p', `Expires ${instant(row.expires_at)} · ${row.bytes} bytes`));
          }
        }
      } else {
        const preferred = ['branch_id', 'sku_code', 'name', 'lot_code', 'available', 'on_hand', 'status', 'days_remaining', 'threshold', 'date', 'total', 'recorded_net', 'units', 'predicted_demand', 'qualification', 'reason'];
        const columns = preferred.filter(key => rows.some(row => Object.hasOwn(row, key)));
        const wrapper = node('div', undefined, 'table-container');
        wrapper.tabIndex = 0; wrapper.setAttribute('role', 'region'); wrapper.setAttribute('aria-label', `${entry.citation} evidence table`);
        const table = node('table', undefined, 'assistant-evidence-table');
        table.append(node('caption', `${entry.citation}: ${toolNames[result.tool] || 'Source'} · integer stock units; recorded money in MYR`));
        const head = node('tr');
        columns.forEach(key => { const labels = {branch_id:'Branch',sku_code:'Product code',lot_code:'Batch code',on_hand:'Physical units',days_remaining:'Days to expiry',threshold:'Reorder level'}; const th = node('th', labels[key] || human(key)); th.scope = 'col'; head.append(th); });
        const thead = node('thead'); thead.append(head); table.append(thead);
        const tbody = node('tbody');
        for (const row of rows) {
          const tr = node('tr');
          for (const key of columns) tr.append(node('td', key === 'branch_id' ? branchName(row[key]) : human(row[key])));
          tbody.append(tr);
        }
        table.append(tbody); wrapper.append(table); details.append(wrapper);
      }
    } else if (result.status === 'ok') details.append(node('p', 'No rows matched this read. Review the scope and filters before drawing a conclusion.'));
    const provenance = node('ul');
    for (const item of result.provenance || []) provenance.append(node('li', item));
    details.append(node('h4', 'Source details'), provenance);
    return details;
  }
  function render(data) {
    messages.replaceChildren();
    empty.hidden = data.messages.length > 0;
    const latest = data.messages.filter(turn => turn.role === 'assistant').at(-1);
    const evidenceBranches = [...new Set((latest?.evidence || []).flatMap(entry => entry.result.scope?.branch_ids || []))];
    messages.append(node('p', (evidenceBranches.length ? evidenceBranches : data.scope.branch_ids).map(branchName).join(' · '), 'assistant-source-meta'));
    data.messages.forEach((turn, index) => {
      const article = node('article', undefined, `assistant-turn ${turn.role === 'user' ? 'assistant-question-turn' : 'assistant-answer'}`);
      article.append(node('h3', turn.role === 'user' ? 'You' : 'Planning brief'));
      if (turn.answer?.kind === 'deterministic_local_evidence') {
        const answer = turn.answer;
        article.append(node('p', answer.summary));
        const findings = node('ul', undefined, 'assistant-findings');
        for (const finding of answer.findings) {
          const li = node('li', undefined, 'assistant-finding');
          li.append(node('strong', `${finding.branch_id == null ? 'Authorized scope' : branchName(finding.branch_id)} · ${toolNames[finding.condition] || finding.condition}`), node('p', finding.text));
          for (const citation of finding.citations) {
            const link = node('a', `Source ${citation}`); link.href = `#assistant-source-${index}-${citation}`;
            link.addEventListener('click', event => {
              event.preventDefault();
              const target = document.getElementById(`assistant-source-${index}-${citation}`);
              if (target) { for (let parent=target;parent;parent=parent.parentElement) if (parent.tagName==='DETAILS') parent.open=true; target.querySelector('summary').focus(); }
            });
            li.append(link);
          }
          findings.append(li);
        }
        article.append(findings);
        const note = node('div', undefined, 'notice neutral');
        answer.limitations.forEach(text => note.append(node('p', text)));
        note.append(node('small', answer.disclosure)); article.append(note);
        article.append(node('p', `Coverage: ${answer.coverage_complete ? 'complete for the reported query' : 'incomplete'} · ${answer.covered_rows} verified rows.`, 'assistant-source-meta'));
      } else if (turn.role === 'user' || !planningBrief(article, turn.evidence || [])) article.append(node('p', turn.text));
      if (turn.role === 'assistant') factBrief(article, turn.evidence || []);
      if (turn.evidence?.length) {
        const sources = node('details', undefined, 'assistant-sources'); sources.append(node('summary', `Sources & exact figures (${turn.evidence.length})`));
        for (const entry of turn.evidence) sources.append(source(entry, index)); article.append(sources);
      }
      messages.append(article);
    });
  }
  function showFailure(error) {
    status.textContent = error.message;
    if (error.code) {
      const details = node('details'); details.append(node('summary', 'Diagnostic code'), node('code', error.code)); messages.append(details);
    }
  }
  document.addEventListener('click', event => {
    const button = event.target.closest('[data-assistant-prompt]');
    if (!button) return;
    if (busy) return;
    input.value = button.dataset.assistantPrompt; input.focus();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (busy || !form.reportValidity()) return;
    const requestVersion = ++version;
    busy = true; cancelled = false; send.disabled = true; cancel.disabled = false; forget.disabled = true;
    status.textContent = 'Reading authorized evidence…'; messages.setAttribute('aria-busy', 'true');
    try {
      if (!conversation) conversation = (await api('/api/assistant/conversations', {})).id;
      if (cancelled) { status.textContent = 'Request cancelled.'; return; }
      const request = {text: input.value};
      const selectedBranch = Number(panel.dataset.selectedBranch);
      if (panel.dataset.localMode === 'true' && Number.isInteger(selectedBranch) && selectedBranch > 0) request.context = JSON.stringify({branch_id:selectedBranch});
      const data = await api(`/api/assistant/conversations/${conversation}/messages`, request);
      if (!cancelled && version === requestVersion) { render(data); input.value = ''; status.textContent = 'Brief ready · check the sources before acting'; messages.lastElementChild?.scrollIntoView({block:'nearest'}); }
    } catch (error) {
      messages.replaceChildren(); showFailure(error);
    } finally {
      busy = false; send.disabled = false; cancel.disabled = true; forget.disabled = false;
      messages.removeAttribute('aria-busy');
      if (panel.open && !cancelled) input.focus();
    }
  });
  async function requestCancel() {
    if (!busy) return;
    cancelled = true; status.textContent = 'Cancellation requested…';
    if (conversation) {
      try { await api(`/api/assistant/conversations/${conversation}/cancel`, {}); status.textContent = 'Waiting for the authorized read to stop.'; }
      catch (error) { showFailure(error); }
    }
  }
  cancel.addEventListener('click', requestCancel);
  panel.addEventListener('close', () => { ++version; requestCancel(); messages.replaceChildren(); empty.hidden = false; });
  new MutationObserver(async () => {
    if (!panel.open || !conversation || busy) return;
    const requestVersion = ++version;
    try {
      const data = await api(`/api/assistant/conversations/${conversation}`, undefined, 'GET');
      if (panel.open && version === requestVersion) render(data);
    } catch (error) { if (panel.open && version === requestVersion) { messages.replaceChildren(); showFailure(error); } }
  }).observe(panel, {attributes: true, attributeFilter: ['open']});
  forget.addEventListener('click', async () => {
    try {
      if (conversation) await api(`/api/assistant/conversations/${conversation}/forget`, {});
      ++version; conversation = null; messages.replaceChildren(); empty.hidden = false; status.textContent = 'Conversation cleared.'; input.focus();
    } catch (error) { messages.replaceChildren(); showFailure(error); }
  });
})();
