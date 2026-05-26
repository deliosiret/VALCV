import React from "react";
import { createRoot } from "react-dom/client";
import {
  BarChart3,
  Bot,
  Copy,
  FilePenLine,
  FileText,
  FileUp,
  Gauge,
  LogOut,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  Settings,
  SlidersHorizontal,
  Star,
  Trash2,
  UserRoundPlus,
  Users,
  X,
} from "lucide-react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Legend,
  PolarAngleAxis,
  PolarGrid,
  PolarRadiusAxis,
  Radar,
  RadarChart,
  RadialBar,
  RadialBarChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import "./styles.css";

const API_URL = import.meta.env.VITE_API_URL ?? "http://localhost:18429";
const COLORS = ["#16697a", "#db6400", "#489fb5", "#7a4eab", "#2f855a", "#c2410c"];
const MANUAL_EVIDENCE_NOTE = "Estos tópicos deben evaluarse en la entrevista y/o examen temático";
const buttonClass =
  "inline-flex min-h-9 cursor-pointer items-center justify-center gap-2 rounded-md border-0 bg-brand px-3 py-2 text-white disabled:cursor-wait disabled:opacity-55";
const inputClass =
  "min-h-9 w-full rounded-md border border-[#ccd8d9] bg-white px-2.5 py-2 text-ink outline-none focus:border-brand focus:ring-2 focus:ring-brand/15";
const panelClass = "rounded-lg border border-line bg-surface p-4";
const headingClass = "mb-3.5 flex items-center gap-2 text-base font-semibold tracking-normal text-ink";
const mutedTextClass = "block text-xs leading-snug text-muted";
const aiLockedTitle = "Este criterio fue evaluado por AI y la plantilla bloquea la edición manual de puntuación, evidencia y documentos.";

function candidateColor(index: number) {
  return COLORS[index % COLORS.length];
}

function toPercentInput(value: number) {
  const percent = (Number(value) || 0) * 100;
  return Number.isInteger(percent) ? String(percent) : String(Number(percent.toFixed(4)));
}

function fromPercentInput(value: string) {
  return (Number(value) || 0) / 100;
}

function clampWeight(value: number, maxValue = 1) {
  return Math.max(0, Math.min(Number(value) || 0, maxValue));
}

function percentStatus(total: number) {
  const diff = 1 - total;
  const percent = Math.abs(diff) * 100;
  if (Math.abs(diff) < 0.0001) return { ok: true, text: "100% completo", className: "bg-[#e6f1ef] text-brand" };
  if (diff > 0) return { ok: false, text: `Falta ${toPercentInput(diff)}%`, className: "bg-[#fff7ed] text-[#9a3412]" };
  return { ok: false, text: `Excede ${toPercentInput(percent / 100)}%`, className: "bg-[#fee2e2] text-[#9a3412]" };
}

function ignoreNumberWheel(event: React.WheelEvent<HTMLInputElement>) {
  event.currentTarget.blur();
}

type Mode = "manual" | "automatic";

type Criterion = {
  id: number;
  code: string;
  category: string;
  aspect: string;
  category_weight: number;
  within_category_weight: number;
  global_weight: number;
  scale: string;
  notes: string;
  is_critical: boolean;
  evaluation_mode: Mode;
  order_index: number;
};

type Template = {
  id: number;
  name: string;
  description: string;
  ai_evaluation_locked: boolean;
  categories: TemplateCategory[];
  criteria: Criterion[];
};

type CriterionDraft = Omit<Criterion, "id"> & { id?: number };

type TemplateCategory = {
  id?: number;
  name: string;
  weight: number;
  order_index: number;
};

type TemplateDraft = {
  id?: number;
  name: string;
  description: string;
  ai_evaluation_locked: boolean;
  categories: TemplateCategory[];
  criteria: CriterionDraft[];
};

type Score = {
  id: number;
  criterion_id: number;
  score: number;
  source: string;
  rationale: string;
  file_ids: number[];
  updated_at: string;
};

type Candidate = {
  id: number;
  template_id: number;
  name: string;
  document_id: string;
  evaluator: string;
  comments: string;
  files: { id: number; original_name: string; mime_type: string; size_bytes: number }[];
  scores: Score[];
};

type SummaryCandidate = {
  id: number;
  name: string;
  document_id: string;
  global_score: number;
  recommendation: string;
  categories: Record<string, number>;
};

type AISettings = {
  gemini_api_key_configured: boolean;
  gemini_api_key_masked: string;
  gemini_model: string;
};

type User = {
  id: number;
  username: string;
  is_admin: boolean;
  can_view_all: boolean;
  is_active: boolean;
};

function api<T>(path: string, init?: RequestInit): Promise<T> {
  const token = localStorage.getItem("valcv_token");
  return fetch(`${API_URL}${path}`, {
    ...init,
    headers:
      init?.body instanceof FormData
        ? { ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init.headers }
        : { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}), ...init?.headers },
  }).then(async (response) => {
    if (!response.ok) {
      const error = await response.json().catch(() => ({ detail: response.statusText }));
      throw new Error(error.detail ?? "Error de API");
    }
    return response.json();
  });
}

function scoreMap(candidate?: Candidate) {
  return new Map((candidate?.scores ?? []).map((score) => [score.criterion_id, score]));
}

function blankCriterion(orderIndex = 0): CriterionDraft {
  return {
    code: "",
    category: "",
    aspect: "",
    category_weight: 0,
    within_category_weight: 0,
    global_weight: 0,
    scale: "0 a 5",
    notes: "",
    is_critical: false,
    evaluation_mode: "manual",
    order_index: orderIndex,
  };
}

function blankCategory(orderIndex = 0): TemplateCategory {
  return { name: "", weight: 0, order_index: orderIndex };
}

function categoriesFromTemplate(template: Template): TemplateCategory[] {
  if (template.categories?.length) {
    return template.categories.map((category, index) => ({ ...category, order_index: index }));
  }
  const rows = new Map<string, number>();
  template.criteria.forEach((criterion) => {
    if (!rows.has(criterion.category)) rows.set(criterion.category, criterion.category_weight);
  });
  return Array.from(rows, ([name, weight], index) => ({ name, weight, order_index: index }));
}

function toTemplateDraft(template: Template, duplicate = false): TemplateDraft {
  return {
    id: duplicate ? undefined : template.id,
    name: duplicate ? `Copia de ${template.name}` : template.name,
    description: template.description,
    ai_evaluation_locked: template.ai_evaluation_locked ?? true,
    categories: categoriesFromTemplate(template),
    criteria: template.criteria.map((criterion, index) => ({
      ...criterion,
      id: duplicate ? undefined : criterion.id,
      order_index: index,
    })),
  };
}

function normalizeWeightsEvenly(draft: TemplateDraft): TemplateDraft {
  const categories = draft.categories.map((category) => ({
    ...category,
    weight: draft.categories.length ? 1 / draft.categories.length : 0,
  }));
  const criteriaByCategory = new Map<string, number>();
  draft.criteria.forEach((criterion) => {
    if (criterion.is_critical) return;
    criteriaByCategory.set(criterion.category, (criteriaByCategory.get(criterion.category) ?? 0) + 1);
  });
  const criteria = draft.criteria.map((criterion) => {
    const siblingCount = criteriaByCategory.get(criterion.category) ?? 0;
    return {
      ...criterion,
      within_category_weight: criterion.is_critical ? 0 : siblingCount ? 1 / siblingCount : 0,
    };
  });
  return { ...draft, categories, criteria };
}

function templateWeightIssues(draft: TemplateDraft) {
  const issues: string[] = [];
  const categoryTotal = draft.categories.reduce((total, category) => total + (Number(category.weight) || 0), 0);
  if (draft.categories.length && !percentStatus(categoryTotal).ok) issues.push("las categorías no suman 100%");
  draft.categories.forEach((category) => {
    const childCriteria = draft.criteria.filter((criterion) => criterion.category === category.name && !criterion.is_critical);
    if (!childCriteria.length) return;
    const childTotal = childCriteria.reduce((total, criterion) => total + (Number(criterion.within_category_weight) || 0), 0);
    if (!percentStatus(childTotal).ok) issues.push(`los criterios de "${category.name || "Sin nombre"}" no suman 100%`);
  });
  return issues;
}

function StarRating({ value, onChange, disabled = false, title }: { value: number; onChange: (value: number) => void; disabled?: boolean; title?: string }) {
  const boundedValue = Math.max(0, Math.min(5, Number(value) || 0));

  function selectStar(event: React.MouseEvent<HTMLButtonElement>, index: number) {
    if (disabled) return;
    const rect = event.currentTarget.getBoundingClientRect();
    const position = event.clientX - rect.left;
    if (index === 0 && position <= rect.width * 0.15) {
      onChange(0);
      return;
    }
    const isHalf = position < rect.width / 2;
    onChange(index + (isHalf ? 0.5 : 1));
  }

  return (
    <div className="flex min-w-[178px] flex-nowrap items-center gap-1" aria-label={`Puntuación ${boundedValue} de 5`} title={title}>
      {Array.from({ length: 5 }, (_, index) => {
        const fillPercent = Math.max(0, Math.min(1, boundedValue - index)) * 100;
        return (
          <button
            className="relative grid size-7 cursor-pointer place-items-center rounded-md text-[#c7d1d2] outline-none transition hover:bg-[#eef6f5] focus:ring-2 focus:ring-brand/20 disabled:cursor-not-allowed disabled:opacity-70 disabled:hover:bg-transparent"
            key={index}
            type="button"
            disabled={disabled}
            onClick={(event) => selectStar(event, index)}
            title={title ?? `${index + 0.5} o ${index + 1} puntos`}
          >
            <Star className="size-6" strokeWidth={1.8} />
            <span className="absolute inset-0 overflow-hidden text-[#db6400]" style={{ width: `${fillPercent}%` }} aria-hidden="true">
              <Star className="m-0.5 size-6 fill-current" strokeWidth={1.8} />
            </span>
          </button>
        );
      })}
      <span className="ml-1 min-w-8 text-sm font-semibold text-accent">{boundedValue.toFixed(1)}</span>
    </div>
  );
}

function ModeToggle({ value, onChange }: { value: Mode; onChange: (value: Mode) => void }) {
  const active = value === "automatic";
  return (
    <button
      className={`flex min-h-8 w-14 cursor-pointer items-center rounded-full border p-1 text-[11px] font-bold transition ${
        active ? "border-brand bg-brand text-white" : "border-line bg-[#eef6f5] text-[#486366]"
      }`}
      type="button"
      onClick={() => onChange(active ? "manual" : "automatic")}
      title={active ? "Evaluación con AI" : "Evaluación manual"}
    >
      <span className={`grid size-6 place-items-center rounded-full bg-white shadow-sm transition ${active ? "translate-x-5 text-brand" : "translate-x-0 text-[#8aa0a1]"}`}>
        AI
      </span>
    </button>
  );
}

function CriticalToggle({ value, onChange }: { value: boolean; onChange: (value: boolean) => void }) {
  return (
    <button
      className={`flex min-h-8 w-full cursor-pointer items-center justify-center rounded-full border px-2 text-[11px] font-bold transition ${
        value ? "border-[#9a3412] bg-[#fff7ed] text-[#9a3412]" : "border-line bg-[#eef6f5] text-[#667579]"
      }`}
      type="button"
      onClick={() => onChange(!value)}
      title={value ? "Criterio crítico activado" : "Marcar como criterio crítico"}
    >
      {value ? "Crítico" : "Normal"}
    </button>
  );
}

function PassFailRating({
  value,
  onChange,
  disabled = false,
  title,
}: {
  value: number;
  onChange: (value: number) => void;
  disabled?: boolean;
  title?: string;
}) {
  const passed = value >= 5;
  return (
    <div className="grid min-w-[178px] grid-cols-2 gap-1" title={title}>
      <button
        className={`min-h-8 rounded-md border px-2 text-xs font-bold ${
          passed ? "border-brand bg-[#e6f1ef] text-brand" : "border-line bg-white text-[#486366]"
        } disabled:cursor-not-allowed disabled:opacity-70`}
        type="button"
        disabled={disabled}
        onClick={() => onChange(5)}
      >
        Cumple
      </button>
      <button
        className={`min-h-8 rounded-md border px-2 text-xs font-bold ${
          !passed ? "border-[#9a3412] bg-[#fff7ed] text-[#9a3412]" : "border-line bg-white text-[#486366]"
        } disabled:cursor-not-allowed disabled:opacity-70`}
        type="button"
        disabled={disabled}
        onClick={() => onChange(0)}
      >
        No cumple
      </button>
    </div>
  );
}

function evidenceValue(criterion: Criterion, current?: Score, draftRationales?: Record<number, string>) {
  if (draftRationales && Object.prototype.hasOwnProperty.call(draftRationales, criterion.id)) {
    return draftRationales[criterion.id];
  }
  if (current?.rationale) {
    return current.rationale;
  }
  return "";
}

function toggleFileReference(currentIds: number[], fileId: number) {
  return currentIds.includes(fileId)
    ? currentIds.filter((currentId) => currentId !== fileId)
    : [...currentIds, fileId];
}

function candidateFileUrl(candidateId: number, fileId: number) {
  const token = localStorage.getItem("valcv_token") ?? "";
  return `${API_URL}/candidates/${candidateId}/files/${fileId}/view?token=${encodeURIComponent(token)}`;
}

function App() {
  const [token, setToken] = React.useState(() => localStorage.getItem("valcv_token") ?? "");
  const [user, setUser] = React.useState<User | null>(null);
  const [loginForm, setLoginForm] = React.useState({ username: "admin", password: "" });
  const [users, setUsers] = React.useState<User[]>([]);
  const [userForm, setUserForm] = React.useState({ username: "", password: "", is_admin: false, can_view_all: true });
  const [templates, setTemplates] = React.useState<Template[]>([]);
  const [candidates, setCandidates] = React.useState<Candidate[]>([]);
  const [summary, setSummary] = React.useState<SummaryCandidate[]>([]);
  const [selectedTemplateId, setSelectedTemplateId] = React.useState<number | null>(null);
  const [selectedCandidateId, setSelectedCandidateId] = React.useState<number | null>(null);
  const [candidateForm, setCandidateForm] = React.useState({ name: "", document_id: "", evaluator: "", comments: "" });
  const [draftScores, setDraftScores] = React.useState<Record<number, number>>({});
  const [draftRationales, setDraftRationales] = React.useState<Record<number, string>>({});
  const [draftFileIds, setDraftFileIds] = React.useState<Record<number, number[]>>({});
  const [evaluationDirty, setEvaluationDirty] = React.useState(false);
  const [autosaveState, setAutosaveState] = React.useState<"idle" | "saving" | "saved" | "error">("idle");
  const [aiSettings, setAiSettings] = React.useState<AISettings | null>(null);
  const [aiModels, setAiModels] = React.useState<string[]>([]);
  const [settingsOpen, setSettingsOpen] = React.useState(false);
  const [settingsForm, setSettingsForm] = React.useState({ gemini_api_key: "", gemini_model: "gemini-3.1-flash-lite" });
  const [templateEditorOpen, setTemplateEditorOpen] = React.useState(false);
  const [templateDraft, setTemplateDraft] = React.useState<TemplateDraft>({
    name: "",
    description: "",
    ai_evaluation_locked: true,
    categories: [blankCategory()],
    criteria: [],
  });
  const [notice, setNotice] = React.useState("Listo");
  const [busy, setBusy] = React.useState(false);

  const selectedTemplate = templates.find((template) => template.id === selectedTemplateId) ?? templates[0];
  const selectedCandidate = candidates.find((candidate) => candidate.id === selectedCandidateId) ?? candidates[0];
  const selectedScores = scoreMap(selectedCandidate);
  const selectedScoreSignature = selectedCandidate?.scores.map((score) => `${score.criterion_id}:${score.score}:${score.updated_at}:${score.file_ids.join(",")}`).join("|") ?? "";
  const categories = Array.from(new Set(selectedTemplate?.criteria.map((criterion) => criterion.category) ?? []));
  const criteriaGroups = categories.map((category) => ({
    category,
    criteria: selectedTemplate?.criteria.filter((criterion) => criterion.category === category) ?? [],
  }));

  async function load() {
    const [templateRows, candidateRows, summaryRows, settingsRows, modelRows] = await Promise.all([
      api<Template[]>("/templates"),
      api<Candidate[]>("/candidates"),
      api<{ candidates: SummaryCandidate[] }>("/summary"),
      api<AISettings>("/settings/ai"),
      api<string[]>("/settings/ai/models"),
    ]);
    setTemplates(templateRows);
    setCandidates(candidateRows);
    setSummary(summaryRows.candidates);
    setAiSettings(settingsRows);
    setAiModels(modelRows);
    setSettingsForm((current) => ({ ...current, gemini_model: settingsRows.gemini_model }));
    setSelectedTemplateId((current) => current ?? templateRows[0]?.id ?? null);
    setSelectedCandidateId((current) => current ?? candidateRows[0]?.id ?? null);
  }

  React.useEffect(() => {
    if (!token) return;
    api<User>("/auth/me")
      .then((currentUser) => {
        setUser(currentUser);
        return load();
      })
      .catch((error) => {
        localStorage.removeItem("valcv_token");
        setToken("");
        setUser(null);
        setNotice(error.message);
      });
  }, [token]);

  React.useEffect(() => {
    if (settingsOpen && user?.is_admin) {
      api<User[]>("/users").then(setUsers).catch((error) => setNotice(error.message));
    }
  }, [settingsOpen, user?.is_admin]);

  React.useEffect(() => {
    if (selectedCandidate) {
      setDraftScores(Object.fromEntries(selectedCandidate.scores.map((score) => [score.criterion_id, score.score])));
      setDraftRationales(Object.fromEntries(selectedCandidate.scores.map((score) => [score.criterion_id, score.rationale])));
      setDraftFileIds(Object.fromEntries(selectedCandidate.scores.map((score) => [score.criterion_id, score.file_ids])));
    } else {
      setDraftScores({});
      setDraftRationales({});
      setDraftFileIds({});
    }
    setEvaluationDirty(false);
    setAutosaveState("idle");
  }, [selectedCandidate?.id, selectedScoreSignature]);

  React.useEffect(() => {
    if (!evaluationDirty || !selectedCandidate || !selectedTemplate) return;
    setAutosaveState("saving");
    const timeout = window.setTimeout(async () => {
      try {
        const payload = selectedTemplate.criteria.map((criterion) => ({
          criterion_id: criterion.id,
          score: Number(draftScores[criterion.id] ?? selectedScores.get(criterion.id)?.score ?? 0),
          rationale: draftRationales[criterion.id] ?? selectedScores.get(criterion.id)?.rationale ?? "",
          file_ids: draftFileIds[criterion.id] ?? selectedScores.get(criterion.id)?.file_ids ?? [],
        }));
        await api(`/candidates/${selectedCandidate.id}/scores`, { method: "POST", body: JSON.stringify(payload) });
        setEvaluationDirty(false);
        setAutosaveState("saved");
        setNotice("Evaluación guardada automáticamente");
        await load();
      } catch (error) {
        setAutosaveState("error");
        setNotice(error instanceof Error ? error.message : "No se pudo autoguardar");
      }
    }, 700);
    return () => window.clearTimeout(timeout);
  }, [evaluationDirty, draftScores, draftRationales, draftFileIds, selectedCandidate?.id, selectedTemplate?.id]);

  async function createCandidate(event: React.FormEvent) {
    event.preventDefault();
    if (!selectedTemplate) return;
    setBusy(true);
    try {
      const created = await api<Candidate>("/candidates", {
        method: "POST",
        body: JSON.stringify({ ...candidateForm, template_id: selectedTemplate.id }),
      });
      setCandidateForm({ name: "", document_id: "", evaluator: "", comments: "" });
      setSelectedCandidateId(created.id);
      setNotice("Candidato creado");
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo crear");
    } finally {
      setBusy(false);
    }
  }

  async function login(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const session = await api<{ token: string; user: User }>("/auth/login", {
        method: "POST",
        body: JSON.stringify(loginForm),
      });
      localStorage.setItem("valcv_token", session.token);
      setToken(session.token);
      setUser(session.user);
      setNotice("Sesión iniciada");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo iniciar sesión");
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    await api("/auth/logout", { method: "POST" }).catch(() => undefined);
    localStorage.removeItem("valcv_token");
    setToken("");
    setUser(null);
    setTemplates([]);
    setCandidates([]);
    setSummary([]);
  }

  async function createUser() {
    setBusy(true);
    try {
      await api<User>("/users", { method: "POST", body: JSON.stringify(userForm) });
      setUserForm({ username: "", password: "", is_admin: false, can_view_all: true });
      setUsers(await api<User[]>("/users"));
      setNotice("Usuario creado");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo crear el usuario");
    } finally {
      setBusy(false);
    }
  }

  async function updateCriterion(criterion: Criterion, changes: Partial<Criterion>) {
    setBusy(true);
    try {
      const updated = await api<Template>(`/criteria/${criterion.id}`, {
        method: "PATCH",
        body: JSON.stringify({ ...criterion, ...changes }),
      });
      setTemplates((current) => current.map((template) => (template.id === updated.id ? updated : template)));
      setNotice("Criterio actualizado");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo guardar");
    } finally {
      setBusy(false);
    }
  }

  function markEvaluationDirty() {
    setEvaluationDirty(true);
    setAutosaveState("saving");
  }

  async function saveScores() {
    if (!selectedCandidate || !selectedTemplate) return;
    setBusy(true);
    try {
      const payload = selectedTemplate.criteria.map((criterion) => ({
        criterion_id: criterion.id,
        score: Number(draftScores[criterion.id] ?? selectedScores.get(criterion.id)?.score ?? 0),
        rationale: draftRationales[criterion.id] ?? selectedScores.get(criterion.id)?.rationale ?? "",
        file_ids: draftFileIds[criterion.id] ?? selectedScores.get(criterion.id)?.file_ids ?? [],
      }));
      await api(`/candidates/${selectedCandidate.id}/scores`, { method: "POST", body: JSON.stringify(payload) });
      setNotice("Evaluación guardada");
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo guardar");
    } finally {
      setBusy(false);
    }
  }

  async function uploadFiles(event: React.ChangeEvent<HTMLInputElement>) {
    if (!selectedCandidate || !event.target.files?.length) return;
    setBusy(true);
    const data = new FormData();
    Array.from(event.target.files).forEach((file) => data.append("files", file));
    try {
      await api(`/candidates/${selectedCandidate.id}/files`, { method: "POST", body: data });
      setNotice("Expediente cargado");
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo cargar");
    } finally {
      event.target.value = "";
      setBusy(false);
    }
  }

  async function deleteFile(fileId: number) {
    if (!selectedCandidate) return;
    setBusy(true);
    try {
      await api<Candidate>(`/candidates/${selectedCandidate.id}/files/${fileId}`, { method: "DELETE" });
      setNotice("Documento eliminado");
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo eliminar el documento");
    } finally {
      setBusy(false);
    }
  }

  async function deleteCandidate(candidateId: number) {
    const candidate = candidates.find((row) => row.id === candidateId);
    if (!window.confirm(`¿Eliminar definitivamente a ${candidate?.name ?? "este candidato"}? Esta acción también borra sus documentos y evaluación.`)) return;
    setBusy(true);
    try {
      await api<{ ok: boolean }>(`/candidates/${candidateId}`, { method: "DELETE" });
      setSelectedCandidateId((current) => (current === candidateId ? null : current));
      setNotice("Candidato eliminado");
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo eliminar el candidato");
    } finally {
      setBusy(false);
    }
  }

  async function resetCandidateEvaluation() {
    if (!selectedCandidate) return;
    if (!window.confirm(`¿Limpiar la evaluación de ${selectedCandidate.name}? Se borrarán puntos, evidencias, referencias y documentos cargados.`)) return;
    setBusy(true);
    try {
      await api<Candidate>(`/candidates/${selectedCandidate.id}/reset`, { method: "POST" });
      setDraftScores({});
      setDraftRationales({});
      setDraftFileIds({});
      setNotice("Evaluación limpiada");
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo limpiar la evaluación");
    } finally {
      setBusy(false);
    }
  }

  async function runAi() {
    if (!selectedCandidate) return;
    setBusy(true);
    try {
      await api(`/candidates/${selectedCandidate.id}/evaluate-ai`, { method: "POST" });
      setNotice("Evaluación automática completada");
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo evaluar con IA");
    } finally {
      setBusy(false);
    }
  }

  async function saveAiSettings(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      const payload = {
        gemini_model: settingsForm.gemini_model,
        gemini_api_key: settingsForm.gemini_api_key.trim() || null,
      };
      const saved = await api<AISettings>("/settings/ai", { method: "PUT", body: JSON.stringify(payload) });
      setAiSettings(saved);
      setSettingsForm({ gemini_api_key: "", gemini_model: saved.gemini_model });
      setSettingsOpen(false);
      setNotice("Configuración de IA guardada");
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo guardar la configuración");
    } finally {
      setBusy(false);
    }
  }

  function openTemplateEditor(action: "new" | "edit" | "duplicate") {
    if (action === "new" || !selectedTemplate) {
      setTemplateDraft({ name: "", description: "", ai_evaluation_locked: true, categories: [blankCategory()], criteria: [] });
    } else {
      setTemplateDraft(toTemplateDraft(selectedTemplate, action === "duplicate"));
    }
    setTemplateEditorOpen(true);
  }

  function updateTemplateCriterion(index: number, changes: Partial<CriterionDraft>) {
    setTemplateDraft((current) => ({
      ...current,
      criteria: current.criteria.map((criterion, criterionIndex) =>
        criterionIndex === index ? { ...criterion, ...changes } : criterion
      ),
    }));
  }

  function updateTemplateCategory(index: number, changes: Partial<TemplateCategory>) {
    setTemplateDraft((current) => {
      const previous = current.categories[index];
      const nextCategories = current.categories.map((category, categoryIndex) =>
        categoryIndex === index ? { ...category, ...changes } : category
      );
      const nextCriteria = changes.name && previous?.name
        ? current.criteria.map((criterion) =>
            criterion.category === previous.name ? { ...criterion, category: changes.name ?? criterion.category } : criterion
          )
        : current.criteria;
      return { ...current, categories: nextCategories, criteria: nextCriteria };
    });
  }

  function updateTemplateCategoryWeight(index: number, rawPercent: string) {
    setTemplateDraft((current) => {
      const requested = fromPercentInput(rawPercent);
      const otherTotal = current.categories.reduce((total, category, categoryIndex) =>
        categoryIndex === index ? total : total + (Number(category.weight) || 0), 0
      );
      const maxWeight = Math.max(0, 1 - otherTotal);
      return {
        ...current,
        categories: current.categories.map((category, categoryIndex) =>
          categoryIndex === index ? { ...category, weight: clampWeight(requested, maxWeight) } : category
        ),
      };
    });
  }

  function updateTemplateCriterionWeight(index: number, rawPercent: string) {
    setTemplateDraft((current) => {
      const criterion = current.criteria[index];
      if (criterion?.is_critical) return current;
      const requested = fromPercentInput(rawPercent);
      const otherTotal = current.criteria.reduce((total, row, rowIndex) =>
        rowIndex === index || row.category !== criterion?.category || row.is_critical ? total : total + (Number(row.within_category_weight) || 0), 0
      );
      const maxWeight = Math.max(0, 1 - otherTotal);
      return {
        ...current,
        criteria: current.criteria.map((row, rowIndex) =>
          rowIndex === index ? { ...row, within_category_weight: clampWeight(requested, maxWeight) } : row
        ),
      };
    });
  }

  function distributeTemplateWeights() {
    setTemplateDraft((current) => normalizeWeightsEvenly(current));
    setNotice("Pesos distribuidos automáticamente.");
  }

  function updateTemplateCritical(index: number, is_critical: boolean) {
    updateTemplateCriterion(index, { is_critical, within_category_weight: is_critical ? 0 : templateDraft.criteria[index]?.within_category_weight ?? 0 });
  }

  function addTemplateCategory() {
    setTemplateDraft((current) => ({
      ...current,
      categories: [...current.categories, blankCategory(current.categories.length)],
    }));
  }

  function removeTemplateCategory(index: number) {
    setTemplateDraft((current) => {
      const removed = current.categories[index];
      const nextCategories = current.categories.filter((_, categoryIndex) => categoryIndex !== index);
      return {
        ...current,
        categories: nextCategories.length ? nextCategories : [blankCategory()],
        criteria: current.criteria.filter((criterion) => criterion.category !== removed?.name),
      };
    });
  }

  function addTemplateCriterion(categoryName: string) {
    if (!categoryName.trim()) {
      setNotice("Primero nombra la categoría.");
      return;
    }
    setTemplateDraft((current) => ({
      ...current,
      criteria: [...current.criteria, { ...blankCriterion(current.criteria.length), category: categoryName }],
    }));
  }

  function removeTemplateCriterion(index: number) {
    setTemplateDraft((current) => ({
      ...current,
      criteria: current.criteria.filter((_, criterionIndex) => criterionIndex !== index),
    }));
  }

  async function saveTemplate(event: React.FormEvent) {
    event.preventDefault();
    let draftToSave = templateDraft;
    let weightIssues = templateWeightIssues(draftToSave);
    if (weightIssues.length) {
      const shouldNormalize = window.confirm(
        `Los pesos todavía no están completos: ${weightIssues.join(", ")}. ¿Quieres distribuirlos equitativamente y guardar como borrador?`
      );
      if (!shouldNormalize) {
        setNotice("Ajusta los pesos hasta que cada grupo sume 100%.");
        return;
      }
      draftToSave = normalizeWeightsEvenly(draftToSave);
      setTemplateDraft(draftToSave);
      weightIssues = templateWeightIssues(draftToSave);
    }
    if (weightIssues.length) {
      setNotice("No se pudo completar automáticamente: revisa categorías y criterios.");
      return;
    }
    const categoryWeights = new Map(draftToSave.categories.map((category) => [category.name.trim(), Number(category.weight) || 0]));
    const payload = {
      name: draftToSave.name.trim(),
      description: draftToSave.description.trim(),
      ai_evaluation_locked: draftToSave.ai_evaluation_locked,
      categories: draftToSave.categories.map((category, index) => ({
        name: category.name.trim(),
        weight: Number(category.weight) || 0,
        order_index: index,
      })),
      criteria: draftToSave.criteria.map((criterion, index) => ({
        code: criterion.code.trim(),
        category: criterion.category.trim(),
        aspect: criterion.aspect.trim(),
        category_weight: categoryWeights.get(criterion.category.trim()) ?? 0,
        within_category_weight: criterion.is_critical ? 0 : Number(criterion.within_category_weight) || 0,
        global_weight: criterion.is_critical ? 0 : (categoryWeights.get(criterion.category.trim()) ?? 0) * (Number(criterion.within_category_weight) || 0),
        scale: criterion.scale.trim() || "0 a 5",
        notes: criterion.notes.trim(),
        is_critical: criterion.is_critical,
        evaluation_mode: criterion.evaluation_mode,
        order_index: index,
      })),
    };
    if (!payload.name || payload.categories.some((category) => !category.name) || payload.criteria.some((criterion) => !criterion.category || !criterion.aspect)) {
      setNotice("Completa nombre, categorías y criterios antes de guardar.");
      return;
    }
    setBusy(true);
    try {
      const saved = await api<Template>(draftToSave.id ? `/templates/${draftToSave.id}` : "/templates", {
        method: draftToSave.id ? "PUT" : "POST",
        body: JSON.stringify(payload),
      });
      setTemplateEditorOpen(false);
      setSelectedTemplateId(saved.id);
      setNotice(draftToSave.id ? "Plantilla actualizada" : "Plantilla creada");
      await load();
    } catch (error) {
      setNotice(error instanceof Error ? error.message : "No se pudo guardar la plantilla");
    } finally {
      setBusy(false);
    }
  }

  const radarData = categories.map((category) => ({
    category: category.replace("Competencias ", "").replace("Formación académica y requisitos básicos", "Formación"),
    ...(summary.reduce((acc, candidate) => ({ ...acc, [candidate.name]: Math.round((candidate.categories[category] ?? 0) * 100) }), {})),
  }));

  const rankingData = summary.map((candidate, index) => ({
    name: candidate.name,
    score: Math.round(candidate.global_score * 100),
    fill: candidateColor(index),
  }));

  if (!token || !user) {
    return (
      <main className="grid min-h-screen place-items-center bg-app p-4 text-ink">
        <form onSubmit={login} className="grid w-full max-w-sm gap-3 rounded-lg border border-line bg-white p-5 shadow-sm">
          <div>
            <p className="mb-1 text-xs font-extrabold uppercase text-accent">VALCV</p>
            <h1 className="text-2xl font-bold">Iniciar sesión</h1>
          </div>
          <input className={inputClass} placeholder="Usuario" value={loginForm.username} onChange={(event) => setLoginForm({ ...loginForm, username: event.target.value })} required />
          <input className={inputClass} type="password" placeholder="Contraseña" value={loginForm.password} onChange={(event) => setLoginForm({ ...loginForm, password: event.target.value })} required />
          <button className={buttonClass} type="submit" disabled={busy}>Entrar</button>
          <small className={mutedTextClass}>{notice}</small>
        </form>
      </main>
    );
  }

  return (
    <main className="min-h-screen bg-app text-ink">
      <header className="grid items-stretch gap-4 border-b border-line bg-white px-4 py-5 md:flex md:items-center md:justify-between md:px-7">
        <div>
          <p className="mb-1 text-xs font-extrabold uppercase text-accent">VALCV</p>
          <h1 className="text-2xl font-bold leading-tight tracking-normal md:text-4xl">Evaluación de curriculum vitae</h1>
        </div>
        <div className="grid min-w-0 items-center gap-2.5 md:flex md:min-w-[420px]">
          <select className={`${inputClass} md:min-w-60`} value={selectedTemplate?.id ?? ""} onChange={(event) => setSelectedTemplateId(Number(event.target.value))}>
            {templates.map((template) => (
              <option key={template.id} value={template.id}>
                {template.name}
              </option>
            ))}
          </select>
          <button className={buttonClass} onClick={() => openTemplateEditor("edit")} disabled={busy || !selectedTemplate} title="Editar plantilla">
            <FilePenLine size={18} />
          </button>
          <button className={buttonClass} onClick={() => openTemplateEditor("new")} disabled={busy} title="Nueva plantilla">
            <Plus size={18} />
          </button>
          <button className={buttonClass} onClick={() => load()} disabled={busy} title="Actualizar">
            <RefreshCw size={18} />
          </button>
          <button className={buttonClass} onClick={() => setSettingsOpen(true)} disabled={busy} title="Configuración">
            <Settings size={18} />
          </button>
          <button className={`${buttonClass} bg-[#486366]`} onClick={logout} disabled={busy} title="Cerrar sesión">
            <LogOut size={18} />
          </button>
        </div>
      </header>

      <section className="grid gap-2 bg-[#e6f1ef] px-4 py-2.5 text-sm text-[#25464a] md:flex md:items-center md:justify-between md:px-7">
        <span>{notice}</span>
        <span>
          {busy
            ? "Procesando..."
            : `${templates.length} plantilla(s), ${candidates.length} candidato(s), IA ${aiSettings?.gemini_api_key_configured ? aiSettings.gemini_model : "sin configurar"}`}
        </span>
      </section>

      {settingsOpen ? (
        <div className="fixed inset-0 z-20 grid place-items-center bg-black/35 p-4">
          <form onSubmit={saveAiSettings} className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-lg border border-line bg-white p-4 shadow-xl">
            <div className="mb-4 flex items-start justify-between gap-3">
              <div>
                <h2 className="mb-1 flex items-center gap-2 text-base font-semibold">
                  <Settings size={18} /> Configuración de IA
                </h2>
                <p className="text-sm text-muted">
                  {aiSettings?.gemini_api_key_configured
                    ? `API key configurada: ${aiSettings.gemini_api_key_masked}`
                    : "Agrega una API key para habilitar la evaluación automática."}
                </p>
              </div>
              <button className={`${buttonClass} bg-[#486366]`} type="button" onClick={() => setSettingsOpen(false)} title="Cerrar">
                <X size={18} />
              </button>
            </div>

            <div className="grid gap-3">
              <label className="grid gap-1.5 text-sm font-semibold">
                Modelo
                <select
                  className={inputClass}
                  value={settingsForm.gemini_model}
                  onChange={(event) => setSettingsForm({ ...settingsForm, gemini_model: event.target.value })}
                >
                  {aiModels.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))}
                  {!aiModels.includes(settingsForm.gemini_model) ? (
                    <option value={settingsForm.gemini_model}>{settingsForm.gemini_model}</option>
                  ) : null}
                </select>
              </label>

              <label className="grid gap-1.5 text-sm font-semibold">
                Gemini API key
                <input
                  className={inputClass}
                  type="password"
                  placeholder={aiSettings?.gemini_api_key_configured ? "Dejar vacío para conservar la actual" : "Pega tu API key"}
                  value={settingsForm.gemini_api_key}
                  onChange={(event) => setSettingsForm({ ...settingsForm, gemini_api_key: event.target.value })}
                />
              </label>

              {user.is_admin ? (
                <section className="mt-2 grid gap-3 border-t border-line pt-3">
                  <h3 className="flex items-center gap-2 text-sm font-semibold">
                    <Users size={17} /> Usuarios
                  </h3>
                  <div className="grid gap-2">
                    {users.map((row) => (
                      <div className="flex flex-wrap items-center justify-between gap-2 rounded-md bg-[#f8fbfa] px-2 py-1.5 text-sm" key={row.id}>
                        <strong>{row.username}</strong>
                        <span className={mutedTextClass}>{row.is_admin ? "Administrador" : "Usuario"} · {row.can_view_all ? "ve resultados" : "sin resultados"}</span>
                      </div>
                    ))}
                  </div>
                  <div className="grid gap-2 md:grid-cols-[1fr_1fr_auto]">
                    <input className={inputClass} placeholder="Usuario" value={userForm.username} onChange={(event) => setUserForm({ ...userForm, username: event.target.value })} />
                    <input className={inputClass} type="password" placeholder="Contraseña" value={userForm.password} onChange={(event) => setUserForm({ ...userForm, password: event.target.value })} />
                    <button className={buttonClass} type="button" onClick={createUser} disabled={busy}>
                      <Plus size={18} /> Usuario
                    </button>
                  </div>
                  <div className="flex flex-wrap gap-3 text-sm">
                    <label className="inline-flex items-center gap-2">
                      <input type="checkbox" checked={userForm.is_admin} onChange={(event) => setUserForm({ ...userForm, is_admin: event.target.checked })} />
                      Admin
                    </label>
                    <label className="inline-flex items-center gap-2">
                      <input type="checkbox" checked={userForm.can_view_all} onChange={(event) => setUserForm({ ...userForm, can_view_all: event.target.checked })} />
                      Ver todos los resultados
                    </label>
                  </div>
                </section>
              ) : null}

              <div className="flex flex-wrap justify-end gap-2 pt-2">
                <button className={`${buttonClass} bg-[#486366]`} type="button" onClick={() => setSettingsOpen(false)}>
                  Cancelar
                </button>
                <button className={buttonClass} type="submit" disabled={busy}>
                  <Save size={18} /> Guardar
                </button>
              </div>
            </div>
          </form>
        </div>
      ) : null}

      {templateEditorOpen ? (
        <div className="fixed inset-0 z-20 grid place-items-center bg-black/35 p-3">
          <form onSubmit={saveTemplate} className="grid max-h-[92vh] w-full max-w-6xl grid-rows-[auto_1fr_auto] rounded-lg border border-line bg-white shadow-xl">
            <div className="flex flex-wrap items-start justify-between gap-3 border-b border-line p-4">
              <div>
                <h2 className="mb-1 flex items-center gap-2 text-base font-semibold">
                  <FilePenLine size={18} /> {templateDraft.id ? "Editar plantilla" : "Nueva plantilla"}
                </h2>
                <p className="text-sm text-muted">Define criterios, pesos y si cada evaluación será manual o por IA.</p>
              </div>
              <div className="flex flex-wrap gap-2">
                {selectedTemplate ? (
                  <button className={`${buttonClass} bg-[#486366]`} type="button" onClick={() => openTemplateEditor("duplicate")} title="Duplicar plantilla actual">
                    <Copy size={18} /> Duplicar
                  </button>
                ) : null}
                <button className={`${buttonClass} bg-[#486366]`} type="button" onClick={() => setTemplateEditorOpen(false)} title="Cerrar">
                  <X size={18} />
                </button>
              </div>
            </div>

            <div className="min-h-0 overflow-y-auto p-4">
              <div className="mb-4 grid gap-3 md:grid-cols-[minmax(220px,0.8fr)_minmax(260px,1.2fr)]">
                <label className="grid gap-1.5 text-sm font-semibold">
                  Nombre de la plantilla
                  <input
                    className={inputClass}
                    value={templateDraft.name}
                    onChange={(event) => setTemplateDraft({ ...templateDraft, name: event.target.value })}
                    placeholder="Ej. Gerente de Normas Eléctricas"
                    required
                  />
                </label>
                <label className="grid gap-1.5 text-sm font-semibold">
                  Descripción
                  <input
                    className={inputClass}
                    value={templateDraft.description}
                    onChange={(event) => setTemplateDraft({ ...templateDraft, description: event.target.value })}
                    placeholder="Uso, alcance o notas generales"
                  />
                </label>
              </div>

              <label className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-line bg-[#f8fbfa] px-3 py-2 text-sm font-semibold text-[#25464a]">
                <input
                  className="size-4 accent-[#16697a]"
                  type="checkbox"
                  checked={templateDraft.ai_evaluation_locked}
                  onChange={(event) => setTemplateDraft({ ...templateDraft, ai_evaluation_locked: event.target.checked })}
                />
                Bloquear edición manual de puntuación, evidencia y documentos en criterios AI
              </label>

              <section className="grid gap-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div>
                    <h3 className="text-sm font-semibold text-ink">Categorías y criterios</h3>
                    <span className={`mt-1 inline-flex rounded-full px-2 py-1 text-xs font-semibold ${percentStatus(templateDraft.categories.reduce((total, category) => total + (Number(category.weight) || 0), 0)).className}`}>
                      Categorías: {percentStatus(templateDraft.categories.reduce((total, category) => total + (Number(category.weight) || 0), 0)).text}
                    </span>
                  </div>
                  <div className="flex flex-wrap gap-2">
                    <button className={`${buttonClass} bg-[#486366]`} type="button" onClick={distributeTemplateWeights}>
                      Repartir pesos
                    </button>
                    <button className={`${buttonClass} bg-[#486366]`} type="button" onClick={addTemplateCategory}>
                      <Plus size={18} /> Categoría
                    </button>
                  </div>
                </div>
                {templateDraft.categories.map((category, categoryIndex) => {
                  const childCriteria = templateDraft.criteria
                    .map((criterion, criterionIndex) => ({ criterion, criterionIndex }))
                    .filter((row) => row.criterion.category === category.name);
                  const weightedChildCriteria = childCriteria.filter((row) => !row.criterion.is_critical);
                  const childTotal = weightedChildCriteria.reduce((total, row) => total + (Number(row.criterion.within_category_weight) || 0), 0);
                  const childStatus = weightedChildCriteria.length ? percentStatus(childTotal) : null;
                  const categoryOtherTotal = templateDraft.categories.reduce((total, row, index) =>
                    index === categoryIndex ? total : total + (Number(row.weight) || 0), 0
                  );
                  const categoryAvailable = Math.max(0, 1 - categoryOtherTotal - (Number(category.weight) || 0));
                  return (
                    <div className="overflow-hidden rounded-lg border border-[#b9d0cf] bg-white shadow-sm" key={`${category.id ?? "cat"}-${categoryIndex}`}>
                      <div className="grid gap-2 border-b border-[#cfe0df] bg-[#e6f1ef] p-3 md:grid-cols-[minmax(0,1fr)_120px_auto_auto] md:items-center">
                        <label className="grid gap-1">
                          <span className="text-[11px] font-extrabold uppercase tracking-normal text-brand">Categoría {categoryIndex + 1}</span>
                          <input className={inputClass} placeholder="Nombre de categoría" value={category.name} onChange={(event) => updateTemplateCategory(categoryIndex, { name: event.target.value })} />
                        </label>
                        <label className="relative">
                          <span className="mb-1 block text-[11px] font-extrabold uppercase tracking-normal text-brand">Peso</span>
                          <input
                            className={`${inputClass} pr-7`}
                            type="number"
                            step="1"
                            min="0"
                            max="100"
                            placeholder="Peso"
                            value={toPercentInput(category.weight)}
                            onWheel={ignoreNumberWheel}
                            onChange={(event) => updateTemplateCategoryWeight(categoryIndex, event.target.value)}
                          />
                          <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-sm font-semibold text-muted">%</span>
                          <small className="mt-1 block text-[11px] font-semibold text-[#486366]">Libre {toPercentInput(categoryAvailable)}%</small>
                        </label>
                        <button className={`${buttonClass} bg-[#486366]`} type="button" onClick={() => addTemplateCriterion(category.name)}>
                          <Plus size={18} /> Criterio
                        </button>
                        <button className={`${buttonClass} min-h-9 bg-[#9a3412] px-2`} type="button" onClick={() => removeTemplateCategory(categoryIndex)} title="Eliminar categoría">
                          <Trash2 size={16} />
                        </button>
                      </div>

                      <div className="grid gap-2 bg-[#fbfdfc] p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <span className="text-xs font-bold text-[#486366]">Criterios ponderados de esta categoría</span>
                          {childStatus ? (
                            <span className={`rounded-full px-2 py-1 text-xs font-semibold ${childStatus.className}`}>
                              {childStatus.text}
                            </span>
                          ) : null}
                        </div>
                        {childCriteria.length ? childCriteria.map(({ criterion, criterionIndex }) => (
                          <div className="rounded-md border border-[#e5eeee] border-l-4 border-l-[#db6400] bg-white p-2.5 shadow-[0_1px_0_rgba(22,105,122,0.05)]" key={`${criterion.id ?? "new"}-${criterionIndex}`}>
                            <div className="grid gap-2 md:grid-cols-[minmax(260px,1fr)_120px_72px_64px_36px] md:items-center">
                              <input className={inputClass} placeholder={`Criterio ${criterionIndex + 1}`} value={criterion.aspect} onChange={(event) => updateTemplateCriterion(criterionIndex, { aspect: event.target.value })} required />
                              {criterion.is_critical ? (
                                <div className="rounded-md border border-[#f0d7c5] bg-[#fff7ed] px-2.5 py-2 text-xs font-semibold text-[#9a3412]">
                                  Sin peso
                                </div>
                              ) : (
                                <label className="relative">
                                  <input
                                    className={`${inputClass} pr-7`}
                                    type="number"
                                    step="1"
                                    min="0"
                                    max="100"
                                    placeholder="Peso"
                                    value={toPercentInput(criterion.within_category_weight)}
                                    onWheel={ignoreNumberWheel}
                                    onChange={(event) => updateTemplateCriterionWeight(criterionIndex, event.target.value)}
                                  />
                                  <span className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-sm font-semibold text-muted">%</span>
                                  <small className="mt-1 block text-[11px] font-semibold text-[#486366]">
                                    Libre {toPercentInput(Math.max(0, 1 - childCriteria.reduce((total, row) =>
                                      row.criterionIndex === criterionIndex || row.criterion.is_critical ? total : total + (Number(row.criterion.within_category_weight) || 0), 0
                                    ) - (Number(criterion.within_category_weight) || 0)))}%
                                  </small>
                                </label>
                              )}
                              <CriticalToggle value={criterion.is_critical} onChange={(is_critical) => updateTemplateCritical(criterionIndex, is_critical)} />
                              <ModeToggle value={criterion.evaluation_mode} onChange={(evaluation_mode) => updateTemplateCriterion(criterionIndex, { evaluation_mode })} />
                              <button className={`${buttonClass} min-h-9 bg-[#9a3412] px-2`} type="button" onClick={() => removeTemplateCriterion(criterionIndex)} title="Eliminar criterio">
                                <Trash2 size={16} />
                              </button>
                            </div>
                            <textarea
                              className={`${inputClass} mt-2 min-h-16 resize-y`}
                              placeholder="Notas / evidencia esperada"
                              value={criterion.notes}
                              onChange={(event) => updateTemplateCriterion(criterionIndex, { notes: event.target.value })}
                            />
                          </div>
                        )) : (
                          <div className="rounded-md border border-dashed border-[#ccd8d9] bg-white px-3 py-4 text-sm text-muted">
                            Agrega criterios dentro de esta categoría.
                          </div>
                        )}
                      </div>
                    </div>
                  );
                })}
              </section>
            </div>

            <div className="flex flex-wrap justify-end gap-2 border-t border-line p-4">
              <div className="flex flex-wrap gap-2">
                <button className={`${buttonClass} bg-[#486366]`} type="button" onClick={() => setTemplateEditorOpen(false)}>
                  Cancelar
                </button>
                <button className={buttonClass} type="submit" disabled={busy}>
                  <Save size={18} /> Guardar plantilla
                </button>
              </div>
            </div>
          </form>
        </div>
      ) : null}

      <div className="grid gap-4 p-3 lg:grid-cols-[320px_minmax(0,1fr)] lg:p-4.5">
        <aside className="grid content-start gap-4 lg:block lg:space-y-4 xl:grid xl:grid-cols-1">
          <form onSubmit={createCandidate} className={`${panelClass} grid gap-2.5`}>
            <h2 className={headingClass}><UserRoundPlus size={18} /> Nuevo candidato</h2>
            <input className={inputClass} placeholder="Nombre" value={candidateForm.name} onChange={(e) => setCandidateForm({ ...candidateForm, name: e.target.value })} required />
            <input className={inputClass} placeholder="Cédula/ID" value={candidateForm.document_id} onChange={(e) => setCandidateForm({ ...candidateForm, document_id: e.target.value })} />
            <input className={inputClass} placeholder="Evaluador" value={candidateForm.evaluator} onChange={(e) => setCandidateForm({ ...candidateForm, evaluator: e.target.value })} />
            <textarea className={`${inputClass} min-h-20 resize-y`} placeholder="Comentarios" value={candidateForm.comments} onChange={(e) => setCandidateForm({ ...candidateForm, comments: e.target.value })} />
            <button className={buttonClass} type="submit" disabled={busy || !selectedTemplate}>
              <Plus size={18} /> Crear
            </button>
          </form>

          <div className={`${panelClass} grid gap-2.5`}>
            <h2 className={headingClass}><Gauge size={18} /> Candidatos</h2>
            {candidates.map((candidate) => (
              <div
                className={`grid grid-cols-[minmax(0,1fr)_36px] items-stretch gap-1 rounded-md border p-1 ${
                  candidate.id === selectedCandidate?.id ? "border-brand bg-brand" : "border-line bg-[#f8fbfa]"
                }`}
                key={candidate.id}
              >
                <button
                  className={`grid min-h-9 min-w-0 cursor-pointer justify-stretch rounded-md border-0 bg-transparent p-1.5 text-left ${
                    candidate.id === selectedCandidate?.id ? "text-white" : "text-ink"
                  }`}
                  onClick={() => setSelectedCandidateId(candidate.id)}
                  type="button"
                >
                  <strong className="truncate">{candidate.name}</strong>
                  <span className={candidate.id === selectedCandidate?.id ? "block truncate text-xs leading-snug text-[#dceff0]" : mutedTextClass}>{candidate.document_id || "Sin ID"}</span>
                </button>
                <button
                  className={`grid size-9 cursor-pointer place-items-center rounded-md ${
                    candidate.id === selectedCandidate?.id ? "bg-white/15 text-white hover:bg-white/25" : "bg-[#eef6f5] text-[#9a3412] hover:bg-[#e3efed]"
                  }`}
                  type="button"
                  onClick={() => deleteCandidate(candidate.id)}
                  disabled={busy}
                  title="Eliminar candidato"
                >
                  <Trash2 size={16} />
                </button>
              </div>
            ))}
          </div>
        </aside>

        <section className="grid content-start gap-4">
          <div className="grid gap-4 xl:grid-cols-2">
            <div className={`${panelClass} min-h-[330px]`}>
              <h2 className={headingClass}><BarChart3 size={18} /> Ranking global</h2>
              <ResponsiveContainer width="100%" height={260}>
                <RadialBarChart innerRadius="28%" outerRadius="95%" data={rankingData} startAngle={90} endAngle={-270}>
                  <RadialBar background dataKey="score">
                    {rankingData.map((entry) => <Cell key={entry.name} fill={entry.fill} />)}
                  </RadialBar>
                  <Tooltip formatter={(value) => `${value}%`} />
                  <Legend iconSize={10} />
                </RadialBarChart>
              </ResponsiveContainer>
            </div>
            <div className={`${panelClass} min-h-[330px]`}>
              <h2 className={headingClass}><SlidersHorizontal size={18} /> Polígono por categoría</h2>
              <ResponsiveContainer width="100%" height={260}>
                <RadarChart data={radarData}>
                  <PolarGrid />
                  <PolarAngleAxis dataKey="category" tick={{ fontSize: 11 }} />
                  <PolarRadiusAxis angle={30} domain={[0, 100]} tick={{ fontSize: 10 }} />
                  {summary.slice(0, 5).map((candidate, index) => (
                    <Radar key={candidate.id} dataKey={candidate.name} stroke={candidateColor(index)} fill={candidateColor(index)} fillOpacity={0.14} />
                  ))}
                  <Legend />
                  <Tooltip />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          </div>

          <div className={panelClass}>
            <h2 className={headingClass}><BarChart3 size={18} /> Comparación compacta</h2>
            <ResponsiveContainer width="100%" height={280}>
              <BarChart data={rankingData}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="name" />
                <YAxis domain={[0, 100]} />
                <Tooltip formatter={(value) => `${value}%`} />
                <Bar dataKey="score" radius={[4, 4, 0, 0]}>
                  {rankingData.map((entry) => <Cell key={entry.name} fill={entry.fill} />)}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div className={panelClass}>
            <h2 className={headingClass}><Bot size={18} /> Expediente y evaluación</h2>
            {selectedCandidate ? (
              <>
                <div className="grid items-stretch gap-2.5 border-b border-[#e5eeee] pb-3.5 md:grid-cols-[minmax(180px,1fr)_auto_auto_auto] md:items-center">
                  <div>
                    <strong>{selectedCandidate.name}</strong>
                    <span className={mutedTextClass}>
                      {selectedCandidate.files.length} archivo(s) cargado(s) · {autosaveState === "saving" ? "guardando..." : autosaveState === "saved" ? "guardado" : autosaveState === "error" ? "error al guardar" : "autoguardado"}
                    </span>
                  </div>
                  <label className={buttonClass} title="Cargar PDF o imagen">
                    <FileUp size={18} />
                    <input className="hidden" type="file" multiple accept="application/pdf,image/png,image/jpeg,image/webp,image/heic,image/heif" onChange={uploadFiles} />
                  </label>
                  <button className={buttonClass} onClick={runAi} disabled={busy || selectedCandidate.files.length === 0} title="Evaluar criterios automáticos con IA">
                    <Bot size={18} /> Evaluar con IA
                  </button>
                  <button className={`${buttonClass} bg-[#9a3412]`} onClick={resetCandidateEvaluation} disabled={busy} title="Limpiar evaluación">
                    <RotateCcw size={18} /> Limpiar
                  </button>
                </div>
                <div className="my-3 flex flex-wrap gap-2">
                  {selectedCandidate.files.map((file) => (
                    <span className="inline-flex items-center gap-1.5 rounded-md bg-[#eef6f5] px-2 py-1.5 text-xs text-[#25464a]" key={file.id}>
                      <a
                        className="inline-flex min-w-0 items-center gap-1.5 rounded text-[#25464a] hover:text-brand"
                        href={candidateFileUrl(selectedCandidate.id, file.id)}
                        target="_blank"
                        rel="noreferrer"
                        title="Abrir documento"
                      >
                        <span className="max-w-56 truncate">{file.original_name}</span>
                        <span className="grid size-5 shrink-0 place-items-center rounded bg-[#d9e8e6] hover:bg-[#c9ddda]">
                          <FileText size={14} />
                        </span>
                      </a>
                      <button
                        className="grid size-5 cursor-pointer place-items-center rounded bg-[#d9e8e6] text-[#25464a] hover:bg-[#c9ddda]"
                        type="button"
                        onClick={() => deleteFile(file.id)}
                        disabled={busy}
                        title="Eliminar documento"
                      >
                        <X size={14} />
                      </button>
                    </span>
                  ))}
                </div>
                <div className="grid gap-4">
                  {criteriaGroups.map((group, groupIndex) => (
                    <section className="overflow-hidden rounded-lg border border-[#b9d0cf] bg-white shadow-sm" key={group.category}>
                      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[#cfe0df] bg-[#dcecea] px-3 py-2.5">
                        <div className="flex min-w-0 items-center gap-2">
                          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-brand text-xs font-extrabold text-white">{groupIndex + 1}</span>
                          <strong className="min-w-0 text-sm leading-tight text-[#25464a]">{group.category}</strong>
                        </div>
                        <span className="rounded-full bg-white px-2 py-1 text-xs font-semibold text-[#486366]">{group.criteria.length} criterio(s)</span>
                      </div>
                      <div className="grid gap-2.5 bg-[#fbfdfc] p-2.5 md:p-3">
                        {group.criteria.map((criterion, criterionIndex) => {
                          const current = selectedScores.get(criterion.id);
                          const referencedFileIds = draftFileIds[criterion.id] ?? current?.file_ids ?? [];
                          const isAutomatic = criterion.evaluation_mode === "automatic";
                          const isAiLocked = isAutomatic && selectedTemplate?.ai_evaluation_locked !== false;
                          const currentScore = draftScores[criterion.id] ?? current?.score ?? 0;
                          return (
                            <article className={`grid gap-2 rounded-md border border-[#e5eeee] border-l-4 bg-white p-2.5 shadow-[0_1px_0_rgba(22,105,122,0.05)] ${criterion.is_critical ? "border-l-[#9a3412]" : "border-l-[#db6400]"}`} key={criterion.id}>
                              <div className="grid gap-2 lg:grid-cols-[minmax(260px,1fr)_210px] lg:items-center">
                                <div className="min-w-0">
                                  <div className="flex min-w-0 flex-wrap items-center gap-1.5">
                                    <span className="shrink-0 rounded bg-[#f4e8de] px-1.5 py-0.5 text-[10px] font-extrabold text-[#9a3412]">{groupIndex + 1}.{criterionIndex + 1}</span>
                                    <strong className="min-w-0 text-sm leading-tight">{criterion.aspect}</strong>
                                    {isAutomatic ? (
                                      <span className="inline-flex min-h-5 shrink-0 items-center rounded-full bg-[#e6f1ef] px-1.5 text-[10px] font-bold leading-none text-brand">
                                        AI
                                      </span>
                                    ) : null}
                                    {criterion.is_critical ? (
                                      <span className="inline-flex min-h-5 shrink-0 items-center rounded-full bg-[#fff7ed] px-1.5 text-[10px] font-bold leading-none text-[#9a3412]" title="Si este criterio no cumple, el score general queda en 0.">
                                        Crítico
                                      </span>
                                    ) : null}
                                  </div>
                                  <small className={mutedTextClass}>
                                    {criterion.is_critical ? "Cumple / no cumple" : `Peso interno ${Math.round(criterion.within_category_weight * 100)}%`}
                                  </small>
                                </div>
                                {criterion.is_critical ? (
                                  <PassFailRating
                                    value={currentScore}
                                    disabled={isAiLocked}
                                    title={isAiLocked ? aiLockedTitle : "Criterio crítico: no cumplirlo deja el score general en 0."}
                                    onChange={(score) => {
                                      setDraftScores({ ...draftScores, [criterion.id]: score });
                                      markEvaluationDirty();
                                    }}
                                  />
                                ) : (
                                  <StarRating
                                    value={currentScore}
                                    disabled={isAiLocked}
                                    title={isAiLocked ? aiLockedTitle : undefined}
                                    onChange={(score) => {
                                      setDraftScores({ ...draftScores, [criterion.id]: score });
                                      markEvaluationDirty();
                                    }}
                                  />
                                )}
                              </div>
                              <textarea
                                className={`${inputClass} min-h-20 resize-y text-sm disabled:cursor-not-allowed disabled:bg-[#eef2f2] disabled:text-[#486366]`}
                                disabled={isAiLocked}
                                title={isAiLocked ? aiLockedTitle : undefined}
                                placeholder={criterion.evaluation_mode === "manual" ? MANUAL_EVIDENCE_NOTE : criterion.notes || "Evidencia, justificación o comentario"}
                                value={evidenceValue(criterion, current, draftRationales)}
                                onChange={(event) => {
                                  setDraftRationales({ ...draftRationales, [criterion.id]: event.target.value });
                                  markEvaluationDirty();
                                }}
                              />
                              {selectedCandidate.files.length ? (
                                <div className="flex flex-wrap gap-1.5">
                                  {selectedCandidate.files.map((file) => (
                                    <span
                                      className={`inline-flex max-w-full items-center gap-1 rounded-md border py-1 pl-2 pr-1 text-xs ${
                                        referencedFileIds.includes(file.id)
                                          ? "border-brand bg-[#e6f1ef] text-brand"
                                          : "border-line bg-white text-[#486366]"
                                      }`}
                                      key={file.id}
                                      title={file.original_name}
                                    >
                                      <label className={`inline-flex min-w-0 items-center gap-1.5 ${isAiLocked ? "cursor-not-allowed" : "cursor-pointer"}`} title={isAiLocked ? aiLockedTitle : file.original_name}>
                                        <input
                                          className="size-3.5 accent-[#16697a]"
                                          type="checkbox"
                                          disabled={isAiLocked}
                                          checked={referencedFileIds.includes(file.id)}
                                          onChange={() => {
                                            setDraftFileIds({
                                              ...draftFileIds,
                                              [criterion.id]: toggleFileReference(referencedFileIds, file.id),
                                            });
                                            markEvaluationDirty();
                                          }}
                                      />
                                        <span className="max-w-44 truncate">{file.original_name}</span>
                                      </label>
                                      <a
                                        className="grid size-5 shrink-0 place-items-center rounded bg-[#eef6f5] text-[#25464a] hover:bg-[#d9e8e6] hover:text-brand"
                                        href={candidateFileUrl(selectedCandidate.id, file.id)}
                                        target="_blank"
                                        rel="noreferrer"
                                        title="Abrir documento"
                                      >
                                        <FileText size={13} />
                                      </a>
                                    </span>
                                  ))}
                                </div>
                              ) : null}
                            </article>
                          );
                        })}
                      </div>
                    </section>
                  ))}
                </div>
              </>
            ) : (
              <p className="m-0 text-muted">Crea un candidato para empezar.</p>
            )}
          </div>

        </section>
      </div>
    </main>
  );
}

createRoot(document.getElementById("root")!).render(<App />);
