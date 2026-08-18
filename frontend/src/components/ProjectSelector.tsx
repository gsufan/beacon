import { createContext, useContext, useEffect, useState } from "react";
import { useLanguage } from "./LanguageSelector";
import { api } from "../lib/api";
import type { Project } from "../lib/types";

interface ProjectContextValue {
  projects: Project[];
  projectId: string | null;
  setProjectId: (id: string) => void;
  reloadProjects: () => void;
}

const ProjectContext = createContext<ProjectContextValue | null>(null);

export function ProjectProvider({ children }: { children: React.ReactNode }) {
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectIdState] = useState<string | null>(
    () => localStorage.getItem("beacon:projectId")
  );

  const reloadProjects = () => {
    api.listProjects().then((list) => {
      setProjects(list);
      if (!projectId && list.length > 0) {
        setProjectId(list[0].id);
      }
    });
  };

  useEffect(() => {
    reloadProjects();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const setProjectId = (id: string) => {
    localStorage.setItem("beacon:projectId", id);
    setProjectIdState(id);
  };

  return (
    <ProjectContext.Provider value={{ projects, projectId, setProjectId, reloadProjects }}>
      {children}
    </ProjectContext.Provider>
  );
}

export function useProject() {
  const ctx = useContext(ProjectContext);
  if (!ctx) throw new Error("useProject debe usarse dentro de ProjectProvider");
  return ctx;
}

export function ProjectSelector() {
  const { projects, projectId, setProjectId } = useProject();
  const { t } = useLanguage();
  return (
    <select
      value={projectId ?? ""}
      onChange={(e) => setProjectId(e.target.value)}
      className="w-full rounded-lg border border-beacon-11 bg-white px-3 py-2 text-sm text-beacon-4 dark:border-beacon-6 dark:bg-beacon-4 dark:text-beacon-11"
    >
      {projects.length === 0 && <option value="">{t("no_projects")}</option>}
      {projects.map((p) => (
        <option key={p.id} value={p.id}>
          {p.name}
        </option>
      ))}
    </select>
  );
}
