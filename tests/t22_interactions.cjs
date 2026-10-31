// Exercise the real progressive enhancement code without a browser or extra packages.
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

class Element {
  constructor(kind = 'div') {
    this.kind = kind;
    this.attributes = new Map();
    this.events = new Map();
    this.children = [];
    this.dataset = {};
    this.hidden = false;
    this.inert = false;
    this.isConnected = true;
    this.visible = true;
    this.open = false;
    this.id = '';
    this.queries = new Map();
    this.style = {};
  }

  append(child) {
    this.children.push(child);
    child.parentElement = this;
    return child;
  }

  appendChild(child) { return this.append(child); }
  insertBefore(child) { return this.append(child); }
  replaceChildren(...children) {
    this.children = [];
    children.forEach(child => this.append(child));
  }

  setAttribute(name, value) { this.attributes.set(name, String(value)); }
  getAttribute(name) { return this.attributes.get(name) ?? null; }
  removeAttribute(name) { this.attributes.delete(name); }
  addEventListener(name, listener) { this.events.set(name, listener); }
  emit(name, extra = {}) { return this.events.get(name)?.(event(this, extra)); }
  focus() { document.activeElement = this; }
  scrollIntoView() { this.scrolled = true; }

  contains(element) {
    return this === element || this.children.some(child => child.contains(element));
  }

  closest(selector) {
    for (let element = this; element; element = element.parentElement) {
      if (selector === '[inert], [hidden]' && (element.inert || element.hidden)) return element;
      if (selector === 'details[open]' && element.kind === 'details' && element.open) return element;
      if (selector === 'form' && element instanceof Form) return element;
    }
    return null;
  }

  getClientRects() {
    return !this.visible || this.closest('[inert], [hidden]') ? [] : [{}];
  }

  querySelector(selector) { return this.queries.get(selector) ?? null; }
  querySelectorAll(selector) {
    if (selector.includes('button:not([disabled])')) return this.controls || [];
    return [];
  }

  insertAdjacentElement(position, element) {
    assert.equal(position, 'afterend');
    this.parentElement.append(element);
  }

  remove() {
    this.isConnected = false;
    this.parentElement.children = this.parentElement.children.filter(child => child !== this);
  }
}

class Form extends Element {
  constructor() { super('form'); }
}

function event(target, extra = {}) {
  return {
    target,
    defaultPrevented: false,
    preventDefault() { this.defaultPrevented = true; },
    ...extra,
  };
}

const documentEvents = new Map();
const frameCallbacks = [];
const selections = new Map();
const ids = new Map();
const classes = new Set();
const media = { matches: true, addEventListener(name, callback) { this.change = callback; } };
const summaryList = [];
const disclosures = [];

global.HTMLFormElement = Form;
global.document = {
  activeElement: null,
  body: {
    classList: {
      contains(value) { return classes.has(value); },
      toggle(value, on) { if (on) classes.add(value); else classes.delete(value); },
    },
  },
  getElementById(id) { return ids.get(id) ?? null; },
  querySelector(selector) { return selections.get(selector)?.[0] ?? null; },
  querySelectorAll(selector) {
    if (selector === '.validation-summary') return summaryList;
    if (selector === 'details[open]') return disclosures.filter(item => item.open);
    return selections.get(selector) ?? [];
  },
  createElement(kind) { return new Element(kind); },
  addEventListener(name, callback) { documentEvents.set(name, callback); },
};
global.matchMedia = () => media;
global.window = { confirm: () => true };
global.requestAnimationFrame = callback => frameCallbacks.push(callback);

function dispatch(name, target = document.activeElement, extra = {}) {
  const result = event(target, extra);
  documentEvents.get(name)?.(result);
  return result;
}

const mode = process.argv[2];
let menu;
let sidebar;
let innerClose;
let currentPage;
let last;
let background;
let alreadyInert;
let backdrop;
let dialog;
let triggers;
let dialogClose;
let dialogLink;
let loading;
let empty;
let unavailable;
let summary;
let form;
let control;
let field;
let disclosure;
let rowSummary;
let rowControl;
let ordinaryDisclosure;
let legacyValidityChecks = 0;

if (mode.startsWith('navigation')) {
  menu = new Element('button');
  sidebar = new Element('aside');
  sidebar.setAttribute('role', 'complementary');
  innerClose = sidebar.append(new Element('button'));
  currentPage = sidebar.append(new Element('a'));
  last = sidebar.append(new Element('button'));
  sidebar.controls = [innerClose, currentPage, last];
  sidebar.queries.set('[aria-current="page"]', currentPage);
  background = new Element();
  alreadyInert = new Element();
  alreadyInert.inert = true;
  backdrop = new Element('button');
  ids.set('primary-sidebar', sidebar);
  selections.set('[data-nav-toggle]', [menu]);
  selections.set('[data-nav-close]', [backdrop, innerClose]);
  selections.set('[data-nav-background]', [background, alreadyInert]);
  menu.focus();
}

if (mode === 'dialog') {
  dialog = new Element('dialog');
  triggers = [new Element('button'), new Element('button')];
  dialogClose = dialog.append(new Element('button'));
  dialogLink = dialog.append(new Element('a'));
  dialog.controls = [dialogClose, dialogLink];
  loading = dialog.append(new Element());
  empty = dialog.append(new Element());
  unavailable = dialog.append(new Element());
  dialog.queries.set('[data-assistant-close]', dialogClose);
  dialog.queries.set('[data-assistant-loading]', loading);
  dialog.queries.set('[data-assistant-empty]', empty);
  dialog.queries.set('[data-assistant-unavailable]', unavailable);
  dialog.showModal = () => { dialog.open = true; };
  dialog.close = () => { dialog.open = false; dialog.emit('close'); };
  ids.set('assistant-panel', dialog);
  selections.set('[data-assistant-open]', triggers);
}

if (mode.startsWith('validation') || mode === 'summary') {
  field = new Element();
  form = new Form();
  control = field.append(new Element('input'));
  control.form = form;
  control.willValidate = true;
  control.validationMessage = 'Enter a positive whole quantity.';
  control.validity = { valid: false };
  control.setAttribute('aria-describedby', 'quantity-hint');
  if (mode === 'validation-preserve') {
    control.setAttribute('aria-invalid', 'true');
    control.setAttribute('aria-describedby', 'quantity-hint quantity-server-error');
  }
  if (mode === 'summary') {
    const parent = new Element();
    summary = parent.append(new Element());
    parent.append(form);
    parent.queries.set('form', form);
    summaryList.push(summary);
  }
}

if (mode === 'disclosure') {
  disclosure = new Element('details');
  disclosure.open = true;
  rowSummary = disclosure.append(new Element('summary'));
  rowControl = disclosure.append(new Element('input'));
  disclosure.queries.set('.row-action-panel', new Element());
  disclosure.queries.set('summary', rowSummary);
  ordinaryDisclosure = new Element('details');
  ordinaryDisclosure.open = true;
  disclosures.push(disclosure, ordinaryDisclosure);
}

if (mode === 'legacy-validation') {
  const parent = new Element();
  form = parent.append(new Form());
  form.setAttribute('aria-describedby', 'existing-form-help');
  form.checkValidity = () => { legacyValidityChecks++; return false; };
  ids.set('productForm', form);
  ['category', 'product', 'variant', 'supplier', 'quantity', 'reorder_level', 'expiry_date']
    .forEach(id => {
      const input = new Element();
      input.value = '';
      ids.set(id, input);
    });
  ids.get('quantity').value = '-1';
  ids.get('reorder_level').value = '-1';
  global.catalog = {};
  global.selectedValues = { product: '', variant: '', supplier: '' };
}

vm.runInThisContext(fs.readFileSync('static/js/workspace.js', 'utf8'));

if (mode === 'navigation') {
  assert.equal(sidebar.inert, true);
  assert.equal(menu.getAttribute('aria-expanded'), 'false');
  menu.emit('click');
  assert.equal(sidebar.inert, false);
  assert.equal(background.inert, true);
  assert.equal(sidebar.getAttribute('role'), 'dialog');
  assert.equal(sidebar.getAttribute('aria-modal'), 'true');
  assert.equal(document.activeElement, innerClose);
  assert.equal(backdrop.hidden, false);

  const reverse = dispatch('keydown', innerClose, { key: 'Tab', shiftKey: true });
  assert.equal(reverse.defaultPrevented, true);
  assert.equal(document.activeElement, last);
  const forward = dispatch('keydown', last, { key: 'Tab', shiftKey: false });
  assert.equal(forward.defaultPrevented, true);
  assert.equal(document.activeElement, innerClose);
  background.focus();
  dispatch('focusin', background);
  assert.equal(document.activeElement, innerClose);

  dispatch('keydown', innerClose, { key: 'Escape' });
  assert.equal(document.activeElement, menu);
  assert.equal(sidebar.inert, true);
  assert.equal(background.inert, false);
  assert.equal(alreadyInert.inert, true);
  assert.equal(sidebar.getAttribute('role'), 'complementary');
  assert.equal(sidebar.getAttribute('aria-modal'), null);
  assert.equal(menu.getAttribute('aria-expanded'), 'false');

  menu.emit('click');
  innerClose.emit('click');
  assert.equal(document.activeElement, menu);
  menu.emit('click');
  backdrop.emit('click');
  assert.equal(document.activeElement, menu);
} else if (mode === 'navigation-resize') {
  menu.emit('click');
  media.matches = false;
  menu.visible = false;
  innerClose.visible = false;
  media.change();
  assert.equal(sidebar.inert, false);
  assert.equal(background.inert, false);
  assert.equal(document.activeElement, currentPage);
  assert.equal(dispatch('keydown', last, { key: 'Tab' }).defaultPrevented, false);
  media.matches = true;
  menu.visible = true;
  innerClose.visible = true;
  media.change();
  assert.equal(sidebar.inert, true);
  assert.equal(document.activeElement, menu);
} else if (mode === 'dialog') {
  triggers[0].emit('click');
  assert.equal(dialog.open, true);
  assert.equal(document.activeElement, dialogClose);
  assert.equal(dialog.dataset.assistantState, 'ready');
  while (frameCallbacks.length) frameCallbacks.shift()();
  assert.equal(dialog.dataset.assistantState, 'ready');
  assert.equal(loading.hidden, true);
  assert.equal(empty.hidden, false);
  assert.equal(unavailable.hidden, false);
  const reverse = event(dialogClose, { key: 'Tab', shiftKey: true });
  dialog.events.get('keydown')(reverse);
  assert.equal(reverse.defaultPrevented, true);
  assert.equal(document.activeElement, dialogLink);
  const forward = event(dialogLink, { key: 'Tab', shiftKey: false });
  dialog.events.get('keydown')(forward);
  assert.equal(forward.defaultPrevented, true);
  assert.equal(document.activeElement, dialogClose);
  dialogClose.emit('click');
  assert.equal(document.activeElement, triggers[0]);
  assert.equal(triggers[0].getAttribute('aria-expanded'), 'false');
  triggers[1].emit('click');
  // A native Escape dismissal dispatches the same close event.
  dialog.close();
  assert.equal(document.activeElement, triggers[1]);
} else if (mode.startsWith('validation')) {
  dispatch('invalid', control);
  const error = field.children.find(child => child !== control);
  assert.equal(error.className, 'field-error');
  assert.equal(error.textContent, control.validationMessage);
  assert.equal(error.getAttribute('aria-live'), 'polite');
  assert.equal(control.getAttribute('aria-invalid'), 'true');
  assert.ok(control.getAttribute('aria-describedby').includes('quantity-hint'));
  assert.ok(control.getAttribute('aria-describedby').includes(error.id));
  dispatch('invalid', control);
  assert.equal(field.children.length, 2, 'Repeated checks must not duplicate inline errors');
  dispatch('input', control);
  assert.equal(error.isConnected, true, 'An invalid edit must retain the error');
  control.validity.valid = true;
  dispatch('change', control);
  assert.equal(error.isConnected, false);
  assert.equal(control.getAttribute('aria-describedby'), mode === 'validation-preserve'
    ? 'quantity-hint quantity-server-error' : 'quantity-hint');
  assert.equal(control.getAttribute('aria-invalid'), mode === 'validation-preserve' ? 'true' : null);
} else if (mode === 'summary') {
  assert.match(summary.id, /^form-validation-/);
  assert.equal(form.getAttribute('aria-describedby'), summary.id);
  assert.equal(control.getAttribute('aria-invalid'), null,
    'A general server error must not falsely mark an unrelated field invalid');
} else if (mode === 'disclosure') {
  rowControl.focus();
  dispatch('keydown', rowControl, { key: 'Escape' });
  assert.equal(disclosure.open, false);
  assert.equal(document.activeElement, rowSummary);
  assert.equal(ordinaryDisclosure.open, true);
  disclosure.open = true;
  dispatch('click', rowControl);
  assert.equal(disclosure.open, true, 'Clicks inside an action must retain the panel');
  dispatch('click', new Element());
  assert.equal(disclosure.open, false);
  assert.equal(ordinaryDisclosure.open, true, 'Evidence disclosures remain open');
} else if (mode === 'legacy-validation') {
  vm.runInThisContext(fs.readFileSync('static/js/product-form.js', 'utf8'));
  const rejected = event(form);
  form.events.get('submit')(rejected);
  assert.equal(rejected.defaultPrevented, true);
  assert.equal(legacyValidityChecks, 1);
  const alert = form.parentElement.children.find(child => child !== form);
  assert.equal(alert.getAttribute('role'), 'alert');
  assert.equal(alert.getAttribute('tabindex'), '-1');
  assert.equal(document.activeElement, alert);
  assert.equal(alert.scrolled, true);
  assert.ok(form.getAttribute('aria-describedby').includes('existing-form-help'));
  assert.ok(form.getAttribute('aria-describedby').includes(alert.id));
  // Error content is text, even if a future validator includes untrusted input.
  selections.set('.validation-summary', [alert]);
  showValidationErrors(['<img src=x onerror=alert(1)>']);
  assert.equal(alert.children[1].children[0].textContent, '<img src=x onerror=alert(1)>');
  assert.equal(alert.children[1].children[0].children.length, 0);
} else {
  throw new Error(`Unknown scenario: ${mode}`);
}
