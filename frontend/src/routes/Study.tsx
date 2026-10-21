// Asistente del modo estudio (`beacon study`). Pantalla independiente: sin barra lateral ni proyectos.
// Cada paso exige una acción del participante; nada se envía a ningún servidor, solo se generan archivos locales.
import { useCallback, useEffect, useState } from "react";

type Lang = "es" | "en";
type Correct = "si" | "parcial" | "no" | "no_se";

interface StudySource { archivo: string; lineas: string }
interface Rating { correcta: Correct; fuentes_utiles: number | null }
interface StudyQuestion {
  n: number; pregunta: string; respuesta: string; segundos: number; fuentes: StudySource[];
  valoracion: Rating | null;
}
interface RepoStatus {
  estado: string; detalle: string | null;
  progreso?: { actual: number; total: number; archivo: string; segundos: number };
}
interface StudyState {
  started: boolean;
  consentimientos?: { participacion: boolean; hardware: boolean; envio: boolean };
  hardware?: Record<string, unknown> | null;
  repo?: { url: string; archivos_indexados: number; fragmentos?: number } | null;
  repo_status?: RepoStatus;
  preguntas?: StudyQuestion[];
  puede_terminar?: boolean;
  experiencia?: { anios?: number; likert?: { util?: number } } | null;
}

const T = {
  es: {
    title: "Estudio de Beacon", lang: "Idioma",
    s1: "Participación", s2: "Tu equipo", s3: "Repositorio", s4: "Preguntas", s5: "Cierre",
    step_of: "Paso {n} de {total}", back: "Atrás", next: "Siguiente",
    intro: "Beacon responde preguntas sobre un repositorio usando un modelo que corre en tu equipo. Este estudio mide qué tan bien funciona en equipos reales.",
    intro_files: "Este programa no envía nada a ninguna parte. Solo generará dos archivos en la carpeta de Beacon: uno con tus resultados (preguntas, respuestas, tiempos y valoraciones) y otro, opcional, con tu identidad. Podrás abrirlos y auditarlos antes de decidir si los subes. No incluyen tu código ni rutas de tu equipo, y subirlos es voluntario.",
    consent_part: "Acepto participar en el estudio y que se guarden en este equipo mis preguntas, las respuestas, los tiempos y mis valoraciones.",
    start: "Comenzar",
    hw_text: "Para interpretar los tiempos necesitamos saber qué equipo usas. Estos son los datos que Beacon detectó en TU equipo en este momento, y es exactamente lo que se guardaría si lo autorizas (no incluye tu nombre ni rutas):",
    consent_hw: "Autorizo guardar estos datos del equipo (si no lo marcas, se omiten).",
    repo_text: "Elige un repositorio público (GitHub, GitLab, Bitbucket o Codeberg) que conozcas, de hasta 3000 archivos de código.",
    repo_ph: "https://github.com/usuario/repositorio", prepare: "Preparar repositorio", examples: "O elige un repositorio de ejemplo...",
    cloning: "Clonando el repositorio...", indexing: "Indexando: {a} de {t} archivos", elapsed: "{s} s",
    repo_ready: "Repositorio listo: {n} archivos indexados.", change_repo: "Elegir otro repositorio",
    repo_locked: "Para cambiar de repositorio hay que descartar la sesión (ya hiciste preguntas).",
    q_text: "Haz al menos {min} preguntas reales sobre el repositorio y valora cada respuesta.",
    q_hint: "Evita preguntas demasiado generales como «¿qué hace este programa?»: funcionan mejor las concretas, por ejemplo «¿dónde se carga la configuración?» o «¿qué función formatea la salida?».",
    q_ph: "Pregunta sobre el código...", ask: "Preguntar", asking: "Consultando...",
    thinking: "Buscando en el código y generando la respuesta... {s} s",
    secs: "{s} s", sources: "Fuentes", correct: "¿La respuesta es correcta?",
    useful: "¿Qué tan útiles fueron las fuentes? (1 = nada, 5 = mucho)",
    c_si: "Sí", c_parcial: "En parte", c_no: "No", c_no_se: "No lo sé",
    finish: "Terminar", count: "{n} de {min} preguntas mínimas",
    exp_title: "Sobre ti", years: "Años de experiencia programando", usefulness: "¿Qué tan útil te pareció Beacon? (1-5)",
    consent_send: "Autorizo generar el archivo de resultados para enviarlo.",
    identity: "Opcional: tu perfil de GitHub o una presentación breve (se guarda en un archivo aparte).",
    github: "Perfil de GitHub", intro_me: "Presentación",
    generate: "Generar archivos", dl_results: "Descargar resultados", copy: "Copiar resultados", copied: "Copiado",
    dl_identity: "Descargar identidad (opcional)", saved: "Se guardaron en la carpeta de Beacon: {f}.",
    review: "Revisa el contenido antes de enviarlo:", sent_note: "Envía el archivo de resultados por el canal indicado en la invitación.",
    discard: "Descartar todo y salir", error: "Error",
  },
  en: {
    title: "Beacon study", lang: "Language",
    s1: "Participation", s2: "Your machine", s3: "Repository", s4: "Questions", s5: "Wrap-up",
    step_of: "Step {n} of {total}", back: "Back", next: "Next",
    intro: "Beacon answers questions about a repository using a model that runs on your machine. This study measures how well it works on real hardware.",
    intro_files: "This program sends nothing anywhere. It will only generate two files in the Beacon folder: one with your results (questions, answers, timings and ratings) and an optional one with your identity. You can open and audit them before deciding whether to upload them. They contain no code or paths from your machine, and uploading them is entirely up to you.",
    consent_part: "I agree to take part in the study and to have my questions, the answers, timings and my ratings stored on this machine.",
    start: "Start",
    hw_text: "To interpret timings we need to know what machine you use. This is the data Beacon just detected on YOUR machine, and exactly what would be stored if you allow it (no name or paths):",
    consent_hw: "I allow storing this hardware data (if unchecked, it is skipped).",
    repo_text: "Pick a public repository (GitHub, GitLab, Bitbucket or Codeberg) you know, with up to 3000 code files.",
    repo_ph: "https://github.com/user/repository", prepare: "Prepare repository", examples: "Or pick an example repository...",
    cloning: "Cloning the repository...", indexing: "Indexing: {a} of {t} files", elapsed: "{s} s",
    repo_ready: "Repository ready: {n} files indexed.", change_repo: "Pick another repository",
    repo_locked: "To change the repository you must discard the session (you already asked questions).",
    q_text: "Ask at least {min} real questions about the repository and rate each answer.",
    q_hint: "Avoid overly general questions such as \"what does this program do?\": specific ones work better, for example \"where is the configuration loaded?\" or \"which function formats the output?\".",
    q_ph: "Question about the code...", ask: "Ask", asking: "Querying...",
    thinking: "Searching the code and generating the answer... {s} s",
    secs: "{s} s", sources: "Sources", correct: "Is the answer correct?",
    useful: "How useful were the sources? (1 = not at all, 5 = very)",
    c_si: "Yes", c_parcial: "Partly", c_no: "No", c_no_se: "Not sure",
    finish: "Finish", count: "{n} of {min} minimum questions",
    exp_title: "About you", years: "Years of programming experience", usefulness: "How useful was Beacon? (1-5)",
    consent_send: "I allow generating the results file so I can send it.",
    identity: "Optional: your GitHub profile or a short introduction (saved in a separate file).",
    github: "GitHub profile", intro_me: "Introduction",
    generate: "Generate files", dl_results: "Download results", copy: "Copy results", copied: "Copied",
    dl_identity: "Download identity (optional)", saved: "Saved in the Beacon folder: {f}.",
    review: "Review the content before sending it:", sent_note: "Send the results file through the channel given in the invitation.",
    discard: "Discard everything and quit", error: "Error",
  },
} as const;

type Key = keyof (typeof T)["es"];
type Tr = (k: Key, v?: Record<string, string | number>) => string;

const MIN_QUESTIONS = 3;
// Repositorios públicos de tamaño moderado, para quien no tiene uno en mente.
const EXAMPLE_REPOS = [
  { name: "charmbracelet/glow", lang: "Go", url: "https://github.com/charmbracelet/glow" },
  { name: "psf/requests", lang: "Python", url: "https://github.com/psf/requests" },
  { name: "pallets/flask", lang: "Python", url: "https://github.com/pallets/flask" },
  { name: "expressjs/express", lang: "JavaScript", url: "https://github.com/expressjs/express" },
  { name: "spring-projects/spring-petclinic", lang: "Java", url: "https://github.com/spring-projects/spring-petclinic" },
  { name: "GoogleCloudPlatform/microservices-demo", lang: "Go, Python, Java, C#, JS", url: "https://github.com/GoogleCloudPlatform/microservices-demo" },
];
const STEPS: Key[] = ["s1", "s2", "s3", "s4", "s5"];
const btn = "rounded-lg bg-accent px-4 py-2 text-sm font-semibold text-accent-on disabled:opacity-50";
const btnGhost = "rounded-lg border border-line px-4 py-2 text-sm text-ink-2 hover:bg-surface-2 disabled:opacity-50";
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

function ProgressBar({ value, label, indeterminate = false }: { value: number; label: string; indeterminate?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <div role="progressbar" aria-label={label} aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(value)}
        className="h-2 overflow-hidden rounded-full bg-surface-2">
        <div className={`h-full rounded-full bg-accent transition-all duration-300 ${indeterminate ? "w-1/3 animate-pulse" : ""}`}
          style={indeterminate ? undefined : { width: `${Math.max(2, value)}%` }} />
      </div>
      <p className="text-xs text-muted">{label}</p>
    </div>
  );
}

export default function Study() {
  const [lang, setLang] = useState<Lang>(navigator.language.startsWith("es") ? "es" : "en");
  const t: Tr = (k, v = {}) =>
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
    const id = setInterval(() => void refresh(), 1000);
    return () => clearInterval(id);
  }, [preparing, refresh]);

  const act = async (fn: () => Promise<unknown>) => {
    setBusy(true);
    setError("");
    try { await fn(); await refresh(); } catch (e) { setError((e as Error).message); } finally { setBusy(false); }
  };

  if (!state) return <p className="p-8 text-muted">{error || "..."}</p>;

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
      <StudyBody {...{ state, t, lang, busy, act }} />
    </div>
  );
}

interface BodyProps {
  state: StudyState; t: Tr; lang: Lang; busy: boolean;
  act: (fn: () => Promise<unknown>) => Promise<void>;
}

function initialStep(state: StudyState): number {
  if (!state.started || !state.consentimientos?.participacion) return 1;
  if (state.repo) return 4;
  return state.hardware ? 3 : 2;
}

function StudyBody({ state, t, lang, busy, act }: BodyProps) {
  const [step, setStep] = useState(() => initialStep(state));
  const consent = state.consentimientos;
  const questions = state.preguntas ?? [];
  const section = "flex flex-col gap-4 rounded-xl border border-line bg-surface p-5";

  // Paso 1
  const [agree, setAgree] = useState(!!consent?.participacion);
  // Paso 2
  const [hw, setHw] = useState<Record<string, unknown> | null>(null);
  const [hwOk, setHwOk] = useState(!!consent?.hardware);
  // Paso 3
  const [url, setUrl] = useState("");
  const [changing, setChanging] = useState(false);
  // Paso 4
  const [question, setQuestion] = useState("");
  const [pending, setPending] = useState<{ q: string; since: number } | null>(null);
  const [now, setNow] = useState(Date.now());
  // Paso 5
  const [years, setYears] = useState(state.experiencia?.anios != null ? String(state.experiencia.anios) : "");
  const [usefulness, setUsefulness] = useState(state.experiencia?.likert?.util != null ? String(state.experiencia.likert.util) : "");
  const [sendOk, setSendOk] = useState(false);
  const [github, setGithub] = useState("");
  const [intro, setIntro] = useState("");
  const [copied, setCopied] = useState(false);
  const [files, setFiles] = useState<{ resultado: unknown; identidad: unknown; archivos?: string[] } | null>(null);

  useEffect(() => {
    if (step === 2 && !hw) void call<Record<string, unknown>>("GET", "/hardware-preview").then(setHw).catch(() => undefined);
  }, [step, hw]);

  useEffect(() => {
    if (!pending) return;
    const id = setInterval(() => setNow(Date.now()), 500);
    return () => clearInterval(id);
  }, [pending]);

  const status = state.repo_status;
  const preparing = status?.estado === "clonando" || status?.estado === "indexando";
  useEffect(() => {
    if (changing && status?.estado === "listo") setChanging(false);
  }, [changing, status?.estado]);

  // Cada paso define su acción principal (la que lo completa y avanza).
  let next: { label: string; disabled: boolean; run: () => Promise<void> | void } | null = null;
  let content: React.ReactNode = null;

  if (step === 1) {
    next = {
      label: t("start"), disabled: !agree || busy,
      run: () => act(async () => {
        if (!state.started) await call("POST", "/start", { perfil: "abierto", idioma: lang });
        if (!consent?.participacion) await call("POST", "/consent", { tipo: "participacion", aceptado: true });
        setStep(2);
      }),
    };
    content = (
      <>
        <p className="text-sm text-ink-2">{t("intro")}</p>
        <p className="rounded-lg bg-accent-tint p-3 text-sm text-accent-ink">{t("intro_files")}</p>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} className="mt-1" />
          {t("consent_part")}
        </label>
      </>
    );
  } else if (step === 2) {
    next = {
      label: t("next"), disabled: busy,
      run: () => act(async () => {
        if (hwOk) {
          await call("POST", "/consent", { tipo: "hardware", aceptado: true });
          await call("POST", "/hardware");
        } else if (consent?.hardware) {
          await call("POST", "/consent", { tipo: "hardware", aceptado: false });  // retirarlo borra lo guardado
        }
        setStep(3);
      }),
    };
    content = (
      <>
        <p className="text-sm text-ink-2">{t("hw_text")}</p>
        <pre className="overflow-x-auto rounded-lg bg-code-bg p-3 font-mono text-xs text-code-ink">{JSON.stringify(hw, null, 2)}</pre>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={hwOk} onChange={(e) => setHwOk(e.target.checked)} className="mt-1" />
          {t("consent_hw")}
        </label>
      </>
    );
  } else if (step === 3) {
    const showForm = !state.repo || changing;
    next = { label: t("next"), disabled: busy || !state.repo || changing || preparing, run: () => setStep(4) };
    const progress = status?.progreso;
    content = (
      <>
        {showForm ? (
          <>
            <p className="text-sm text-ink-2">{t("repo_text")}</p>
            <input aria-label="URL" className={input} placeholder={t("repo_ph")} value={url} onChange={(e) => setUrl(e.target.value)} disabled={preparing} />
            <select aria-label={t("examples")} className={input} value="" disabled={preparing} onChange={(e) => setUrl(e.target.value)}>
              <option value="">{t("examples")}</option>
              {EXAMPLE_REPOS.map((r) => <option key={r.url} value={r.url}>{r.name} ({r.lang})</option>)}
            </select>
            <button className={btn + " self-start"} disabled={!url.trim() || busy || preparing}
              onClick={() => act(() => call("POST", "/repo", { url: url.trim() }))}>{t("prepare")}</button>
          </>
        ) : (
          <>
            <p className="text-sm text-ink-2">{t("repo_ready", { n: state.repo?.archivos_indexados ?? 0 })} <span className="font-mono text-xs">{state.repo?.url}</span></p>
            {questions.length === 0
              ? <button className={btnGhost + " self-start"} onClick={() => setChanging(true)}>{t("change_repo")}</button>
              : <p className="text-xs text-muted">{t("repo_locked")}</p>}
          </>
        )}
        {status?.estado === "clonando" && <ProgressBar value={0} indeterminate label={t("cloning")} />}
        {status?.estado === "indexando" && progress && (
          <ProgressBar value={progress.total ? (100 * progress.actual) / progress.total : 0}
            label={`${t("indexing", { a: progress.actual, t: progress.total })} · ${t("elapsed", { s: progress.segundos })}${progress.archivo ? ` · ${progress.archivo}` : ""}`} />
        )}
        {status?.estado === "error" && <p role="alert" className="text-sm text-danger-ink">{status.detalle}</p>}
      </>
    );
  } else if (step === 4) {
    next = { label: t("finish"), disabled: !state.puede_terminar || busy, run: () => setStep(5) };
    const rate = (q: StudyQuestion, correcta: Correct, fuentes: number | null) =>
      act(() => call("POST", "/rate", { n: q.n, correcta, fuentes_utiles: fuentes }));
    content = (
      <>
        <p className="text-sm text-ink-2">{t("q_text", { min: MIN_QUESTIONS })}</p>
        <p className="rounded-lg bg-accent-tint p-3 text-sm text-accent-ink">{t("q_hint")}</p>
        <form className="flex gap-2" onSubmit={(e) => {
          e.preventDefault();
          const q = question.trim();
          setPending({ q, since: Date.now() });
          setNow(Date.now());
          setQuestion("");
          void act(() => call("POST", "/ask", { pregunta: q, idioma: lang })).finally(() => setPending(null));
        }}>
          <input aria-label={t("q_ph")} className={input} placeholder={t("q_ph")} value={question} onChange={(e) => setQuestion(e.target.value)} disabled={busy} />
          <button className={btn} disabled={busy || question.trim().length < 3}>{busy ? t("asking") : t("ask")}</button>
        </form>
        <ol className="flex flex-col gap-4">
          {pending && (
            <li className="flex flex-col gap-2 rounded-lg border border-accent p-3" aria-live="polite">
              <p className="text-sm font-semibold">{pending.q}</p>
              <ProgressBar value={0} indeterminate label={t("thinking", { s: Math.max(0, Math.round((now - pending.since) / 1000)) })} />
            </li>
          )}
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
                    onClick={() => rate(q, c, q.valoracion?.fuentes_utiles ?? null)}>{t(`c_${c}` as Key)}</button>
                ))}
              </div>
              {q.valoracion && q.fuentes.length > 0 && (
                <div className="flex flex-wrap items-center gap-2" role="group" aria-label={t("useful")}>
                  <span className="text-xs text-muted">{t("useful")}</span>
                  {[1, 2, 3, 4, 5].map((k) => (
                    <button key={k} disabled={busy} aria-label={`${t("useful")} ${k}`}
                      className={`h-7 w-7 rounded-md border text-xs ${q.valoracion?.fuentes_utiles === k ? "border-accent bg-accent-tint font-semibold text-accent-ink" : "border-line text-ink-2"}`}
                      onClick={() => rate(q, q.valoracion!.correcta, k)}>{k}</button>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ol>
        <span className="text-xs text-muted">{t("count", { n: questions.length, min: MIN_QUESTIONS })}</span>
      </>
    );
  } else {
    content = (
      <>
        <h3 className="text-sm font-semibold">{t("exp_title")}</h3>
        <label className="text-sm">{t("years")}
          <input type="number" min={0} className={input} value={years} onChange={(e) => setYears(e.target.value)} />
        </label>
        <label className="text-sm">{t("usefulness")}
          <input type="number" min={1} max={5} className={input} value={usefulness} onChange={(e) => setUsefulness(e.target.value)} />
        </label>
        <p className="text-sm text-ink-2">{t("identity")}</p>
        <input aria-label={t("github")} placeholder={t("github")} className={input} value={github} onChange={(e) => setGithub(e.target.value)} />
        <textarea aria-label={t("intro_me")} placeholder={t("intro_me")} className={input} rows={3} value={intro} onChange={(e) => setIntro(e.target.value)} />
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" checked={sendOk} onChange={(e) => setSendOk(e.target.checked)} className="mt-1" />
          {t("consent_send")}
        </label>
        <button className={btn + " self-start"} disabled={!sendOk || busy} onClick={() => act(async () => {
          await call("POST", "/experience", { datos: { anios: Number(years) || null, likert: { util: Number(usefulness) || null } } });
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
              <button className={btn} onClick={() => download(files.archivos?.[0] ?? "beacon-estudio-resultados.json", files.resultado)}>{t("dl_results")}</button>
              <button className={btnGhost} onClick={() => {
                void navigator.clipboard?.writeText(JSON.stringify(files.resultado)).then(() => setCopied(true));
              }}>{copied ? t("copied") : t("copy")}</button>
              {files.identidad !== null && (
                <button className={btnGhost} onClick={() => download(files.archivos?.[1] ?? "beacon-estudio-identidad.json", files.identidad)}>{t("dl_identity")}</button>
              )}
            </div>
            <p className="text-sm text-muted">{t("sent_note")}</p>
          </div>
        )}
        <button className={btnGhost + " self-start"} disabled={busy} onClick={() => act(() => call("POST", "/discard"))}>{t("discard")}</button>
      </>
    );
  }

  return (
    <>
      <nav aria-label="progress" className="flex flex-col gap-2">
        <ProgressBar value={(100 * step) / STEPS.length} label={`${t("step_of", { n: step, total: STEPS.length })} · ${t(STEPS[step - 1])}`} />
        <ol className="flex justify-between text-xs">
          {STEPS.map((k, i) => (
            <li key={k} aria-current={i + 1 === step ? "step" : undefined}
              className={i + 1 === step ? "font-semibold text-accent-ink" : i + 1 < step ? "text-ink-2" : "text-muted"}>
              {i + 1 < step ? "✓ " : ""}{t(k)}
            </li>
          ))}
        </ol>
      </nav>
      <section className={section}>
        <h2 className="font-semibold">{t(STEPS[step - 1])}</h2>
        {content}
      </section>
      <div className="flex items-center justify-between">
        <button className={btnGhost} disabled={step === 1 || busy} onClick={() => setStep(step - 1)}>{t("back")}</button>
        {next && <button className={btn} disabled={next.disabled} onClick={() => void next!.run()}>{next.label}</button>}
      </div>
    </>
  );
}
