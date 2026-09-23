import { useEffect, useMemo, useRef, useState } from 'react';

const API = '/api';

function localDate() {
  const now = new Date();
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000).toISOString().slice(0, 10);
}

function clock(seconds = 0) {
  const value = Math.max(0, Math.floor(Number(seconds) || 0));
  return `${String(Math.floor(value / 60)).padStart(2, '0')}:${String(value % 60).padStart(2, '0')}`;
}

function csvCell(value) {
  let text = String(value ?? '');
  if (/^\s*[=+\-@]/.test(text)) text = `'${text}`;
  return `"${text.replaceAll('"', '""')}"`;
}

function downloadBlob(blob, filename) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}

function App() {
  const [file, setFile] = useState(null);
  const [title, setTitle] = useState('Совещание');
  const [meetingDate, setMeetingDate] = useState(localDate);
  const [speakers, setSpeakers] = useState('0');
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [result, setResult] = useState(null);
  const [actions, setActions] = useState([]);
  const [speakerNames, setSpeakerNames] = useState({});
  const [audioUrl, setAudioUrl] = useState('');
  const [recordingState, setRecordingState] = useState('idle');
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [summary, setSummary] = useState('');
  const recorderRef = useRef(null);
  const streamRef = useRef(null);
  const timerRef = useRef(null);
  const audioUrlRef = useRef('');
  const speakerIds = useMemo(() => [...new Set((result?.transcript ?? []).map((line) => line.speaker).filter(Boolean))], [result]);

  useEffect(() => () => {
    clearInterval(timerRef.current);
    if (recorderRef.current?.state !== 'inactive') recorderRef.current?.stop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
  }, []);

  function chooseFile(nextFile) {
    if (nextFile && nextFile.size > 512 * 1024 * 1024) {
      setError('Файл больше 512 МБ. Выберите запись меньшего размера.');
      return;
    }
    setFile(nextFile);
    setResult(null);
    setActions([]);
    setError('');
    if (audioUrlRef.current) URL.revokeObjectURL(audioUrlRef.current);
    audioUrlRef.current = nextFile ? URL.createObjectURL(nextFile) : '';
    setAudioUrl(audioUrlRef.current);
  }

  async function startRecording() {
    if (!consent) return;
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
      setError('Этот браузер не поддерживает запись с микрофона. Загрузите аудиофайл.');
      return;
    }
    setError('');
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      streamRef.current = stream;
      chooseFile(null);
      const mimeType = ['audio/webm;codecs=opus', 'audio/mp4', 'audio/ogg;codecs=opus'].find((type) => MediaRecorder.isTypeSupported(type));
      const recorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
      const chunks = [];
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => setError('Не удалось записать звук. Проверьте микрофон и повторите попытку.');
      recorder.onstop = () => {
        clearInterval(timerRef.current);
        stream.getTracks().forEach((track) => track.stop());
        streamRef.current = null;
        if (chunks.length) {
          const type = recorder.mimeType || mimeType || 'audio/webm';
          const extension = type.includes('mp4') ? 'm4a' : type.includes('ogg') ? 'ogg' : 'webm';
          chooseFile(new File(chunks, `brieflyAI-${Date.now()}.${extension}`, { type }));
          setRecordingState('ready');
        } else {
          setRecordingState('idle');
          setError('Запись пуста. Проверьте микрофон и повторите попытку.');
        }
      };
      setRecordingSeconds(0);
      recorder.start(1000);
      setRecordingState('recording');
      timerRef.current = setInterval(() => setRecordingSeconds((seconds) => seconds + 1), 1000);
    } catch (caught) {
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      setRecordingState('idle');
      setError(caught.name === 'NotAllowedError' ? 'Разрешите доступ к микрофону в браузере и попробуйте ещё раз.' : 'Микрофон недоступен. Подключите его или загрузите файл.');
    }
  }

  function toggleRecording() {
    const recorder = recorderRef.current;
    if (!recorder) return;
    if (recorder.state === 'recording') {
      recorder.pause();
      clearInterval(timerRef.current);
      setRecordingState('paused');
    } else if (recorder.state === 'paused') {
      recorder.resume();
      timerRef.current = setInterval(() => setRecordingSeconds((seconds) => seconds + 1), 1000);
      setRecordingState('recording');
    }
  }

  function finishRecording() {
    const recorder = recorderRef.current;
    if (recorder && recorder.state !== 'inactive') {
      setRecordingState('finishing');
      recorder.stop();
    }
  }

  async function processMeeting(event) {
    event.preventDefault();
    if (!file || !consent) return;
    setBusy(true);
    setError('');
    setResult(null);
    const form = new FormData();
    form.append('file', file);
    form.append('title', title);
    form.append('meeting_date', meetingDate);
    form.append('expected_speakers', speakers || '0');
    form.append('consent', String(consent));
    try {
      const response = await fetch(`${API}/meetings/process`, { method: 'POST', body: form });
      const body = await response.json();
      if (!response.ok) throw new Error(body.detail || 'Не удалось обработать запись.');
      setResult(body);
      setSummary(body.summary ?? '');
      setActions(body.actions ?? []);
      setSpeakerNames(Object.fromEntries((body.participants ?? []).map((person) => [person.speaker, person.name || ''])));
    } catch (caught) {
      setError(caught.message || 'Не удалось связаться с сервером обработки.');
    } finally {
      setBusy(false);
    }
  }

  function updateAction(index, field, value) {
    setActions((current) => current.map((action, position) => position === index ? { ...action, [field]: value } : action));
  }

  async function exportDocument(format) {
    const transcript = (result.transcript ?? []).map((line) => ({ ...line, speaker: speakerNames[line.speaker] || line.speaker }));
    const participants = speakerIds.map((speaker) => ({ speaker, name: speakerNames[speaker] || speaker }));
    try {
      const response = await fetch(`${API}/meetings/export`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ format, title: result.title, date: result.date, summary, actions, transcript, participants, summary_items: result.summary_items ?? [] }),
      });
      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || 'Не удалось создать файл.');
      }
      const safeName = (result.title || 'meeting').replace(/[^\p{L}\p{N}_-]+/gu, '_');
      downloadBlob(await response.blob(), `${safeName}.${format}`);
    } catch (caught) {
      setError(caught.message || 'Не удалось скачать файл.');
    }
  }

  function exportCsv() {
    const headings = ['Поручение', 'Ответственный', 'Спикер', 'Срок', 'Дата срока', 'Статус', 'Цитата'];
    const rows = actions.map((action) => [action.task, action.assignee, speakerNames[action.speaker] || action.speaker, action.deadline, action.deadline_iso, action.status, action.source_quote]);
    const csv = `\uFEFF${[headings, ...rows].map((row) => row.map(csvCell).join(',')).join('\r\n')}`;
    downloadBlob(new Blob([csv], { type: 'text/csv;charset=utf-8' }), 'porucheniya.csv');
  }

  return (
    <main className="page-shell">
      <header className="topbar">
        <a className="brand" href="#top"><span className="brand-icon">b</span><span><b>brieflyAI</b><small>Протокол совещания за минуты</small></span></a>
        <span className="privacy-pill"><i />Данные обрабатываются локально</span>
      </header>
      <section className="hero" id="top">
        <p className="eyebrow">ЗАПИСЬ → ПРОВЕРКА → ГОТОВЫЙ ДОКУМЕНТ</p>
        <h1>Совещание прошло.<br /><em>Решения остались.</em></h1>
        <p className="hero-copy">Запишите встречу или добавьте файл. Мы соберём расшифровку, поручения и краткий итог на одной странице.</p>
      </section>

      <section className="upload-card card">
        <div className="section-title"><span className="step">1</span><div><h2>Добавьте встречу</h2><p>Запишите звук или загрузите готовый файл</p></div></div>
        <form onSubmit={processMeeting}>
          <div className="form-grid">
            <label>Название совещания<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength="160" /></label>
            <label>Дата встречи<input type="date" value={meetingDate} onChange={(event) => setMeetingDate(event.target.value)} required /></label>
            <label>Участников (подсказка)<input type="number" min="0" max="30" value={speakers} onChange={(event) => setSpeakers(event.target.value)} /></label>
          </div>
          <label className="consent"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>Участники согласны на запись и обработку совещания.</span></label>
          <div className="source-grid">
            <div className="record-panel">
              <span className="source-label">С микрофона</span>
              <strong>{recordingState === 'recording' ? 'Идёт запись' : recordingState === 'paused' ? 'Запись на паузе' : recordingState === 'finishing' ? 'Сохраняем запись…' : 'Запишите встречу сейчас'}</strong>
              <span className="record-time" aria-live="polite">{clock(recordingSeconds)}</span>
              <div className="record-controls">
                {recordingState === 'recording' || recordingState === 'paused' ? <><button type="button" className="quiet-button" onClick={toggleRecording}>{recordingState === 'paused' ? 'Продолжить' : 'Пауза'}</button><button type="button" className="finish-button" onClick={finishRecording}>Завершить</button></> : <button type="button" className="record-button" disabled={!consent || busy || recordingState === 'finishing'} onClick={startRecording}>● Начать запись</button>}
              </div>
              {!consent && <small>Сначала подтвердите согласие участников</small>}
            </div>
            <label className="dropzone" onDragOver={(event) => event.preventDefault()} onDrop={(event) => { event.preventDefault(); if (!['recording', 'paused', 'finishing'].includes(recordingState)) chooseFile(event.dataTransfer.files?.[0] ?? null); }}>
              <input type="file" accept="audio/*,video/*,.mkv,.avi" disabled={['recording', 'paused', 'finishing'].includes(recordingState)} onChange={(event) => { chooseFile(event.target.files?.[0] ?? null); event.target.value = ''; }} />
              <span className="upload-symbol">↑</span>
              <b>Загрузить аудио или видео</b>
              <small>Выберите или перетащите файл · до 512 МБ</small>
            </label>
          </div>
          {file && <div className="selected-file"><span>✓ Готово к обработке: <b>{file.name}</b> · {(file.size / 1024 / 1024).toFixed(1)} МБ</span><button type="button" onClick={() => chooseFile(null)}>Убрать</button></div>}
          {file && audioUrl && (file.type.startsWith('video/') || /\.(mp4|mov|mkv|webm|avi)$/i.test(file.name)
            ? <video className="recording-preview" src={audioUrl} controls />
            : <audio className="recording-preview" src={audioUrl} controls />)}
          <div className="submit-row"><button className="primary-button" disabled={!file || !consent || busy || recordingState === 'recording' || recordingState === 'paused' || recordingState === 'finishing'}>{busy ? <><span className="spinner" />Обрабатываем запись…</> : 'Получить протокол'}<span aria-hidden="true">→</span></button><small>{file ? 'Проверьте результат перед скачиванием' : 'После записи или загрузки файла продолжайте здесь'}</small></div>
          {error && <p className="error-box" role="alert">{error}</p>}
        </form>
      </section>

      {result && <section className="results" aria-live="polite">
        <div className="results-heading"><div><p className="eyebrow">ГОТОВЫЙ РЕЗУЛЬТАТ · {result.date} · {result.language}</p><h2>{result.title}</h2><p className="muted">Длительность {Math.floor(result.duration / 60)} мин {Math.round(result.duration % 60)} сек · анализ {result.analysis_mode}</p></div><span className="step">2</span></div>
        {result.analysis_note && <p className="warning-box">{result.analysis_note}</p>}

        <article className="card result-card"><div className="section-title"><span className="section-icon">✦</span><div><h2>Итог встречи</h2><p>При необходимости поправьте текст прямо здесь</p></div></div><textarea className="summary-editor" value={summary} onChange={(event) => setSummary(event.target.value)} placeholder="Добавьте краткий итог встречи" rows="4" />
          {(result.summary_items ?? []).length > 0 && <div className="summary-grid">{result.summary_items.map((item, index) => <div className="summary-item" key={index}><b>{item.topic}</b><span>{item.indicator}</span><small>{item.problem}</small></div>)}</div>}
        </article>

        {speakerIds.length > 0 && <article className="card result-card"><div className="section-title"><span className="section-icon">◉</span><div><h2>Участники</h2><p>Сопоставьте голос и имя — диаризация сама личность не определяет</p></div></div><div className="speaker-list">{speakerIds.map((speaker) => <label className="speaker-row" key={speaker}><span>{speaker}</span><input value={speakerNames[speaker] ?? ''} onChange={(event) => setSpeakerNames((current) => ({ ...current, [speaker]: event.target.value }))} placeholder="Имя участника" /></label>)}</div></article>}

        <article className="card result-card"><div className="section-title"><span className="section-icon">✓</span><div><h2>Поручения <span className="count">{actions.length}</span></h2><p>Проверьте формулировки, сроки и ответственных перед экспортом</p></div></div>
          {actions.length === 0 && <p className="muted">Поручения не найдены. Можно добавить их вручную.</p>}
          <div className="action-list">{actions.map((action, index) => <div className="action-row" key={`${index}-${action.time ?? ''}`}>
            <div className="action-number">{String(index + 1).padStart(2, '0')}</div>
            <div className="action-fields"><div className="action-header"><span>Поручение {index + 1}</span><button type="button" onClick={() => setActions((current) => current.filter((_, position) => position !== index))}>Удалить</button></div><label>Что сделать<textarea rows="2" value={action.task ?? ''} onChange={(event) => updateAction(index, 'task', event.target.value)} /></label>
              <div className="action-meta"><label>Ответственный<input value={action.assignee ?? ''} onChange={(event) => updateAction(index, 'assignee', event.target.value)} /></label><label>Срок<input value={action.deadline ?? ''} onChange={(event) => updateAction(index, 'deadline', event.target.value)} placeholder="Не указан" /></label><label>Дата напоминания<input type="date" value={action.deadline_iso ?? ''} onChange={(event) => updateAction(index, 'deadline_iso', event.target.value)} /></label><label>Статус<select value={action.status ?? 'В работе'} onChange={(event) => updateAction(index, 'status', event.target.value)}><option>В работе</option><option>Просрочено</option><option>Выполнено</option></select></label></div>
              {(action.source_quote || action.time) && <small className="evidence">{action.time ? `${action.time} · ` : ''}{action.source_quote}</small>}
            </div>
          </div>)}</div>
          <button type="button" className="add-action" onClick={() => setActions((current) => [...current, { task: '', assignee: '', speaker: '', deadline: '', deadline_iso: '', status: 'В работе', source_quote: '' }])}>+ Добавить поручение</button>
          {(result.flags ?? []).length > 0 && <div className="flags"><b>Нужно проверить</b>{result.flags.map((flag, index) => <p key={index}>• {flag}</p>)}</div>}
          <div className="export-row"><button onClick={() => exportDocument('docx')}>Скачать DOCX</button><button onClick={() => exportDocument('pdf')}>Скачать PDF</button><button onClick={exportCsv}>Скачать CSV</button></div>
        </article>

        <details className="card transcript-card"><summary>Полный транскрипт <span>{result.transcript.length} фрагм.</span></summary><div className="transcript-list">{result.transcript.map((line, index) => <p key={`${line.id ?? index}-${line.start}`}><time>{clock(line.start)}</time><b>{speakerNames[line.speaker] || line.speaker}</b><span>{line.text}</span></p>)}</div></details>
      </section>}
      <footer>brieflyAI · проверьте имена, сроки и текст перед экспортом</footer>
    </main>
  );
}

export default App;
