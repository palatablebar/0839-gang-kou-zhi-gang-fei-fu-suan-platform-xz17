(function () {
  var state = { payload: null, kind: 'bars', run: null, approved: false, handover: null };
  var $ = function (id) { return document.getElementById(id); };
  function esc(value) {
    return String(value === null || value === undefined ? '' : value).replace(/[&<>"']/g, function (ch) {
      return {"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[ch];
    });
  }
  async function request(url, options) {
    var response = await fetch(url, Object.assign({headers: {'Content-Type': 'application/json'}}, options || {}));
    var data = await response.json().catch(function () { return {}; });
    if (!response.ok) throw new Error(data.error || ('HTTP ' + response.status));
    return data;
  }
  function setNotice(text) { $('notice').textContent = text || ''; }
  function renderIssues(data) {
    $('issue-count').textContent = data.issues.length + ' 条';
    $('issue-list').innerHTML = data.issues.length ? data.issues.map(function (item) {
      return '<li><span>' + esc(item.line || '-') + '</span><span>' + esc(item.key || '无主键') + '</span><code>' + esc(item.code) + '</code></li>';
    }).join('') : '<li class="empty">暂无异常</li>';
  }
  function renderBreakdown(items) {
    if (!items || !items.length) return '';
    return '<details><summary>' + items.length + ' 项</summary><ul>' + items.map(function (item) {
      return '<li>' + esc(item) + '</li>';
    }).join('') + '</ul></details>';
  }
  function renderRows(data) {
    var labels = Object.assign({label: '记录'}, state.resultColumns);
    var source = data.row_kind === 'source';
    var hasBreakdown = data.rows.some(function (row) { return row.breakdown && row.breakdown.length; });
    var keys = source ? state.inputColumns : Object.keys(labels).filter(function (key) {
      return data.rows.some(function (row) { return Object.hasOwn(row, key); });
    });
    $('row-count').textContent = source ? data.rows.length + ' 行预览' :
      data.summary.PASS + ' 通过 / ' + data.summary.REVIEW + ' 复核 / ' + data.summary.REJECT + ' 阻断';
    var tail = source ? '' : '<th>结论</th>' + (hasBreakdown ? '<th>计算分项</th>' : '');
    $('row-head').innerHTML = '<tr>' + keys.map(function (key) { return '<th>' + esc(labels[key] || key) + '</th>'; }).join('') + tail + '</tr>';
    $('row-body').innerHTML = data.rows.length ? data.rows.map(function (row) {
      var values = keys.map(function (key) { return '<td class="' + (typeof row[key] === 'number' ? 'num' : '') + '">' + esc(row[key]) + '</td>'; }).join('');
      var verdict = source ? '' : '<td><span class="verdict" data-v="' + esc(row.verdict) + '">' + esc(row.verdict || '') + '</span><br><small>' + esc(row.reason || '') + '</small></td>';
      var breakdown = hasBreakdown ? '<td>' + renderBreakdown(row.breakdown) + '</td>' : '';
      return '<tr>' + values + verdict + breakdown + '</tr>';
    }).join('') : '<tr><td class="empty" colspan="' + (keys.length + (source ? 0 : 1)) + '">没有可展示条目</td></tr>';
  }
  function renderLedger(data) {
    if (!data.ledger) { $('ledger-list').innerHTML = '<p class="empty">运行对账后显示</p>'; return; }
    var values = data.ledger;
    $('ledger-list').innerHTML = [
      ['来源行', values.counts.input], ['采信行', values.counts.accepted], ['隔离行', values.counts.isolated],
      ['精确重复', values.counts.duplicates], ['忽略行', values.counts.ignored], ['计算条目', values.counts.computed]
    ].map(function (row) { return '<div class="ledger-row"><span>' + row[0] + '</span><strong>' + row[1] + '</strong></div>'; }).join('') +
      '<div class="ledger-row"><span>' + esc(values.metric_field) + '</span><strong>' + esc(values.metric_total) + '</strong></div>' +
      values.checks.map(function (check) { return '<div class="ledger-row"><span>' + esc(check.label) + '</span><strong class="' + (check.ok ? 'check-ok' : 'check-bad') + '">' + (check.ok ? '通过' : '异常') + '</strong></div>'; }).join('');
  }
  function render(data) {
    state.payload = data;
    $('chart-label').textContent = Object.values(state.resultColumns)[0];
    $('m-input').textContent = data.counts.input;
    $('m-accepted').textContent = data.counts.accepted;
    $('m-isolated').textContent = data.counts.isolated + data.counts.duplicates;
    $('m-rejected').textContent = data.summary.REJECT;
    renderRows(data);
    renderIssues(data);
    renderLedger(data);
    window.renderChart($('chart'), data.chart && data.chart.length ? (state.kind || 'bars') : 'bars', data.chart || []);
  }
  function stageButtons(stages) {
    $('stage-nav').innerHTML = stages.map(function (stage, index) {
      return '<button type="button" data-stage="' + stage.id + '" aria-pressed="' + (index === 0 ? 'true' : 'false') + '">' + esc(stage.name) + '</button>';
    }).join('');
    Array.from(document.querySelectorAll('[data-stage]')).forEach(function (button) {
      button.addEventListener('click', function () {
        document.querySelectorAll('[data-stage]').forEach(function (other) { other.setAttribute('aria-pressed', String(other === button)); });
        loadStage(button.getAttribute('data-stage'));
      });
    });
  }
  async function loadStage(stage) {
    try {
      setNotice('');
      document.body.setAttribute('aria-busy', 'true');
      var payload = await request('/api/stages/' + stage, {method: 'POST', body: JSON.stringify({profile: $('profile').value})});
      render(payload);
    } catch (error) { setNotice(error.message); }
    finally { document.body.removeAttribute('aria-busy'); }
  }
  async function runBatch() {
    try {
      setNotice('');
      $('run').disabled = true;
      var run = await request('/api/runs', {method: 'POST', body: JSON.stringify({profile: $('profile').value})});
      state.run = run;
      state.approved = run.status === 'APPROVED';
      render(run);
      populateHandover(run);
      $('run-status').textContent = run.status + ' · rev ' + run.revision;
      $('revision').textContent = 'rev ' + run.revision;
      $('sign').disabled = run.summary.REJECT > 0 || state.approved;
      $('hold').disabled = state.approved;
      $('download').hidden = !state.approved;
      if (state.approved) $('download').href = '/api/runs/' + run.run_id + '/evidence';
    } catch (error) { setNotice(error.message); }
    finally { $('run').disabled = false; }
  }
  async function review(decision) {
    if (!state.run) return;
    try {
      var run = await request('/api/runs/' + state.run.run_id + '/review', {
        method: 'POST',
        body: JSON.stringify({decision: decision, comment: $('comment').value, expected_revision: state.run.revision})
      });
      state.run = run;
      state.approved = run.status === 'APPROVED';
      $('run-status').textContent = run.status + ' · rev ' + run.revision;
      $('revision').textContent = 'rev ' + run.revision;
      $('sign').disabled = true;
      $('hold').disabled = true;
      $('download').hidden = !state.approved;
      if (state.approved) $('download').href = '/api/runs/' + run.run_id + '/evidence';
    } catch (error) { setNotice(error.message); }
  }
  function populateHandover(run) {
    var rows = (run && (run.computed_rows || run.rows)) || [];
    var ids = Array.from(new Set(rows.map(function (row) { return row.container_id; }).filter(Boolean)));
    $('hv-container-options').innerHTML = ids.map(function (id) { return '<option value="' + esc(id) + '"></option>'; }).join('');
    $('hv-preview').disabled = !run;
    $('hv-issue').disabled = !run;
  }
  function handoverBody() {
    var dates = [$('hv-date-1').value, $('hv-date-2').value].filter(Boolean);
    return {
      container_id: $('hv-container').value.trim(),
      old_owner: $('hv-old').value.trim() || '旧货主',
      new_owner: $('hv-new').value.trim() || '新货主',
      first_handover_date: dates[0] || null,
      second_handover_date: dates[1] || null,
      handover_dates: dates
    };
  }
  function cents(value) { return (Number(value || 0)).toLocaleString('zh-CN') + ' 分'; }
  function ownerLabel(role, h) {
    if (role === 'old_owner') return '旧货主 · ' + esc(h.old_owner || '');
    if (role === 'new_owner') return '新货主 · ' + esc(h.new_owner || '');
    return '日期争议/待定';
  }
  function renderHandover(h) {
    state.handover = h;
    if (!h) { $('hv-result').innerHTML = '<p class="empty">暂无分账结果</p>'; return; }
    var rec = h.reconciliation || {};
    var rows = h.days || h.lines || [];
    var summary = [
      ['整箱复算额', h.whole_container_cents || h.total_cents],
      ['旧货主已签', h.signed_old_owner_cents],
      ['新货主已签', h.signed_new_owner_cents],
      ['两家已签合计', h.signed_cents],
      ['待定费用', h.pending_cents],
      ['对账差额', rec.difference_cents]
    ];
    var html = '<div class="handover-summary">' + summary.map(function (row) {
      return '<div class="ledger-row"><span>' + row[0] + '</span><strong>' + esc(cents(row[1])) + '</strong></div>';
    }).join('') + '</div>';
    html += '<p class="handover-balance ' + (rec.balanced ? 'check-ok' : 'check-bad') + '">' +
      (rec.balanced ? '对账平衡：' : '对账不平衡：') + esc(rec.formula || '') + '</p>';
    html += '<div class="table-wrap"><table><thead><tr>' +
      '<th>日费日期</th><th>全局计费日</th><th class="num">日费率</th><th class="num">金额</th><th>归属</th><th>状态</th><th>签发号</th>' +
      '</tr></thead><tbody>' + rows.map(function (day) {
        return '<tr>' +
          '<td>' + esc(day.date) + '</td>' +
          '<td>' + esc(day.day_number || day.charge_day || '') + '</td>' +
          '<td class="num">' + esc(cents(day.rate_cents)) + '</td>' +
          '<td class="num">' + esc(cents(day.amount_cents)) + '</td>' +
          '<td>' + ownerLabel(day.owner_role || day.assignment, h) + '</td>' +
          '<td><span class="verdict" data-v="' + (day.status === 'SIGNED' ? 'PASS' : (day.status === 'DISPUTED' ? 'REJECT' : 'REVIEW')) + '">' + esc(day.status) + '</span></td>' +
          '<td><small>' + esc(day.charge_id || '') + '</small></td>' +
        '</tr>';
      }).join('') + '</tbody></table></div>';
    $('hv-result').innerHTML = html;
    $('hv-retry').hidden = !h.handover_id;
  }
  async function previewHandover() {
    if (!state.run) return;
    try {
      setNotice('');
      var h = await request('/api/runs/' + state.run.run_id + '/handovers/preview', {
        method: 'POST', body: JSON.stringify(handoverBody())
      });
      renderHandover(h);
    } catch (error) { setNotice(error.message); }
  }
  async function issueHandover() {
    if (!state.run) return;
    try {
      setNotice('');
      $('hv-issue').disabled = true;
      var h = await request('/api/runs/' + state.run.run_id + '/handovers', {
        method: 'POST', body: JSON.stringify(handoverBody())
      });
      renderHandover(h);
      setNotice(h.reconciliation && h.reconciliation.balanced ? '交接分账已签发，争议日费已待定' : '交接分账对账异常');
    } catch (error) { setNotice(error.message); }
    finally { $('hv-issue').disabled = false; }
  }
  async function retryHandover() {
    if (!state.handover || !state.handover.handover_id) return;
    try {
      setNotice('');
      var h = await request('/api/handovers/' + state.handover.handover_id + '/issue', {method: 'POST', body: '{}'});
      renderHandover(h);
      setNotice('重试完成：同一天费用未重复签发');
    } catch (error) { setNotice(error.message); }
  }
  async function boot() {
    try {
      var catalog = await request('/api/catalog');
      state.kind = catalog.chart;
      state.resultColumns = catalog.result_columns;
      state.inputColumns = catalog.columns;
      stageButtons(catalog.stages.slice(0, 5));
      document.querySelector('.rail-note').textContent = catalog.code + ' · ' + catalog.stages[6].name;
      await loadStage('intake');
    } catch (error) { setNotice(error.message); }
    $('run').addEventListener('click', runBatch);
    $('sign').addEventListener('click', function () { review('APPROVED'); });
    $('hold').addEventListener('click', function () { review('HELD'); });
    $('profile').addEventListener('change', function () {
      state.run = null; state.approved = false; state.handover = null;
      $('sign').disabled = true; $('hold').disabled = true; $('download').hidden = true;
      $('hv-preview').disabled = true; $('hv-issue').disabled = true; $('hv-retry').hidden = true;
      $('hv-result').innerHTML = '<p class="empty">执行批次后，填写两份交接记录中的日期；无争议日期会签发，争议日期保留为待定费用。</p>';
      $('run-status').textContent = '未执行批次'; $('revision').textContent = 'rev 0';
      loadStage('intake');
    });
    $('hv-preview').addEventListener('click', previewHandover);
    $('hv-issue').addEventListener('click', issueHandover);
    $('hv-retry').addEventListener('click', retryHandover);
  }
  document.addEventListener('DOMContentLoaded', boot);
})();
