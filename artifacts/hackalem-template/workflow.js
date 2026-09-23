/* brieflyAI: one-page, local-first meeting workspace. No AI backend is implied. */
(() => {
  const STORAGE_KEY = 'brieflyai-meeting-workspace-v1';
  let mediaUrl = null;
  let recorder = null;
  let stream = null;
  let chunks = [];
  let elapsed = 0;
  let timer = null;
  let recordedBlob = null;

  const $ = (selector) => document.querySelector(selector);
  const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
  })[char]);
  const escapeXml = (value) => String(value ?? '').replace(/[&<>"']/g, (char) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&apos;'
  })[char]);

  function init() {
    const root = document.getElementById('screen0');
    if (!root) return;
    root.innerHTML = `
      <div class="wf" id="brieflyWorkflow">
        <div class="wf-intro">
          <div><span class="wf-eyebrow">brieflyAI · рабочее пространство</span><h1>Совещание → готовый протокол</h1><p>Всё на одном экране: запись, текст, поручения и документ.</p></div>
          <span class="wf-local-badge">Данные хранятся в браузере</span>
        </div>
        <div class="wf-grid">
          <section class="wf-card wf-source" aria-labelledby="wfSourceTitle">
            <div class="wf-card-heading"><span class="wf-step">1</span><div><h2 id="wfSourceTitle">Добавьте встречу</h2><p>Загрузите файл или запишите звук с микрофона</p></div></div>
            <label class="wf-label" for="wfTitle">Название совещания</label>
            <input class="wf-input" id="wfTitle" type="text" placeholder="Например, планирование пилотного запуска" autocomplete="off">
            <div class="wf-source-options">
              <label class="wf-upload" for="wfFile"><span class="wf-upload-icon" aria-hidden="true">↑</span><strong>Выбрать аудио или видео</strong><small>Файл можно сразу прослушать или посмотреть</small></label>
              <input id="wfFile" type="file" accept="audio/*,video/*" hidden>
              <div class="wf-or" aria-hidden="true">или</div>
              <div class="wf-mic"><div class="wf-mic-head"><strong>Записать с микрофона</strong><span id="wfTimer">00:00</span></div><label class="wf-consent"><input id="wfConsent" type="checkbox"> Участники уведомлены о записи</label><div class="wf-record-buttons"><button id="wfStart" class="wf-button wf-button-primary" type="button" disabled>● Начать</button><button id="wfPause" class="wf-button" type="button" disabled>Пауза</button><button id="wfStop" class="wf-button" type="button" disabled>Завершить</button></div></div>
            </div>
            <div id="wfMediaPanel" class="wf-media-panel" hidden><div class="wf-media-heading"><strong id="wfMediaName"></strong><a id="wfDownloadAudio" class="wf-inline-link" hidden>Скачать запись</a></div><div id="wfMediaPlayer"></div></div>
            <p id="wfRecordStatus" class="wf-status" role="status" aria-live="polite">Выберите файл или разрешите запись с микрофона.</p>
          </section>
          <section class="wf-card wf-text" aria-labelledby="wfTextTitle">
            <div class="wf-card-heading"><span class="wf-step">2</span><div><h2 id="wfTextTitle">Зафиксируйте итоги</h2><p>Текст можно редактировать и сохранить</p></div></div>
            <label class="wf-label" for="wfTranscript">Транскрипт</label><textarea class="wf-textarea wf-transcript" id="wfTranscript" placeholder="Вставьте расшифровку или запишите ключевые реплики…"></textarea>
            <label class="wf-label" for="wfSummary">Краткое саммари</label><textarea class="wf-textarea wf-summary" id="wfSummary" placeholder="Главные решения и итоги встречи…"></textarea>
            <p class="wf-honest-note">Автоматическое распознавание и саммари требуют подключения backend. Сейчас текст вводится вручную.</p>
          </section>
        </div>
        <section class="wf-card wf-tasks" aria-labelledby="wfTasksTitle">
          <div class="wf-card-heading wf-tasks-heading"><span class="wf-step">3</span><div><h2 id="wfTasksTitle">Поручения</h2><p>Суть, ответственный и срок исполнения</p></div><button id="wfAddTask" class="wf-button wf-button-light" type="button">+ Добавить поручение</button></div>
          <div id="wfTaskList" class="wf-task-list"></div>
          <p id="wfEmptyTasks" class="wf-empty">Поручений пока нет. Добавьте первое, если на встрече договорились о задаче.</p>
        </section>
        <div class="wf-actions"><span id="wfSaveStatus" class="wf-save-status" role="status" aria-live="polite">Черновик сохраняется автоматически</span><div><button id="wfSave" class="wf-button" type="button">Сохранить</button><button id="wfExportDocx" class="wf-button wf-button-light" type="button">Скачать DOCX</button><button id="wfExportPdf" class="wf-button wf-button-primary" type="button">Сохранить PDF…</button></div></div>
      </div>`;

    bind();
    restore();
    window.brieflyStopRecording = stopRecording;
  }

  function setStatus(message, error = false) {
    const el = $('#wfRecordStatus');
    el.textContent = message;
    el.classList.toggle('wf-error', error);
  }

  function setSaveStatus(message) { $('#wfSaveStatus').textContent = message; }

  function readData() {
    return {
      title: $('#wfTitle').value.trim(),
      transcript: $('#wfTranscript').value,
      summary: $('#wfSummary').value,
      tasks: [...document.querySelectorAll('.wf-task')].map((row) => ({
        description: row.querySelector('[data-field="description"]').value.trim(),
        owner: row.querySelector('[data-field="owner"]').value.trim(),
        deadline: row.querySelector('[data-field="deadline"]').value
      }))
    };
  }

  function save() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(readData()));
      setSaveStatus('Сохранено на этом устройстве · ' + new Date().toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }));
    } catch (_) {
      setSaveStatus('Не удалось сохранить: проверьте настройки браузера');
    }
  }

  function restore() {
    try {
      const data = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
      $('#wfTitle').value = data.title || '';
      $('#wfTranscript').value = data.transcript || '';
      $('#wfSummary').value = data.summary || '';
      if (Array.isArray(data.tasks)) data.tasks.forEach(addTask);
    } catch (_) {
      setSaveStatus('Сохранённый черновик не удалось открыть');
    }
    updateEmptyTasks();
  }

  function updateEmptyTasks() {
    $('#wfEmptyTasks').hidden = !!document.querySelector('.wf-task');
  }

  function addTask(data = {}) {
    const row = document.createElement('div');
    row.className = 'wf-task';
    row.innerHTML = `<label><span>Поручение</span><input data-field="description" type="text" placeholder="Что нужно сделать"></label><label><span>Ответственный</span><input data-field="owner" type="text" placeholder="Имя участника"></label><label><span>Срок</span><input data-field="deadline" type="date"></label><button class="wf-remove-task" type="button" aria-label="Удалить поручение" title="Удалить поручение">×</button>`;
    row.querySelector('[data-field="description"]').value = data.description || '';
    row.querySelector('[data-field="owner"]').value = data.owner || '';
    row.querySelector('[data-field="deadline"]').value = data.deadline || '';
    row.querySelector('.wf-remove-task').addEventListener('click', () => { row.remove(); updateEmptyTasks(); save(); });
    $('#wfTaskList').append(row);
    updateEmptyTasks();
  }

  function releaseStream() {
    if (stream) { stream.getTracks().forEach((track) => track.stop()); stream = null; }
  }

  function stopTimer() { if (timer) { clearInterval(timer); timer = null; } }

  function showElapsed() {
    $('#wfTimer').textContent = String(Math.floor(elapsed / 60)).padStart(2, '0') + ':' + String(elapsed % 60).padStart(2, '0');
  }

  function showMedia(blob, name, downloadable) {
    if (mediaUrl) URL.revokeObjectURL(mediaUrl);
    mediaUrl = URL.createObjectURL(blob);
    const isVideo = blob.type.startsWith('video/');
    const player = document.createElement(isVideo ? 'video' : 'audio');
    player.controls = true;
    player.preload = 'metadata';
    player.src = mediaUrl;
    $('#wfMediaPlayer').replaceChildren(player);
    $('#wfMediaName').textContent = name;
    $('#wfMediaPanel').hidden = false;
    const link = $('#wfDownloadAudio');
    link.hidden = !downloadable;
    if (downloadable) { link.href = mediaUrl; link.download = name; }
  }

  async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setStatus('Этот браузер не поддерживает запись с микрофона.', true);
      return;
    }
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: true, noiseSuppression: true } });
      const type = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4'].find((item) => MediaRecorder.isTypeSupported(item));
      recorder = new MediaRecorder(stream, type ? { mimeType: type } : undefined);
      chunks = [];
      elapsed = 0;
      showElapsed();
      recordedBlob = null;
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onstop = () => {
        stopTimer();
        releaseStream();
        recordedBlob = new Blob(chunks, { type: recorder.mimeType || 'audio/webm' });
        $('#wfStart').disabled = !$('#wfConsent').checked;
        $('#wfPause').disabled = true;
        $('#wfStop').disabled = true;
        if (!recordedBlob.size) { setStatus('Запись пустая. Попробуйте ещё раз.', true); return; }
        const extension = recordedBlob.type.includes('mp4') ? 'm4a' : 'webm';
        const name = 'brieflyAI-' + new Date().toISOString().slice(0, 19).replaceAll(':', '-') + '.' + extension;
        showMedia(recordedBlob, name, true);
        setStatus('Запись готова. Прослушайте или скачайте файл.');
      };
      recorder.onerror = () => { stopTimer(); releaseStream(); setStatus('Ошибка записи. Проверьте микрофон и повторите.', true); };
      recorder.start(1000);
      timer = setInterval(() => { if (recorder?.state === 'recording') { elapsed += 1; showElapsed(); } }, 1000);
      $('#wfStart').disabled = true;
      $('#wfPause').disabled = false;
      $('#wfPause').textContent = 'Пауза';
      $('#wfStop').disabled = false;
      setStatus('Идёт запись с микрофона…');
    } catch (error) {
      stopTimer();
      releaseStream();
      $('#wfStart').disabled = !$('#wfConsent').checked;
      setStatus(error?.name === 'NotAllowedError' ? 'Нет доступа к микрофону. Разрешите его в браузере.' : 'Не удалось включить микрофон: ' + (error?.message || 'проверьте устройство'), true);
    }
  }

  function togglePause() {
    if (!recorder) return;
    if (recorder.state === 'recording') { recorder.pause(); $('#wfPause').textContent = 'Продолжить'; setStatus('Запись на паузе.'); }
    else if (recorder.state === 'paused') { recorder.resume(); $('#wfPause').textContent = 'Пауза'; setStatus('Идёт запись с микрофона…'); }
  }

  function stopRecording() {
    if (recorder && recorder.state !== 'inactive') recorder.stop();
  }

  function bind() {
    let saveDelay;
    $('#brieflyWorkflow').addEventListener('input', (event) => {
      if (event.target.matches('input,textarea') && event.target.id !== 'wfFile') {
        clearTimeout(saveDelay);
        setSaveStatus('Сохраняем…');
        saveDelay = setTimeout(save, 250);
      }
    });
    $('#wfSave').addEventListener('click', save);
    $('#wfAddTask').addEventListener('click', () => { addTask(); save(); document.querySelector('.wf-task:last-child [data-field="description"]').focus(); });
    $('#wfFile').addEventListener('change', (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      if (!file.type.startsWith('audio/') && !file.type.startsWith('video/')) { setStatus('Выберите аудио- или видеофайл.', true); return; }
      showMedia(file, file.name, false);
      setStatus('Файл открыт для просмотра. После обновления страницы его нужно выбрать снова.');
    });
    $('#wfConsent').addEventListener('change', () => { if (!recorder || recorder.state === 'inactive') $('#wfStart').disabled = !$('#wfConsent').checked; });
    $('#wfStart').addEventListener('click', startRecording);
    $('#wfPause').addEventListener('click', togglePause);
    $('#wfStop').addEventListener('click', stopRecording);
    $('#wfExportDocx').addEventListener('click', exportDocx);
    $('#wfExportPdf').addEventListener('click', exportPdf);
  }

  function downloadBlob(blob, filename) {
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = filename;
    document.body.append(link);
    link.click();
    link.remove();
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  }

  function filenameBase(data) {
    return (data.title || 'протокол-совещания').replace(/[\\/:*?"<>|]/g, '-').slice(0, 70);
  }

  function docParagraph(text, bold = false) {
    return '<w:p><w:r>' + (bold ? '<w:rPr><w:b/><w:sz w:val="28"/></w:rPr>' : '') + '<w:t xml:space="preserve">' + escapeXml(text) + '</w:t></w:r></w:p>';
  }

  function makeDocxXml(data) {
    const tasks = data.tasks.filter((task) => task.description || task.owner || task.deadline);
    const parts = [docParagraph(data.title || 'Протокол совещания', true), docParagraph('Создано в brieflyAI · ' + new Date().toLocaleDateString('ru-RU')),
      docParagraph('Краткое саммари', true), ...String(data.summary || 'Не заполнено').split('\n').map((line) => docParagraph(line || ' ')),
      docParagraph('Поручения', true)];
    if (!tasks.length) parts.push(docParagraph('Поручения не добавлены.'));
    tasks.forEach((task, index) => parts.push(docParagraph((index + 1) + '. ' + (task.description || 'Без описания') + ' — ' + (task.owner || 'ответственный не указан') + ' — ' + (task.deadline || 'срок не указан'))));
    parts.push(docParagraph('Транскрипт', true));
    parts.push(...String(data.transcript || 'Не заполнено').split('\n').map((line) => docParagraph(line || ' ')));
    return '<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>' + parts.join('') + '<w:sectPr><w:pgSz w:w="11906" w:h="16838"/><w:pgMar w:top="1134" w:right="1134" w:bottom="1134" w:left="1134"/></w:sectPr></w:body></w:document>';
  }

  function crc32(bytes) {
    let crc = 0xffffffff;
    for (const byte of bytes) {
      crc ^= byte;
      for (let i = 0; i < 8; i++) crc = (crc >>> 1) ^ ((crc & 1) ? 0xedb88320 : 0);
    }
    return (crc ^ 0xffffffff) >>> 0;
  }

  function zipStore(entries) {
    const encoder = new TextEncoder();
    const locals = [], centrals = [];
    let offset = 0;
    const date = 33; // 1980-01-01, valid DOS ZIP date
    for (const [name, content] of entries) {
      const filename = encoder.encode(name), bytes = encoder.encode(content), crc = crc32(bytes);
      const local = new Uint8Array(30 + filename.length + bytes.length), l = new DataView(local.buffer);
      l.setUint32(0, 0x04034b50, true); l.setUint16(4, 20, true); l.setUint16(6, 0, true); l.setUint16(8, 0, true);
      l.setUint16(10, 0, true); l.setUint16(12, date, true); l.setUint32(14, crc, true);
      l.setUint32(18, bytes.length, true); l.setUint32(22, bytes.length, true); l.setUint16(26, filename.length, true); l.setUint16(28, 0, true);
      local.set(filename, 30); local.set(bytes, 30 + filename.length); locals.push(local);
      const central = new Uint8Array(46 + filename.length), c = new DataView(central.buffer);
      c.setUint32(0, 0x02014b50, true); c.setUint16(4, 20, true); c.setUint16(6, 20, true); c.setUint16(8, 0, true); c.setUint16(10, 0, true);
      c.setUint16(12, 0, true); c.setUint16(14, date, true); c.setUint32(16, crc, true);
      c.setUint32(20, bytes.length, true); c.setUint32(24, bytes.length, true); c.setUint16(28, filename.length, true);
      c.setUint16(30, 0, true); c.setUint16(32, 0, true); c.setUint16(34, 0, true); c.setUint16(36, 0, true);
      c.setUint32(38, 0, true); c.setUint32(42, offset, true); central.set(filename, 46); centrals.push(central);
      offset += local.length;
    }
    const centralSize = centrals.reduce((sum, chunk) => sum + chunk.length, 0);
    const end = new Uint8Array(22), e = new DataView(end.buffer);
    e.setUint32(0, 0x06054b50, true); e.setUint16(4, 0, true); e.setUint16(6, 0, true);
    e.setUint16(8, entries.length, true); e.setUint16(10, entries.length, true);
    e.setUint32(12, centralSize, true); e.setUint32(16, offset, true); e.setUint16(20, 0, true);
    return new Blob([...locals, ...centrals, end], { type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' });
  }

  function exportDocx() {
    const data = readData();
    save();
    const contentTypes = '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>';
    const rels = '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>';
    const blob = zipStore([['[Content_Types].xml', contentTypes], ['_rels/.rels', rels], ['word/document.xml', makeDocxXml(data)]]);
    downloadBlob(blob, filenameBase(data) + '.docx');
    setSaveStatus('DOCX скачан');
  }

  function exportPdf() {
    const data = readData();
    save();
    const tasks = data.tasks.filter((task) => task.description || task.owner || task.deadline);
    const printWindow = window.open('', '_blank');
    if (!printWindow) { setSaveStatus('Браузер заблокировал окно PDF. Разрешите всплывающие окна.'); return; }
    const taskRows = tasks.map((task) => '<tr><td>' + escapeHtml(task.description) + '</td><td>' + escapeHtml(task.owner) + '</td><td>' + escapeHtml(task.deadline) + '</td></tr>').join('');
    printWindow.document.write('<!doctype html><html lang="ru"><meta charset="utf-8"><title>' + escapeHtml(data.title || 'Протокол совещания') + '</title><style>body{font:15px/1.5 Arial,sans-serif;color:#172b4d;max-width:800px;margin:42px auto;padding:0 30px}h1{font-size:28px;margin:0 0 6px}h2{font-size:18px;margin:30px 0 10px;border-bottom:1px solid #d7e4f8;padding-bottom:6px}.meta{color:#64748b}pre{font:inherit;white-space:pre-wrap;overflow-wrap:anywhere}table{width:100%;border-collapse:collapse}th,td{padding:9px;border:1px solid #d7e4f8;text-align:left;vertical-align:top}th{background:#eef5ff}@page{margin:15mm}</style><h1>' + escapeHtml(data.title || 'Протокол совещания') + '</h1><div class="meta">brieflyAI · ' + new Date().toLocaleDateString('ru-RU') + '</div><h2>Краткое саммари</h2><pre>' + escapeHtml(data.summary || 'Не заполнено') + '</pre><h2>Поручения</h2>' + (tasks.length ? '<table><thead><tr><th>Поручение</th><th>Ответственный</th><th>Срок</th></tr></thead><tbody>' + taskRows + '</tbody></table>' : '<p>Поручения не добавлены.</p>') + '<h2>Транскрипт</h2><pre>' + escapeHtml(data.transcript || 'Не заполнено') + '</pre></html>');
    printWindow.document.close();
    printWindow.focus();
    setTimeout(() => printWindow.print(), 350);
    setSaveStatus('Открылось окно печати — выберите «Сохранить как PDF»');
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
  else init();
})();
