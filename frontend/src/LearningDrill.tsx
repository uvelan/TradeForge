import { useEffect, useState } from 'react';
import { api } from './api';
import { Card, Notice, SectionTitle } from './components';

const path = '/learning/lessons/smt-swing-timing-v1';
type Candle = {open_utc: string; eurusd_high: string; gbpusd_high: string};
type Attempt = {choice: string; explanation: string; journal_id: string; correct: boolean;
  reveal: {explanation: string; next_candle: Candle}};
type Drill = {
  lesson: {title: string; objective: string; prompt: string; context: string; candles: Candle[];
    choices: {id: string; label: string}[]};
  attempt: Attempt | null;
};

export default function LearningDrill({saved, openJournal}: {
  saved: () => void; openJournal: (id: string) => Promise<void>;
}) {
  const [drill, setDrill] = useState<Drill>();
  const [choice, setChoice] = useState('');
  const [explanation, setExplanation] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    api.get<Drill>(path).then(d => { if (active) setDrill(d); })
      .catch(e => { if (active) setError(e.message); });
    return () => { active = false; };
  }, []);
  const lesson = drill?.lesson;
  const attempt = drill?.attempt;
  async function submit(event: React.FormEvent) {
    event.preventDefault();
    if (!lesson || busy || attempt) return;
    setBusy(true);
    setError('');
    try {
      const next = await api.post<Attempt>(path + '/hypothesis', {choice, explanation});
      setDrill({lesson, attempt: next});
      saved();
    } catch (e) { setError((e as Error).message); }
    finally { setBusy(false); }
  }
  return <Card>
    <SectionTitle eyebrow="LEARN · SYNTHETIC ONLY" title={lesson?.title ?? 'Blind SMT drill'}/>
    {error && <Notice tone="red">{error}</Notice>}
    {!lesson && !error && <p role="status">Loading exercise…</p>}
    {lesson && <>
      <p>{lesson.objective}</p><p>{lesson.context}</p>
      <table>
        <caption>M5 candle highs (synthetic)</caption>
        <thead><tr><th scope="col">Open UTC</th><th scope="col">EURUSD</th><th scope="col">GBPUSD</th></tr></thead>
        <tbody>{[...lesson.candles, ...(attempt ? [attempt.reveal.next_candle] : [])].map(candle =>
          <tr key={candle.open_utc}><th scope="row">{candle.open_utc}</th>
            <td>{candle.eurusd_high}</td><td>{candle.gbpusd_high}</td></tr>)}</tbody>
      </table>
      {attempt ? <>
        <p role="status"><strong>{attempt.correct ? 'Correct' : 'Needs review'}</strong> · {attempt.reveal.explanation}</p>
        <p>Your recorded hypothesis: {attempt.explanation}</p>
        <button type="button" className="btn btn-secondary" onClick={() =>
          void openJournal(attempt.journal_id).catch(e => setError(e.message))}>
          Open journal entry {attempt.journal_id}
        </button>
      </> : <form onSubmit={submit}>
        <fieldset><legend>{lesson.prompt}</legend>{lesson.choices.map(option =>
          <label className="field" key={option.id}><span>
            <input type="radio" name="learning-hypothesis" value={option.id}
              checked={choice === option.id} onChange={() => setChoice(option.id)} required/> {option.label}
          </span></label>)}</fieldset>
        <label className="field">Your reasoning (10–1,000 characters)
          <textarea value={explanation} onChange={e => setExplanation(e.target.value)}
            rows={3} minLength={10} maxLength={1000} required/>
        </label>
        <button className="btn btn-primary" disabled={busy || !choice || explanation.trim().length < 10}>
          Submit hypothesis and reveal
        </button>
      </form>}
    </>}
  </Card>;
}
