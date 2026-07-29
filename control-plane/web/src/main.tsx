import React, { FormEvent, useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

type Service = {
  id: string;
  name: string;
  description: string;
  state: string;
  healthy: boolean;
  managed: boolean;
  latency_ms?: number;
  details?: Record<string, unknown>;
  error?: string;
};

type Tab = "overview" | "resources" | "chat" | "logs" | "config" | "maintenance";
type Locale = "fr" | "en";
type Diagnostics = {
  model?: string; memory_bytes?: number; cpu_cores?: number; metal_compatible?: boolean;
  profile?: string; recommendation?: string; ollama_version?: string;
};
type OllamaModel = {
  model?: string; loaded?: boolean; engine_online?: boolean; vram_bytes?: number;
  context_length?: number;
};
type ResourceSample = {
  time: string; cpu_percent: number; memory_bytes: number; memory_total_bytes: number; model_memory_bytes: number;
  storage_used_bytes: number; storage_total_bytes: number;
  homecortex_storage_bytes: number;
  network_receive_bps: number; network_transmit_bps: number;
};
type Backup = {
  name: string; size: number; created_at: string;
  includes_tts_cache: boolean; includes_secrets: boolean;
};

const translations = {
  fr: {
    tabs: { overview: "Vue d’ensemble", resources: "Ressources", chat: "Chat", logs: "Logs", config: "Configuration", maintenance: "Maintenance" },
    local: "Local",
    smartHome: "KIRA · MAISON INTELLIGENTE",
    healthyServices: "services sains",
    machine: "Machine",
    automaticCheck: "vérification automatique",
    coreLatency: "latence du health check",
    runtime: "RUNTIME",
    services: "Services",
    refresh: "Actualiser",
    start: "Démarrer",
    restart: "Redémarrer",
    stop: "Arrêter",
    observed: "Observé",
    loadModel: "Charger",
    unloadModel: "Décharger",
    restartModel: "Recharger",
    modelLoaded: "modèle chargé",
    modelUnloaded: "modèle déchargé",
    hardware: "MATÉRIEL",
    platformProfile: "Profil de la plateforme",
    unifiedMemory: "mémoire unifiée",
    metalReady: "Metal compatible",
    editFile: "Fichier à éditer",
    liveResources: "UTILISATION EN DIRECT",
    deployedEnvironment: "Environnement HomeCortex",
    cpu: "CPU",
    memory: "Mémoire",
    storage: "Stockage",
    network: "Réseau",
    received: "Reçu",
    transmitted: "Envoyé",
    hostNetwork: "Le réseau représente l’activité totale du Mac.",
    backupRestore: "SAUVEGARDE ET RESTAURATION",
    recoveryPoints: "Points de restauration",
    createBackup: "Créer une sauvegarde",
    includeTTS: "Inclure le cache TTS",
    noBackups: "Aucune sauvegarde disponible.",
    restore: "Restaurer",
    containsSecrets: "contient .env",
    backupCreated: "Sauvegarde créée.",
    restoreConfirm: "Restaurer cette sauvegarde ? Une sauvegarde de sécurité sera créée et Kira redémarrera.",
    restored: "Sauvegarde restaurée. Kira redémarre.",
    directTest: "TEST DIRECT",
    conversation: "Conversation avec Kira",
    kiraStatus: "Assistante locale · disponible",
    room: "Pièce",
    welcome: "Bonjour, je suis Kira. Que veux-tu tester ?",
    you: "Vous",
    chatPlaceholder: "Demandez quelque chose à Kira…",
    send: "Envoyer",
    emptyReply: "Réponse vide",
    realTime: "TEMPS RÉEL",
    serviceLogs: "Journal des services",
    resume: "Reprendre",
    pause: "Pause",
    clear: "Effacer",
    waitingLogs: "En attente de nouvelles lignes…",
    advancedMode: "MODE AVANCÉ",
    save: "Valider et enregistrer",
    saved: "Configuration enregistrée. Un redémarrage de Kira Core est nécessaire.",
    unavailable: "Control Plane indisponible",
    actionFailed: "Action impossible",
    descriptions: {
      "homecortex-core": "Pipeline vocal, Home Assistant et mémoire",
      ollama: "Moteur d’inférence LLM local",
      "home-assistant": "Plateforme domotique observée",
    },
    states: {
      healthy: "sain", offline: "hors ligne", degraded: "dégradé",
      unconfigured: "non configuré", unknown: "inconnu",
    },
  },
  en: {
    tabs: { overview: "Overview", resources: "Resources", chat: "Chat", logs: "Logs", config: "Configuration", maintenance: "Maintenance" },
    local: "Local",
    smartHome: "KIRA · SMART HOME",
    healthyServices: "healthy services",
    machine: "Machine",
    automaticCheck: "automatic health check",
    coreLatency: "health-check latency",
    runtime: "RUNTIME",
    services: "Services",
    refresh: "Refresh",
    start: "Start",
    restart: "Restart",
    stop: "Stop",
    observed: "Observed",
    loadModel: "Load",
    unloadModel: "Unload",
    restartModel: "Reload",
    modelLoaded: "model loaded",
    modelUnloaded: "model unloaded",
    hardware: "HARDWARE",
    platformProfile: "Platform profile",
    unifiedMemory: "unified memory",
    metalReady: "Metal compatible",
    editFile: "File to edit",
    liveResources: "LIVE USAGE",
    deployedEnvironment: "HomeCortex environment",
    cpu: "CPU",
    memory: "Memory",
    storage: "Storage",
    network: "Network",
    received: "Received",
    transmitted: "Sent",
    hostNetwork: "Network represents total Mac activity.",
    backupRestore: "BACKUP AND RESTORE",
    recoveryPoints: "Recovery points",
    createBackup: "Create backup",
    includeTTS: "Include TTS cache",
    noBackups: "No backups available.",
    restore: "Restore",
    containsSecrets: "contains .env",
    backupCreated: "Backup created.",
    restoreConfirm: "Restore this backup? A safety backup will be created and Kira will restart.",
    restored: "Backup restored. Kira is restarting.",
    directTest: "DIRECT TEST",
    conversation: "Conversation with Kira",
    kiraStatus: "Local assistant · available",
    room: "Room",
    welcome: "Hello, I’m Kira. What would you like to test?",
    you: "You",
    chatPlaceholder: "Ask Kira something…",
    send: "Send",
    emptyReply: "Empty response",
    realTime: "REAL TIME",
    serviceLogs: "Service logs",
    resume: "Resume",
    pause: "Pause",
    clear: "Clear",
    waitingLogs: "Waiting for new log lines…",
    advancedMode: "ADVANCED MODE",
    save: "Validate and save",
    saved: "Configuration saved. Kira Core must be restarted.",
    unavailable: "Control Plane unavailable",
    actionFailed: "Action failed",
    descriptions: {
      "homecortex-core": "Voice pipeline, Home Assistant and memory",
      ollama: "Local LLM inference engine",
      "home-assistant": "Observed home-automation platform",
    },
    states: {
      healthy: "healthy", offline: "offline", degraded: "degraded",
      unconfigured: "unconfigured", unknown: "unknown",
    },
  },
};

function App() {
  const [tab, setTab] = useState<Tab>("overview");
  const [locale, setLocale] = useState<Locale>(() => {
    const stored = window.localStorage.getItem("homecortex-locale");
    if (stored === "fr" || stored === "en") return stored;
    return navigator.language.toLowerCase().startsWith("fr") ? "fr" : "en";
  });
  const [services, setServices] = useState<Service[]>([]);
  const [system, setSystem] = useState<Record<string, string>>({});
  const [diagnostics, setDiagnostics] = useState<Diagnostics>({});
  const [ollamaModel, setOllamaModel] = useState<OllamaModel>({});
  const [error, setError] = useState("");
  const t = translations[locale];

  const changeLocale = (next: Locale) => {
    setLocale(next);
    window.localStorage.setItem("homecortex-locale", next);
  };

  const refresh = async () => {
    try {
      const [serviceResponse, systemResponse, diagnosticsResponse, modelResponse] = await Promise.all([
        fetch("/api/v1/services"),
        fetch("/api/v1/system"),
        fetch("/api/v1/diagnostics"),
        fetch("/api/v1/ollama/model"),
      ]);
      if (!serviceResponse.ok || !systemResponse.ok) throw new Error(t.unavailable);
      setServices((await serviceResponse.json()).services);
      setSystem(await systemResponse.json());
      if (diagnosticsResponse.ok) setDiagnostics(await diagnosticsResponse.json());
      if (modelResponse.ok) setOllamaModel(await modelResponse.json());
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : String(reason));
    }
  };

  useEffect(() => {
    refresh();
    const events = new EventSource("/api/v1/events");
    events.addEventListener("services", (event) => {
      setServices(JSON.parse((event as MessageEvent).data).services);
    });
    return () => events.close();
  }, []);

  const healthy = services.filter((service) => service.healthy).length;

  return (
    <div className="shell">
      <aside>
        <div className="brand"><span className="brand-mark"><img src="/homecortex-logo.png" alt="" /></span><div><strong>HomeCortex</strong><small>Control Plane</small></div></div>
        <nav>
          {(Object.keys(t.tabs) as Tab[]).map((item) => (
            <button key={item} className={tab === item ? "active" : ""} onClick={() => setTab(item)}>
              {t.tabs[item]}
            </button>
          ))}
        </nav>
        <div className="aside-foot"><span className="dot healthy" /> {t.local} · {system.version ?? "…"}</div>
      </aside>
      <main>
        <header>
          <div><p className="eyebrow">{t.smartHome}</p><h1>{t.tabs[tab]}</h1></div>
          <div className="header-actions">
            <div className="locale-switch" aria-label="Language">
              <button className={locale === "fr" ? "selected" : ""} onClick={() => changeLocale("fr")}>FR</button>
              <button className={locale === "en" ? "selected" : ""} onClick={() => changeLocale("en")}>EN</button>
            </div>
            <div className="health-summary"><span className="dot healthy" />{healthy}/{services.length} {t.healthyServices}</div>
          </div>
        </header>
        {error && <div className="alert">{error}</div>}
        {tab === "overview" && <Overview services={services} system={system} diagnostics={diagnostics} ollamaModel={ollamaModel} refresh={refresh} t={t} />}
        {tab === "resources" && <Resources t={t} />}
        {tab === "chat" && <Chat t={t} />}
        {tab === "logs" && <Logs services={services} t={t} />}
        {tab === "config" && <Configuration t={t} />}
        {tab === "maintenance" && <Maintenance t={t} />}
      </main>
    </div>
  );
}

type Translation = typeof translations.fr;

function formatBytes(value?: number) {
  if (!value) return "—";
  if (value < 1024 * 1024 * 1024) return `${(value / 1024 / 1024).toFixed(0)} MB`;
  return `${(value / 1024 / 1024 / 1024).toFixed(1)} GB`;
}

function Overview({ services, system, diagnostics, ollamaModel, refresh, t }: {
  services: Service[]; system: Record<string, string>; diagnostics: Diagnostics;
  ollamaModel: OllamaModel; refresh: () => void; t: Translation;
}) {
  const action = async (id: string, name: string) => {
    const response = await fetch(`/api/v1/services/${id}/${name}`, { method: "POST" });
    if (!response.ok) {
      const body = await response.json();
      window.alert(body.error ?? t.actionFailed);
    }
    setTimeout(refresh, 700);
  };
  const modelAction = async (name: string) => {
    const response = await fetch(`/api/v1/ollama/model/${name}`, { method: "POST" });
    const body = await response.json();
    if (!response.ok) window.alert(body.error ?? t.actionFailed);
    await refresh();
  };
  return (
    <>
      <section className="stats">
        <article><span>{t.machine}</span><strong>{system.hostname ?? "…"}</strong><small>{system.os} · {system.arch}</small></article>
        <article><span>{t.healthyServices}</span><strong>{services.filter((s) => s.healthy).length}/{services.length}</strong><small>{t.automaticCheck}</small></article>
        <article><span>Kira Core</span><strong>{services.find((s) => s.id === "homecortex-core")?.latency_ms ?? "—"} ms</strong><small>{t.coreLatency}</small></article>
      </section>
      <section className="section">
        <div className="section-heading"><div><p className="eyebrow">{t.runtime}</p><h2>{t.services}</h2></div><button className="secondary" onClick={refresh}>{t.refresh}</button></div>
        <div className="service-list">
          {services.map((service) => (
            <article className="service" key={service.id}>
              <div className="service-main"><span className={`status-icon ${service.healthy ? "ok" : ""}`} /><div><strong>{service.name}</strong><small>{service.id === "ollama" ? `${ollamaModel.model ?? "—"} · ${ollamaModel.loaded ? t.modelLoaded : t.modelUnloaded}` : (t.descriptions[service.id as keyof typeof t.descriptions] ?? service.description)}</small></div></div>
              <div className="service-state"><span className={`badge ${service.healthy ? "ok" : ""}`}>{t.states[service.state as keyof typeof t.states] ?? service.state}</span><small>{service.id === "ollama" && ollamaModel.loaded ? formatBytes(ollamaModel.vram_bytes) : service.latency_ms != null ? `${service.latency_ms} ms` : "—"}</small></div>
              <div className="actions">
                {service.managed ? <>
                  <button onClick={() => action(service.id, "start")}>{t.start}</button>
                  <button onClick={() => action(service.id, "restart")}>{t.restart}</button>
                  <button onClick={() => action(service.id, "stop")}>{t.stop}</button>
                </> : service.id === "ollama" ? <>
                  <button onClick={() => modelAction("load")}>{t.loadModel}</button>
                  <button onClick={() => modelAction("restart")}>{t.restartModel}</button>
                  <button onClick={() => modelAction("unload")}>{t.unloadModel}</button>
                </> : <span className="observed">{t.observed}</span>}
              </div>
            </article>
          ))}
        </div>
      </section>
      <section className="section hardware-section">
        <div className="section-heading"><div><p className="eyebrow">{t.hardware}</p><h2>{t.platformProfile}</h2></div></div>
        <div className="hardware-grid">
          <article><span>Apple Silicon</span><strong>{diagnostics.model ?? "—"}</strong><small>{diagnostics.cpu_cores ?? "—"} CPU cores</small></article>
          <article><span>{t.unifiedMemory}</span><strong>{formatBytes(diagnostics.memory_bytes)}</strong><small>{diagnostics.metal_compatible ? t.metalReady : "Metal unavailable"}</small></article>
          <article><span>HomeCortex</span><strong>{diagnostics.profile ?? "—"}</strong><small>{diagnostics.recommendation ?? ""}</small></article>
        </div>
      </section>
    </>
  );
}

function Chat({ t }: { t: Translation }) {
  const [messages, setMessages] = useState<{ role: string; text: string }[]>([
    { role: "kira", text: t.welcome },
  ]);
  const [text, setText] = useState("");
  const [room, setRoom] = useState("chat");
  const [sending, setSending] = useState(false);
  useEffect(() => {
    setMessages((current) => current.length === 1 && current[0].role === "kira"
      ? [{ role: "kira", text: t.welcome }]
      : current);
  }, [t.welcome]);
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const prompt = text.trim();
    if (!prompt || sending) return;
    setMessages((current) => [...current, { role: "user", text: prompt }]);
    setText("");
    setSending(true);
    try {
      const response = await fetch("/api/v1/chat", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ text: prompt, room }),
      });
      const body = await response.json();
      setMessages((current) => [...current, { role: "kira", text: body.reply ?? body.error ?? t.emptyReply }]);
    } catch (reason) {
      setMessages((current) => [...current, { role: "kira", text: String(reason) }]);
    } finally {
      setSending(false);
    }
  };
  return (
    <section className="chat-panel">
      <div className="chat-top"><div className="kira-profile"><div className="kira-portrait"><img src="/kira-avatar.png" alt="Kira" /><span /></div><div><p className="eyebrow">{t.directTest}</p><h2>{t.conversation}</h2><small>{t.kiraStatus}</small></div></div>
        <label>{t.room}<input value={room} onChange={(event) => setRoom(event.target.value)} /></label></div>
      <div className="messages">{messages.map((message, index) =>
        <div key={index} className={`message ${message.role}`}>
          {message.role === "kira" && <img className="message-avatar" src="/kira-avatar.png" alt="" />}
          <div><span>{message.role === "kira" ? "Kira" : t.you}</span><p>{message.text}</p></div>
        </div>
      )}</div>
      <form className="composer" onSubmit={submit}><input value={text} onChange={(event) => setText(event.target.value)} placeholder={t.chatPlaceholder} /><button disabled={sending}>{sending ? "…" : t.send}</button></form>
    </section>
  );
}

function Logs({ services, t }: { services: Service[]; t: Translation }) {
  const available = useMemo(() => services.filter((service) => service.id === "homecortex-core"), [services]);
  const [selected, setSelected] = useState("homecortex-core");
  const [lines, setLines] = useState<string[]>([]);
  const [paused, setPaused] = useState(false);
  useEffect(() => {
    if (paused) return;
    setLines([]);
    const events = new EventSource(`/api/v1/logs/stream?service=${encodeURIComponent(selected)}`);
    events.onmessage = (event) => setLines((current) => [...current.slice(-499), JSON.parse(event.data).line]);
    return () => events.close();
  }, [selected, paused]);
  return (
    <section className="section logs-section">
      <div className="section-heading"><div><p className="eyebrow">{t.realTime}</p><h2>{t.serviceLogs}</h2></div>
        <div className="toolbar"><select value={selected} onChange={(event) => setSelected(event.target.value)}>{available.map((service) => <option key={service.id} value={service.id}>{service.name}</option>)}</select><button className="secondary" onClick={() => setPaused(!paused)}>{paused ? t.resume : t.pause}</button><button className="secondary" onClick={() => setLines([])}>{t.clear}</button></div></div>
      <pre className="terminal">{lines.length ? lines.join("\n") : t.waitingLogs}</pre>
    </section>
  );
}

function formatRate(value: number) {
  if (value >= 1024 * 1024) return `${(value / 1024 / 1024).toFixed(1)} MB/s`;
  if (value >= 1024) return `${(value / 1024).toFixed(1)} KB/s`;
  return `${value.toFixed(0)} B/s`;
}

function DialGauge({ label, percent, value, detail }: {
  label: string; percent: number; value: string; detail: string;
}) {
  const bounded = Math.max(0, Math.min(percent, 100));
  const angle = -90 + bounded * 1.8;
  return (
    <article className="instrument dial-card">
      <div className="instrument-title"><span>{label}</span><strong>{value}</strong></div>
      <svg viewBox="0 0 220 140" role="img" aria-label={`${label}: ${value}`}>
        <path d="M 30 112 A 80 80 0 0 1 190 112" pathLength="100" className="dial-track" />
        <path d="M 30 112 A 80 80 0 0 1 190 112" pathLength="100" className="dial-zone zone-green" />
        <path d="M 30 112 A 80 80 0 0 1 190 112" pathLength="100" className="dial-zone zone-orange" />
        <path d="M 30 112 A 80 80 0 0 1 190 112" pathLength="100" className="dial-zone zone-red" />
        <line x1="110" y1="112" x2="110" y2="45" className="dial-needle" transform={`rotate(${angle} 110 112)`} />
        <circle cx="110" cy="112" r="7" className="dial-hub" />
        <text x="28" y="132">0</text><text x="177" y="132">100</text>
      </svg>
      <small>{detail}</small>
    </article>
  );
}

function SegmentedGauge({ label, percent, value, detail, tone = "capacity" }: {
  label: string; percent: number; value: string; detail: string; tone?: "capacity" | "receive" | "transmit";
}) {
  const segments = 22;
  const active = Math.round(Math.max(0, Math.min(percent, 100)) / 100 * segments);
  return (
    <article className="instrument segmented-card">
      <div className="instrument-title"><span>{label}</span><strong>{value}</strong></div>
      <div className={`segments ${tone}`} role="img" aria-label={`${label}: ${value}`}>
        {Array.from({ length: segments }, (_, index) => <i key={index} className={index < active ? "active" : ""} />)}
      </div>
      <div className="segment-detail"><small>{detail}</small><b>{Math.round(percent)}%</b></div>
    </article>
  );
}

function Resources({ t }: { t: Translation }) {
  const [samples, setSamples] = useState<ResourceSample[]>([]);
  useEffect(() => {
    let active = true;
    const collect = async () => {
      try {
        const response = await fetch("/api/v1/resources");
        if (response.ok && active) {
          const sample = await response.json();
          setSamples((current) => [...current.slice(-59), sample]);
        }
      } catch { /* the next interval retries */ }
    };
    collect();
    const timer = window.setInterval(collect, 2000);
    return () => { active = false; window.clearInterval(timer); };
  }, []);
  const current = samples.at(-1);
  const receiveValues = samples.map((sample) => sample.network_receive_bps);
  const transmitValues = samples.map((sample) => sample.network_transmit_bps);
  const networkMaximum = Math.max(1024, ...receiveValues, ...transmitValues);
  const memoryPercent = current ? current.memory_bytes / Math.max(current.memory_total_bytes, 1) * 100 : 0;
  const storagePercent = current ? current.storage_used_bytes / Math.max(current.storage_total_bytes, 1) * 100 : 0;
  const receivePercent = current ? current.network_receive_bps / networkMaximum * 100 : 0;
  const transmitPercent = current ? current.network_transmit_bps / networkMaximum * 100 : 0;
  return (
    <section className="section resources-section">
      <div className="section-heading"><div><p className="eyebrow">{t.liveResources}</p><h2>{t.deployedEnvironment}</h2></div><small>{t.hostNetwork}</small></div>
      <div className="instruments-grid">
        <DialGauge label={t.cpu} percent={current?.cpu_percent ?? 0} value={`${current?.cpu_percent.toFixed(1) ?? "—"} %`} detail="HomeCortex · 0–100%" />
        <DialGauge label={t.memory} percent={memoryPercent} value={formatBytes(current?.memory_bytes)} detail={`Model: ${formatBytes(current?.model_memory_bytes)} · / ${formatBytes(current?.memory_total_bytes)}`} />
        <SegmentedGauge label={t.storage} percent={storagePercent} value={`${formatBytes(current?.storage_used_bytes)} / ${formatBytes(current?.storage_total_bytes)}`} detail={`HomeCortex: ${formatBytes(current?.homecortex_storage_bytes)}`} />
        <div className="network-instrument">
          <div className="instrument-title"><span>{t.network}</span><strong>{formatRate(Math.max(current?.network_receive_bps ?? 0, current?.network_transmit_bps ?? 0))}</strong></div>
          <SegmentedGauge label={t.received} percent={receivePercent} value={formatRate(current?.network_receive_bps ?? 0)} detail="Mac" tone="receive" />
          <SegmentedGauge label={t.transmitted} percent={transmitPercent} value={formatRate(current?.network_transmit_bps ?? 0)} detail="Mac" tone="transmit" />
        </div>
      </div>
    </section>
  );
}

function Configuration({ t }: { t: Translation }) {
  const files = [
    { id: "config", label: "config/kira.yaml" },
    { id: "prompt_fr", label: "prompt_fr.txt" },
    { id: "prompt_suffix_fr", label: "prompt_suffix_fr.txt" },
    { id: "prompt_en", label: "prompt_en.txt" },
    { id: "prompt_suffix_en", label: "prompt_suffix_en.txt" },
  ];
  const [selected, setSelected] = useState("config");
  const [content, setContent] = useState("");
  const [message, setMessage] = useState("");
  useEffect(() => {
    setMessage("");
    fetch(`/api/v1/files/${selected}`).then((r) => r.json()).then((body) => setContent(body.content ?? ""));
  }, [selected]);
  const save = async () => {
    const response = await fetch(`/api/v1/files/${selected}`, {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content }),
    });
    const body = await response.json();
    setMessage(response.ok ? t.saved : body.error);
  };
  return (
    <section className="section config-section">
      <div className="section-heading"><div><p className="eyebrow">{t.advancedMode}</p><h2>{files.find((file) => file.id === selected)?.label}</h2></div><div className="toolbar"><label className="file-picker">{t.editFile}<select value={selected} onChange={(event) => setSelected(event.target.value)}>{files.map((file) => <option value={file.id} key={file.id}>{file.label}</option>)}</select></label><button className="primary" onClick={save}>{t.save}</button></div></div>
      {message && <div className="notice">{message}</div>}
      <textarea value={content} onChange={(event) => setContent(event.target.value)} spellCheck={false} />
    </section>
  );
}

function Maintenance({ t }: { t: Translation }) {
  const [backups, setBackups] = useState<Backup[]>([]);
  const [includeTTS, setIncludeTTS] = useState(false);
  const [working, setWorking] = useState("");
  const [message, setMessage] = useState("");
  const refresh = async () => {
    const response = await fetch("/api/v1/backups");
    const body = await response.json();
    if (response.ok) setBackups(body.backups ?? []);
  };
  useEffect(() => { refresh(); }, []);
  const create = async () => {
    setWorking("create"); setMessage("");
    const response = await fetch("/api/v1/backups", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ include_tts_cache: includeTTS }),
    });
    const body = await response.json();
    setMessage(response.ok ? t.backupCreated : body.error);
    setWorking(""); await refresh();
  };
  const restore = async (backup: Backup) => {
    if (!window.confirm(t.restoreConfirm)) return;
    setWorking(backup.name); setMessage("");
    const response = await fetch(`/api/v1/backups/${encodeURIComponent(backup.name)}/restore`, { method: "POST" });
    const body = await response.json();
    setMessage(response.ok ? t.restored : body.error);
    setWorking(""); await refresh();
  };
  return (
    <section className="section maintenance-section">
      <div className="section-heading"><div><p className="eyebrow">{t.backupRestore}</p><h2>{t.recoveryPoints}</h2></div>
        <div className="backup-actions"><label><input type="checkbox" checked={includeTTS} onChange={(event) => setIncludeTTS(event.target.checked)} />{t.includeTTS}</label><button className="primary" disabled={!!working} onClick={create}>{working === "create" ? "…" : t.createBackup}</button></div>
      </div>
      {message && <div className="notice">{message}</div>}
      <div className="backup-list">
        {backups.length === 0 && <div className="empty-state">{t.noBackups}</div>}
        {backups.map((backup) => <article key={backup.name}>
          <div><strong>{backup.name}</strong><small>{new Date(backup.created_at).toLocaleString()} · {formatBytes(backup.size)} · {backup.includes_secrets ? t.containsSecrets : ""}{backup.includes_tts_cache ? ` · TTS` : ""}</small></div>
          <button className="danger-outline" disabled={!!working} onClick={() => restore(backup)}>{working === backup.name ? "…" : t.restore}</button>
        </article>)}
      </div>
    </section>
  );
}

createRoot(document.getElementById("root")!).render(<React.StrictMode><App /></React.StrictMode>);
