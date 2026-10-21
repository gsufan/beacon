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
interface HealthItem { id: string; estado: "ok" | "warn" | "error" | "skip"; valor: string }
interface StudyState {
  started: boolean;
  carpeta_repo?: string;
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
    repo_ph: "https://github.com/usuario/repositorio", prepare: "Preparar repositorio", examples: "O elige uno de estos repositorios de ejemplo:", use_repo: "Usar este", chosen: "Elegido", working_on: "Trabajando en:", storage_note: "Los repositorios clonados se guardan en {dir}. Si quieres recuperar espacio, puedes borrar esa carpeta (se borra sola al descartar la sesión). Para ver y limpiar el espacio de todos tus proyectos: beacon space y beacon clean.", closed: "Sesión descartada y servidor detenido. Ya puedes cerrar esta pestaña.",
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
    cancel: "Cancelar", cancelled_hint: "Si tarda demasiado, puedes cancelarla (se detiene sola a los 3 minutos).",
    health_title: "Revisión previa", health_retry: "Reintentar", health_checking: "Revisando tu equipo...",
    health_blocked: "Resuelve lo marcado con ✗ y pulsa Reintentar para poder comenzar.",
    health_ollama_ok: "Ollama responde en {v}", health_ollama_error: "Ollama no responde en {v}. Ábrelo (o ejecuta «ollama serve») y pulsa Reintentar.",
    health_model_embedding_ok: "Modelo de búsqueda {v} instalado", health_model_embedding_error: "Falta el modelo {v}. Ejecuta: ollama pull {v}",
    health_model_llm_ok: "Modelo de respuestas {v} instalado", health_model_llm_error: "Falta el modelo {v}. Ejecuta: ollama pull {v}",
    health_gpu_ok: "GPU: {v}", health_gpu_none: "No se detectó una GPU NVIDIA: las respuestas pueden tardar minutos (el estudio funciona igual).",
    health_gpu_warn: "GPU con poca memoria ({v}): puede ir lento o fallar.",
    health_disk_ok: "Espacio libre en disco: {v}", health_disk_warn: "Queda poco espacio ({v}): el repositorio clonado y el índice ocupan lugar.",
    health_disk_error: "Casi no queda espacio en disco ({v}).",
    health_network_ok: "Conexión a {v}", health_network_error: "No hay conexión a {v}: sin ella no se puede clonar un repositorio.",
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
    repo_ph: "https://github.com/user/repository", prepare: "Prepare repository", examples: "Or pick one of these example repositories:", use_repo: "Use this one", chosen: "Chosen", working_on: "Working on:", storage_note: "Cloned repositories are stored in {dir}. To recover space you can delete that folder (it is removed automatically when you discard the session). To see and clean the space of all your projects: beacon space and beacon clean.", closed: "Session discarded and server stopped. You can close this tab now.",
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
    cancel: "Cancel", cancelled_hint: "If it takes too long you can cancel it (it stops by itself after 3 minutes).",
    health_title: "Pre-check", health_retry: "Retry", health_checking: "Checking your machine...",
    health_blocked: "Fix what is marked with ✗ and press Retry to be able to start.",
    health_ollama_ok: "Ollama responds at {v}", health_ollama_error: "Ollama does not respond at {v}. Open it (or run 'ollama serve') and press Retry.",
    health_model_embedding_ok: "Search model {v} installed", health_model_embedding_error: "Model {v} is missing. Run: ollama pull {v}",
    health_model_llm_ok: "Answer model {v} installed", health_model_llm_error: "Model {v} is missing. Run: ollama pull {v}",
    health_gpu_ok: "GPU: {v}", health_gpu_none: "No NVIDIA GPU detected: answers can take minutes (the study works anyway).",
    health_gpu_warn: "GPU with little memory ({v}): it may be slow or fail.",
    health_disk_ok: "Free disk space: {v}", health_disk_warn: "Little space left ({v}): the cloned repository and the index take room.",
    health_disk_error: "Almost no disk space left ({v}).",
    health_network_ok: "Connection to {v}", health_network_error: "No connection to {v}: without it a repository cannot be cloned.",
  },
} as const;

type Key = keyof (typeof T)["es"];
type Tr = (k: Key, v?: Record<string, string | number>) => string;

const MIN_QUESTIONS = 3;
// Repositorios públicos para quien no tiene uno en mente.
const EXAMPLE_REPOS = [
  { name: "antirez/kilo", lang: "C", es: "Un editor de texto de terminal en un solo archivo. Ideal para una prueba rápida.", en: "A terminal text editor in a single file. Ideal for a quick try." },
  { name: "charmbracelet/glow", lang: "Go", es: "Lector de Markdown para la terminal, con interfaz de navegación.", en: "Markdown reader for the terminal, with a browsing interface." },
  { name: "psf/requests", lang: "Python", es: "La biblioteca de peticiones HTTP más usada de Python: sesiones, cookies, redirecciones.", en: "Python's most used HTTP library: sessions, cookies, redirects." },
  { name: "pallets/flask", lang: "Python", es: "Microframework web: rutas, plantillas, contexto de petición y extensiones.", en: "Web microframework: routing, templates, request context and extensions." },
  { name: "encode/httpx", lang: "Python", es: "Cliente HTTP moderno con soporte síncrono y asíncrono.", en: "Modern HTTP client with sync and async support." },
  { name: "expressjs/express", lang: "JavaScript", es: "Framework web minimalista para Node.js: enrutado y middleware.", en: "Minimalist web framework for Node.js: routing and middleware." },
  { name: "spring-projects/spring-petclinic", lang: "Java", es: "Aplicación de ejemplo oficial de Spring: una clínica veterinaria con dueños, mascotas y visitas.", en: "Official Spring sample application: a vet clinic with owners, pets and visits." },
  { name: "sharkdp/bat", lang: "Rust", es: "Un «cat» con resaltado de sintaxis, paginación e integración con git.", en: "A cat clone with syntax highlighting, paging and git integration." },
  { name: "junegunn/fzf", lang: "Go", es: "Buscador difuso interactivo de línea de comandos.", en: "Interactive fuzzy finder for the command line." },
  { name: "BurntSushi/ripgrep", lang: "Rust", es: "Buscador de texto recursivo muy rápido; buen ejemplo de proyecto con varios módulos.", en: "Very fast recursive text search; a good multi-crate example." },
  { name: "GoogleCloudPlatform/microservices-demo", lang: "Go, Python, Java, C#, JS", es: "Tienda en línea de ejemplo con 11 microservicios en distintos lenguajes.", en: "Sample online shop made of 11 microservices in different languages." },
] as const;
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
    Object.entries(v).reduce((s: string, [key, val]) => s.split(`{${key}}`).join(String(val)), T[lang][k] as string);
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
  const [health, setHealth] = useState<HealthItem[] | null>(null);
  const loadHealth = useCallback(async () => {
    setHealth(null);
    try {
      setHealth((await call<{ items?: HealthItem[] }>("GET", "/health")).items ?? []);
    } catch {
      setHealth([]);
    }
  }, []);
  useEffect(() => { if (step === 1) void loadHealth(); }, [step, loadHealth]);
  const healthBlocked = health === null || health.some((i) => i.estado === "error");
  // Paso 2
  const [hw, setHw] = useState<Record<string, unknown> | null>(null);
  const [hwOk, setHwOk] = useState(!!consent?.hardware);
  // Paso 3
  const [url, setUrl] = useState("");
  const [changing, setChanging] = useState(false);
  const [sawPreparing, setSawPreparing] = useState(false);
  const [workingOn, setWorkingOn] = useState("");
  const [closed, setClosed] = useState(false);
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
    // El formulario de «elegir otro» se cierra cuando termina de prepararse el repositorio nuevo: no antes, porque el
    // estado del anterior también dice «listo».
    if (!changing) return;
    if (preparing) setSawPreparing(true);
    else if (status?.estado === "listo" && (sawPreparing || (workingOn !== "" && state.repo?.url === workingOn))) {
      setChanging(false);
      setSawPreparing(false);
    }
  }, [changing, preparing, sawPreparing, status?.estado, workingOn, state.repo?.url]);

  // Cada paso define su acción principal (la que lo completa y avanza).
  let next: { label: string; disabled: boolean; run: () => Promise<void> | void } | null = null;
  let content: React.ReactNode = null;

  if (step === 1) {
    next = {
      label: t("start"), disabled: !agree || busy || healthBlocked,
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
        <div className="flex flex-col gap-2 rounded-lg border border-line p-3" aria-label={t("health_title")}>
          <p className="text-sm font-semibold">{t("health_title")}</p>
          {health === null && <p role="status" className="text-xs text-muted">{t("health_checking")}</p>}
          <ul className="flex flex-col gap-1">
            {(health ?? []).filter((i) => i.estado !== "skip").map((i) => {
              const key = (i.id === "gpu" && i.estado === "warn" && !i.valor ? "health_gpu_none" : `health_${i.id}_${i.estado}`) as Key;
              const mark = i.estado === "ok" ? "✓" : i.estado === "warn" ? "⚠" : "✗";
              return (
                <li key={i.id} className={`text-xs ${i.estado === "error" ? "text-danger-ink" : i.estado === "warn" ? "text-ink-2" : "text-muted"}`}>
                  <span aria-hidden="true">{mark}</span> {t(key, { v: i.valor })}
                </li>
              );
            })}
          </ul>
          {health !== null && healthBlocked && <p role="alert" className="text-xs text-danger-ink">{t("health_blocked")}</p>}
          <button type="button" className={btnGhost + " self-start !px-3 !py-1 text-xs"} disabled={health === null} onClick={() => void loadHealth()}>{t("health_retry")}</button>
        </div>
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
            <p className="text-sm font-semibold">{t("examples")}</p>
            <ul className="flex max-h-80 flex-col gap-2 overflow-y-auto pr-1" aria-label={t("examples")}>
              {EXAMPLE_REPOS.map((r) => {
                const repoUrl = `https://github.com/${r.name}`;
                return (
                  <li key={r.name} className={`flex items-start justify-between gap-3 rounded-lg border p-3 ${url === repoUrl ? "border-accent bg-accent-tint" : "border-line"}`}>
                    <div className="flex flex-col gap-1">
                      <p className="text-sm">
                        <a href={repoUrl} target="_blank" rel="noreferrer" className="font-semibold text-accent-ink underline">{r.name}</a>
                        <span className="ml-2 text-xs text-muted">{r.lang}</span>
                      </p>
                      <p className="text-xs text-ink-2">{lang === "es" ? r.es : r.en}</p>
                    </div>
                    <button className={btnGhost + " shrink-0 !px-3 !py-1 text-xs"} disabled={preparing} aria-label={`${t("use_repo")} ${r.name}`}
                      onClick={() => setUrl(repoUrl)}>{url === repoUrl ? t("chosen") : t("use_repo")}</button>
                  </li>
                );
              })}
            </ul>
            <button className={btn + " self-start"} disabled={!url.trim() || busy || preparing}
              onClick={() => {
                if (changing && url.trim() === state.repo?.url) { setChanging(false); return; }  // ya está preparado: no se repite el indexado
                setWorkingOn(url.trim());
                void act(() => call("POST", "/repo", { url: url.trim() }));
              }}>{t("prepare")}</button>
          </>
        ) : (
          <>
            <p className="text-sm text-ink-2">{t("repo_ready", { n: state.repo?.archivos_indexados ?? 0 })} <span className="font-mono text-xs">{state.repo?.url}</span></p>
            {questions.length === 0
              ? <button className={btnGhost + " self-start"} onClick={() => { setSawPreparing(false); setWorkingOn(""); setUrl(""); setChanging(true); }}>{t("change_repo")}</button>
              : <p className="text-xs text-muted">{t("repo_locked")}</p>}
          </>
        )}
        {status?.estado === "clonando" && <ProgressBar value={0} indeterminate label={t("cloning")} />}
        {status?.estado === "indexando" && progress && (
          <ProgressBar value={progress.total ? (100 * progress.actual) / progress.total : 0}
            label={`${t("indexing", { a: progress.actual, t: progress.total })} · ${t("elapsed", { s: progress.segundos })}${progress.archivo ? ` · ${progress.archivo}` : ""}`} />
        )}
        {status?.estado === "error" && <p role="alert" className="text-sm text-danger-ink">{status.detalle}</p>}
        {state.carpeta_repo && <p className="text-xs text-muted">{t("storage_note", { dir: state.carpeta_repo })}</p>}
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
              <div className="flex items-center gap-3">
                <button type="button" className={btnGhost + " !px-3 !py-1 text-xs"} onClick={() => void call("POST", "/cancel").catch(() => undefined)}>{t("cancel")}</button>
                <span className="text-xs text-muted">{t("cancelled_hint")}</span>
              </div>
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
        {state.carpeta_repo && <p className="text-xs text-muted">{t("storage_note", { dir: state.carpeta_repo })}</p>}
        <button className={btnGhost + " self-start"} disabled={busy} onClick={() => act(async () => {
          await call("POST", "/quit");  // descarta la sesión y detiene el servidor
          setClosed(true);
        })}>{t("discard")}</button>
      </>
    );
  }

  if (closed) return <p role="status" className="rounded-xl border border-line bg-surface p-5 text-sm">{t("closed")}</p>;

  // El repositorio con el que se trabaja: el ya listo o el que se está preparando.
  const preparingUrl = preparing && status?.detalle?.startsWith("http") ? status.detalle : "";
  const shownUrl = state.repo?.url || preparingUrl || workingOn;

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
      {step >= 3 && shownUrl && (
        <p className="text-sm text-ink-2">{t("working_on")} <a href={shownUrl} target="_blank" rel="noreferrer" className="font-mono text-xs text-accent-ink underline">{shownUrl}</a></p>
      )}
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
