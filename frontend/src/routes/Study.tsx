// Asistente del modo estudio (`beacon study`). Pantalla independiente: sin barra lateral ni proyectos.
// Cada paso exige una acción del participante; nada se envía a ningún servidor, solo se descarga un archivo.
import { useCallback, useEffect, useState } from "react";

type Lang = "es" | "en";
type Correct = "si" | "parcial" | "no" | "no_se";

interface StudySource { archivo: string; lineas: string }
interface StudyQuestion {
  n: number; pregunta: string; respuesta: string; segundos: number; fuentes: StudySource[];
  valoracion: { correcta: Correct } | null;
}
interface StudyState {
  started: boolean;
  consentimientos?: { participacion: boolean; hardware: boolean; envio: boolean };
  hardware?: Record<string, unknown> | null;
  repo?: { url: string; archivos_indexados: number } | null;
  repo_status?: { estado: string; detalle: string | null };
  preguntas?: StudyQuestion[];
  puede_terminar?: boolean;
  experiencia?: { anios?: number } | null;
}

const T = {
  es: {
    title: "Estudio de Beacon", lang: "Idioma",
    s1: "1. Participación", s2: "2. Tu equipo", s3: "3. Repositorio", s4: "4. Preguntas", s5: "5. Cierre",
    intro: "Beacon responde preguntas sobre un repositorio usando un modelo que corre en tu equipo. Este estudio mide qué tan bien funciona en equipos reales. Tu código nunca sale de tu equipo: el archivo final no incluye código ni rutas, y tú lo revisas antes de enviarlo.",
    consent_part: "Acepto participar en el estudio y que se guarden en este equipo mis preguntas, las respuestas, los tiempos y mis valoraciones.",
    start: "Comenzar",
    hw_text: "Para interpretar los tiempos necesitamos saber qué equipo usas. Esto es lo que se guardaría (no incluye tu nombre ni rutas):",
    consent_hw: "Autorizo guardar estos datos del equipo.", skip: "Prefiero no compartirlos", next: "Continuar",
    repo_text: "Elige un repositorio público (GitHub, GitLab, Bitbucket o Codeberg) que conozcas, de hasta 3000 archivos de código.",
    repo_ph: "https://github.com/usuario/repositorio", prepare: "Preparar repositorio",
    preparing: "Preparando el repositorio...", ready: "Listo: {n} archivos indexados.",
    q_text: "Haz al menos {min} preguntas reales sobre el repositorio y valora cada respuesta.",
    q_ph: "Pregunta sobre el código...", ask: "Preguntar", asking: "Consultando...",
    secs: "{s} s", sources: "Fuentes", correct: "¿La respuesta es correcta?",
    c_si: "Sí", c_parcial: "En parte", c_no: "No", c_no_se: "No lo sé", rated: "Valorada",
    finish: "Terminar", count: "{n} de {min} preguntas mínimas",
    exp_title: "Sobre ti", years: "Años de experiencia programando", useful: "¿Qué tan útil te pareció Beacon? (1-5)",
    consent_send: "Autorizo generar el archivo de resultados para enviarlo.",
    identity: "Opcional: tu perfil de GitHub o una presentación breve (se descarga en un archivo aparte).",
    github: "Perfil de GitHub", intro_me: "Presentación",
    generate: "Generar archivos", dl_results: "Descargar resultados", copy: "Copiar resultados", copied: "Copiado", dl_identity: "Descargar identidad (opcional)",
    review: "Revisa el contenido antes de enviarlo:", saved: "Se guardaron en la carpeta de Beacon: {f}.", sent_note: "Envía el archivo de resultados por el canal indicado en la invitación.",
    discard: "Descartar todo y salir", error: "Error",
  },
  en: {
    title: "Beacon study", lang: "Language",
    s1: "1. Participation", s2: "2. Your machine", s3: "3. Repository", s4: "4. Questions", s5: "5. Wrap-up",
    intro: "Beacon answers questions about a repository using a model that runs on your machine. This study measures how well it works on real hardware. Your code never leaves your machine: the final file contains no code or paths, and you review it before sending it.",
    consent_part: "I agree to take part in the study and to have my questions, the answers, timings and my ratings stored on this machine.",
    start: "Start",
    hw_text: "To interpret timings we need to know what machine you use. This is what would be stored (no name or paths):",
    consent_hw: "I allow storing this hardware data.", skip: "I prefer not to share it", next: "Continue",
    repo_text: "Pick a public repository (GitHub, GitLab, Bitbucket or Codeberg) you know, with up to 3000 code files.",
    repo_ph: "https://github.com/user/repository", prepare: "Prepare repository",
    preparing: "Preparing the repository...", ready: "Ready: {n} files indexed.",
    q_text: "Ask at least {min} real questions about the repository and rate each answer.",
    q_ph: "Question about the code...", ask: "Ask", asking: "Querying...",
    secs: "{s} s", sources: "Sources", correct: "Is the answer correct?",
    c_si: "Yes", c_parcial: "Partly", c_no: "No", c_no_se: "Not sure", rated: "Rated",
    finish: "Finish", count: "{n} of {min} minimum questions",
    exp_title: "About you", years: "Years of programming experience", useful: "How useful was Beacon? (1-5)",
    consent_send: "I allow generating the results file so I can send it.",
    identity: "Optional: your GitHub profile or a short introduction (downloaded as a separate file).",
    github: "GitHub profile", intro_me: "Introduction",
    generate: "Generate files", dl_results: "Download results", copy: "Copy results", copied: "Copied", dl_identity: "Download identity (optional)",
    review: "Review the content before sending it:", saved: "Saved in the Beacon folder: {f}.", sent_note: "Send the results file through the channel given in the invitation.",
    discard: "Discard everything and quit", error: "Error",
  },
} as const;

const MIN_QUESTIONS = 3;
const btn = "rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-on disabled:opacity-50";
const btnGhost = "rounded-lg border border-line px-4 py-2 text-sm text-ink-2 hover:bg-surface-2";
const input = "w-full rounded-lg border border-line bg-surface px-3 py-2 text-sm text-ink";

async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
  const res = await fetch(`/study${path}`, {
    method,
    headers: { "Content-Type": "application/json" },
    ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail ?? res.statusText);
  return data as T;
}

function download(name: string, data: unknown) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(data, null, 2)], { type: "application/json" }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  a.click();
  URL.revokeObjectURL(url);
}

export default function Study() {
  const [lang, setLang] = useState<Lang>(navigator.language.startsWith("es") ? "es" : "en");
  const t = (k: keyof (typeof T)["es"], v: Record<string, string | number> = {}) =>
    Object.entries(v).reduce((s: string, [key, val]) => s.replace(`{${key}}`, String(val)), T[lang][k] as string);
  const [state, setState] = useState<StudyState | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      setState(await call<StudyState>("GET", "/state"));
    } catch (e) {
      setError((e as Error).message);
    }
  }, []);

  useEffect(() => { void refresh(); }, [refresh]);
  const preparing = state?.repo_status?.estado === "clonando" || state?.repo_status?.estado === "indexando";
  useEffect(() => {
    if (!preparing) return;
    const id = setInterval(() => void refresh(), 3000);
    return () => clearInterval(id);
  }, [preparing, refresh]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try { await fn(); await refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  if (!state) return <p className="p-8 text-muted">{error || "..."}</p>;

  const consent = state.consentimientos;
  const stage = !state.started || !consent?.participacion ? 1 : 2;

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-6 py-8">
      <header className="flex items-center justify-between">
        <h1 className="font-display text-2xl font-bold text-accent-ink">{t("title")}</h1>
        <select aria-label={t("lang")} value={lang} onChange={(e) => setLang(e.target.value as Lang)} className={input + " !w-auto"}>
          <option value="es">Español</option>
          <option value="en">English</option>
        </select>
      </header>
      {error && <p role="alert" className="rounded-lg border border-danger-line bg-danger-bg p-3 text-sm text-danger-ink">{t("error")}: {error}</p>}
      <StudyBody {...{ state, t, lang, busy, act, stage }} />
    </div>
  );
}

interface BodyProps {
  state: StudyState; t: (k: keyof (typeof T)["es"], v?: Record<string, string | number>) => string; lang: Lang;
  busy: boolean; act: (fn: () => Promise<unknown>) => Promise<void>; stage: number;
}

function StudyBody({ state, t, lang, busy, act, stage }: BodyProps) {
  const [agree, setAgree] = useState(false);
  const [hw, setHw] = useState<Record<string, unknown> | null>(null);
  const [hwOk, setHwOk] = useState(false);
  const [hwDone, setHwDone] = useState(false);
  const [url, setUrl] = useState("");
  const [question, setQuestion] = useState("");
  const [finishing, setFinishing] = useState(false);
  const [years, setYears] = useState("");
  const [useful, setUseful] = useState("");
  const [sendOk, setSendOk] = useState(false);
  const [copied, setCopied] = useState(false);
  const [github, setGithub] = useState("");
  const [intro, setIntro] = useState("");
  const [files, setFiles] = useState<{ resultado: unknown; identidad: unknown; archivos?: string[] } | null>(null);

  useEffect(() => {
    if (stage === 2 && !hw && !hwDone) void call<Record<string, unknown>>("GET", "/hardware-preview").then(setHw).catch(() => undefined);
  }, [stage, hw]);

  const questions = state.preguntas ?? [];
  const section = "flex flex-col gap-4 rounded-xl border border-line bg-surface p-5";

  if (stage === 1) {
    return (
      <section className={section}>
        <h2 className="font-semibold">{t("s1")}</h2>
        <p className="text-sm text-ink-2">{t("intro")}</p>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-1" />
          {t("consent_part")}
        </label>
        <button className={btn + " self-start"} disabled={!agree || busy} onClick={() => act(async () => {
          if (!state.started) await call("POST", "/start", { perfil: "abierto", idioma: lang });
          await call("POST", "/consent", { tipo: "participacion", aceptado: true });
        })}>{t("start")}</button>
      </section>
    );
  }

  if (stage === 2 && !hwDone && !state.repo && !state.hardware) {
    return (
      <section className={section}>
        <h2 className="font-semibold">{t("s2")}</h2>
        <p className="text-sm text-ink-2">{t("hw_text")}</p>
        <pre className="overflow-x-auto rounded-lg bg-code-bg p-3 font-mono text-xs text-code-ink">{JSON.stringify(hw, null, 2)}</pre>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={hwOk} onChange={(e) => setHwOk(e.target.checked)} className="mt-1" />
          {t("consent_hw")}
        </label>
        <div className="flex gap-3">
          <button className={btn} disabled={!hwOk || busy} onClick={() => act(async () => {
            await call("POST", "/consent", { tipo: "hardware", aceptado: true });
            await call("POST", "/hardware");
            setHwDone(true);
          })}>{t("next")}</button>
          <button className={btnGhost} disabled={busy} onClick={() => setHwDone(true)}>{t("skip")}</button>
        </div>
      </section>
    );
  }

  if (!state.repo) {
    const status = state.repo_status;
    return (
      <section className={section}>
        <h2 className="font-semibold">{t("s3")}</h2>
        <p className="text-sm text-ink-2">{t("repo_text")}</p>
        <input aria-label="URL" className={input} placeholder={t("repo_ph")} value={url} onChange={(e) => setUrl(e.target.value)} />
        <button className={btn + " self-start"} disabled={!url.trim() || busy || status?.estado === "clonando" || status?.estado === "indexando"}
          onClick={() => act(() => call("POST", "/repo", { url: url.trim() }))}>{t("prepare")}</button>
        {(status?.estado === "clonando" || status?.estado === "indexando") && (
          <p role="status" className="text-sm text-muted">{t("preparing")} {status.detalle}</p>
        )}
        {status?.estado === "error" && <p role="alert" className="text-sm text-danger-ink">{status.detalle}</p>}
      </section>
    );
  }

  if (finishing) {
    return (
      <section className={section}>
        <h2 className="font-semibold">{t("s5")}</h2>
        <h3 className="text-sm font-semibold">{t("exp_title")}</h3>
        <label className="text-sm">{t("years")}
          <input type="number" min={0} className={input} value={years} onChange={(e) => setYears(e.target.value)} />
        </label>
        <label className="text-sm">{t("useful")}
          <input type="number" min={1} max={5} className={input} value={useful} onChange={(e) => setUseful(e.target.value)} />
        </label>
        <p className="text-sm text-ink-2">{t("identity")}</p>
        <input aria-label={t("github")} placeholder={t("github")} className={input} value={github} onChange={(e) => setGithub(e.target.value)} />
        <textarea aria-label={t("intro_me")} placeholder={t("intro_me")} className={input} rows={3} value={intro} onChange={(e) => setIntro(e.target.value)} />
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={sendOk} onChange={(e) => setSendOk(e.target.checked)} className="mt-1" />
          {t("consent_send")}
        </label>
        <button className={btn + " self-start"} disabled={!sendOk || busy} onClick={() => act(async () => {
          await call("POST", "/experience", { datos: { anios: Number(years) || null, likert: { util: Number(useful) || null } } });
          await call("POST", "/identity", { github, presentacion: intro });
          await call("POST", "/consent", { tipo: "envio", aceptado: true });
          setFiles(await call("GET", "/result"));
        })}>{t("generate")}</button>
        {files && (
          <div className="flex flex-col gap-3">
            {files.archivos && <p className="text-sm">{t("saved", { f: files.archivos.join(", ") })}</p>}
            <p className="text-sm">{t("review")}</p>
            <pre className="max-h-64 overflow-auto rounded-lg bg-code-bg p-3 font-mono text-xs text-code-ink">{JSON.stringify(files.resultado, null, 2)}</pre>
            <div className="flex flex-wrap gap-3">
              <button className={btn} onClick={() => download("beacon-estudio-resultados.json", files.resultado)}>{t("dl_results")}</button>
              <button className={btnGhost} onClick={() => {
                void navigator.clipboard?.writeText(JSON.stringify(files.resultado)).then(() => setCopied(true));
              }}>{copied ? t("copied") : t("copy")}</button>
              {files.identidad !== null && (
                <button className={btnGhost} onClick={() => download("beacon-estudio-identidad.json", files.identidad)}>{t("dl_identity")}</button>
              )}
            </div>
            <p className="text-sm text-muted">{t("sent_note")}</p>
          </div>
        )}
        <button className={btnGhost + " self-start"} disabled={busy} onClick={() => act(() => call("POST", "/discard"))}>{t("discard")}</button>
      </section>
    );
  }

  return (
    <section className={section}>
      <h2 className="font-semibold">{t("s4")}</h2>
      <p className="text-sm text-ink-2">{t("ready", { n: state.repo.archivos_indexados })} {t("q_text", { min: MIN_QUESTIONS })}</p>
      <form className="flex gap-2" onSubmit={(e) => {
        e.preventDefault();
        void act(async () => { await call("POST", "/ask", { pregunta: question, idioma: lang }); setQuestion(""); });
      }}>
        <input aria-label={t("q_ph")} className={input} placeholder={t("q_ph")} value={question} onChange={(e) => setQuestion(e.target.value)} disabled={busy} />
        <button className={btn} disabled={busy || question.trim().length < 3}>{busy ? t("asking") : t("ask")}</button>
      </form>
      <ol className="flex flex-col gap-4">
        {[...questions].reverse().map((q) => (
          <li key={q.n} className="flex flex-col gap-2 rounded-lg border border-line p-3">
            <p className="text-sm font-semibold">{q.pregunta}</p>
            <p className="whitespace-pre-wrap text-sm text-ink-2">{q.respuesta}</p>
            <p className="text-xs text-muted">{t("secs", { s: q.segundos })} · {t("sources")}: {q.fuentes.length}</p>
            <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t("correct")}>
              <span className="text-xs text-muted">{t("correct")}</span>
              {(["si", "parcial", "no", "no_se"] as Correct[]).map((c) => (
                <button key={c} disabled={busy}
                  className={`rounded-md border px-2 py-1 text-xs ${q.valoracion?.correcta === c ? "border-accent bg-accent-tint font-semibold text-accent-ink" : "border-line text-ink-2"}`}
                  onClick={() => act(() => call("POST", "/rate", { n: q.n, correcta: c }))}>{t(`c_${c}` as "c_si")}</button>
              ))}
            </div>
          </li>
        ))}
      </ol>
      <div className="flex items-center gap-3">
        <button className={btn} disabled={!state.puede_terminar || busy} onClick={() => setFinishing(true)}>{t("finish")}</button>
        <span className="text-xs text-muted">{t("count", { n: questions.length, min: MIN_QUESTIONS })}</span>
      </div>
    </section>
  );
}
