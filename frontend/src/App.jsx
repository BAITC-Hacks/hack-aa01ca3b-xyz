import { useMemo, useState } from 'react';

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
  const speakerIds = useMemo(() => [...new Set((result?.transcript ?? []).map((line) => line.speaker).filter(Boolean))], [result]);

  function chooseFile(nextFile) {
    setFile(nextFile);
    setResult(null);
    setActions([]);
    setError('');
    if (audioUrl) URL.revokeObjectURL(audioUrl);
    setAudioUrl(nextFile ? URL.createObjectURL(nextFile) : '');
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
        body: JSON.stringify({ format, title: result.title, date: result.date, summary: result.summary, actions, transcript, participants, summary_items: result.summary_items ?? [] }),
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
        <a className="brand" href="#top"><span className="brand-icon">B</span><span><b>Briefly AI</b><small>Локальный ИИ-протокол</small></span></a>
        <span className="privacy-pill"><i />Данные обрабатываются локально</span>
      </header>
      <section className="hero" id="top">
        <p className="eyebrow">HACKALEM AI · ИННОВАЦИИ</p>
        <h1>Из записи совещания<br /><em>в понятный протокол</em></h1>
        <p className="hero-copy">Транскрипт на русском и казахском, участники, поручения и сроки — с возможностью проверить и исправить результат.</p>
      </section>

      <section className="upload-card card">
        <div className="section-title"><span className="step">01</span><div><h2>Запись совещания</h2><p>Аудио или видео с согласия участников</p></div></div>
        <form onSubmit={processMeeting}>
          <div className="form-grid">
            <label>Название совещания<input value={title} onChange={(event) => setTitle(event.target.value)} maxLength="160" /></label>
            <label>Дата встречи<input type="date" value={meetingDate} onChange={(event) => setMeetingDate(event.target.value)} required /></label>
            <label>Участников (подсказка)<input type="number" min="0" max="30" value={speakers} onChange={(event) => setSpeakers(event.target.value)} /></label>
          </div>
          <label className="dropzone">
            <input type="file" accept="audio/*,video/*,.mkv,.avi" onChange={(event) => chooseFile(event.target.files?.[0] ?? null)} />
            <span className="upload-symbol">↑</span>
            <b>{file ? file.name : 'Выберите файл или перетащите его сюда'}</b>
            <small>{file ? `${(file.size / 1024 / 1024).toFixed(1)} МБ` : 'WAV, MP3, M4A, OGG, MP4, MOV, MKV · до 512 МБ'}</small>
          </label>
          {file && audioUrl && (file.type.startsWith('video/') || /\.(mp4|mov|mkv|webm|avi)$/i.test(file.name)
            ? <video className="recording-preview" src={audioUrl} controls />
            : <audio className="recording-preview" src={audioUrl} controls />)}
          <label className="consent"><input type="checkbox" checked={consent} onChange={(event) => setConsent(event.target.checked)} /><span>Участники уведомлены о записи и обработке аудио локальным ИИ.</span></label>
          <div className="submit-row"><button className="primary-button" disabled={!file || !consent || busy}>{busy ? <><span className="spinner" />Обрабатываем запись…</> : 'Создать протокол'}<span aria-hidden="true">→</span></button><small>Обработка может занять несколько минут на CPU</small></div>
          {error && <p className="error-box" role="alert">{error}</p>}
        </form>
      </section>

      {result && <section className="results" aria-live="polite">
        <div className="results-heading"><div><p className="eyebrow">РЕЗУЛЬТАТ · {result.date} · {result.language}</p><h2>{result.title}</h2><p className="muted">Длительность {Math.floor(result.duration / 60)} мин {Math.round(result.duration % 60)} сек · анализ {result.analysis_mode}</p></div><span className="step">02</span></div>
        {result.analysis_note && <p className="warning-box">{result.analysis_note}</p>}

        <article className="card result-card"><div className="section-title"><span className="section-icon">✦</span><div><h2>Краткое саммари</h2><p>Основные итоги встречи</p></div></div><p className="summary-text">{result.summary || 'Саммари не сформировано.'}</p>
          {(result.summary_items ?? []).length > 0 && <div className="summary-grid">{result.summary_items.map((item, index) => <div className="summary-item" key={index}><b>{item.topic}</b><span>{item.indicator}</span><small>{item.problem}</small></div>)}</div>}
        </article>

        {speakerIds.length > 0 && <article className="card result-card"><div className="section-title"><span className="section-icon">◉</span><div><h2>Участники</h2><p>Сопоставьте голос и имя — диаризация сама личность не определяет</p></div></div><div className="speaker-list">{speakerIds.map((speaker) => <label className="speaker-row" key={speaker}><span>{speaker}</span><input value={speakerNames[speaker] ?? ''} onChange={(event) => setSpeakerNames((current) => ({ ...current, [speaker]: event.target.value }))} placeholder="Имя участника" /></label>)}</div></article>}

        <article className="card result-card"><div className="section-title"><span className="section-icon">✓</span><div><h2>Поручения <span className="count">{actions.length}</span></h2><p>Проверьте формулировки, сроки и ответственных перед экспортом</p></div></div>
          {actions.length === 0 ? <p className="muted">Поручения не найдены. Проверьте транскрипт и саммари.</p> : <div className="action-list">{actions.map((action, index) => <div className="action-row" key={`${index}-${action.time ?? ''}`}>
            <div className="action-number">{String(index + 1).padStart(2, '0')}</div>
            <div className="action-fields"><label>Поручение<textarea rows="2" value={action.task ?? ''} onChange={(event) => updateAction(index, 'task', event.target.value)} /></label>
              <div className="action-meta"><label>Ответственный<input value={action.assignee ?? ''} onChange={(event) => updateAction(index, 'assignee', event.target.value)} /></label><label>Срок<input value={action.deadline ?? ''} onChange={(event) => updateAction(index, 'deadline', event.target.value)} placeholder="Не указан" /></label><label>Дата напоминания<input type="date" value={action.deadline_iso ?? ''} onChange={(event) => updateAction(index, 'deadline_iso', event.target.value)} /></label><label>Статус<select value={action.status ?? 'В работе'} onChange={(event) => updateAction(index, 'status', event.target.value)}><option>В работе</option><option>Просрочено</option><option>Выполнено</option></select></label></div>
              {(action.source_quote || action.time) && <small className="evidence">{action.time ? `${action.time} · ` : ''}{action.source_quote}</small>}
            </div>
          </div>)}</div>}
          {(result.flags ?? []).length > 0 && <div className="flags"><b>Нужно проверить</b>{result.flags.map((flag, index) => <p key={index}>• {flag}</p>)}</div>}
          <div className="export-row"><button onClick={() => exportDocument('docx')}>Скачать DOCX</button><button onClick={() => exportDocument('pdf')}>Скачать PDF</button><button onClick={exportCsv}>Скачать CSV</button></div>
        </article>

        <details className="card transcript-card"><summary>Полный транскрипт <span>{result.transcript.length} фрагм.</span></summary><div className="transcript-list">{result.transcript.map((line, index) => <p key={`${line.id ?? index}-${line.start}`}><time>{clock(line.start)}</time><b>{speakerNames[line.speaker] || line.speaker}</b><span>{line.text}</span></p>)}</div></details>
      </section>}
      <footer>Briefly AI · аудио не покидает ваш компьютер · проверьте протокол перед использованием</footer>
    </main>
  );
}

export default App;
