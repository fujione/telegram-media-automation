const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../src/telegram_media_automation/web/app.js'), 'utf8');
const functions = source.slice(source.indexOf('function resetReplacementTarget('), source.indexOf('async function importTask('));

function fixture() {
  const nodes = new Map();
  function $(selector) {
    if (!nodes.has(selector)) nodes.set(selector, {
      value: '', checked: false, disabled: false, required: false, hidden: false,
      options: [], replaceChildren(...options) { this.options = options; this.value = ''; },
      add(option) {this.options.push(option);},
    });
    return nodes.get(selector);
  }
  const context = vm.createContext({$, URLSearchParams, t: value => value, importIsReplacementPlan: false,
    Option: function(text, value) {this.text = text; this.value = value;},
    humanBytes: value => `${value} bytes`, toast: () => {},
    runBusy: async (_button, _label, action) => action(),
    api: async () => [{path: '/media/tv/Show/Season 1/Show - S01E22.mp4', file_size: 42}],
  });
  vm.runInContext(functions, context);
  return {context, $};
}

test('replacement requires a selected library target and hides download-only', () => {
  const {context, $} = fixture();
  $('#enqueue-replace').checked = true;
  context.updateReplacementMode('enqueue');
  assert.equal($('#enqueue-target').required, true);
  assert.equal($('#enqueue-target').disabled, false);
  assert.equal($('#enqueue-download-only').hidden, true);
  assert.equal($('#enqueue-form button[type=submit]').textContent, '下载并自动替换');
  $('#enqueue-replace').checked = false;
  context.updateReplacementMode('enqueue');
  assert.equal($('#enqueue-target').required, false);
  assert.equal($('#enqueue-target').disabled, true);
  assert.equal($('#enqueue-download-only').hidden, false);
});

test('completed download replacement requires confirmed identity', () => {
  const {context, $} = fixture();
  $('#import-replace').checked = true;
  context.updateReplacementMode('import');
  assert.equal($('#import-confirmed').required, true);
  assert.equal($('#import-target').required, true);
});

test('lookup never auto-selects a file and reset clears stale selection', async () => {
  const {context, $} = fixture();
  $('#enqueue-title').value = 'Show';
  $('#enqueue-kind').value = 'series';
  await context.findReplacementTarget('enqueue');
  assert.equal($('#enqueue-target').options.length, 2);
  assert.equal($('#enqueue-target').value, '');
  assert.equal($('#enqueue-target').options[1].value, '/media/tv/Show/Season 1/Show - S01E22.mp4');
  $('#enqueue-target').value = '/media/tv/stale.mp4';
  context.resetReplacementTarget('enqueue');
  assert.equal($('#enqueue-target').value, '');
});

test('lookup discards an async result if identity changes', async () => {
  const {context, $} = fixture();
  $('#enqueue-title').value = 'Show';
  $('#enqueue-kind').value = 'series';
  context.api = async () => {
    $('#enqueue-title').value = 'Another Show';
    return [{path: '/media/tv/stale.mp4', file_size: 42}];
  };
  await context.findReplacementTarget('enqueue');
  assert.equal($('#enqueue-target').options.length, 1);
});
