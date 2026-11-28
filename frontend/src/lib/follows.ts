// Files the person follows in the current project, loaded once and shared by every source citation.
import { useEffect, useSyncExternalStore } from "react";
import { hub } from "./hub";

let project = "";
let files = new Set<string>();
let loading: Promise<void> | null = null;
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());

function load(projectId: string) {
  if (project === projectId && loading) return;
  project = projectId;
  files = new Set();
  loading = hub.follows(projectId).then(
    (s) => {
      if (project === projectId) {
        files = new Set(s.files.map((f) => f.file_path));
        emit();
      }
    },
    () => {},
  );
}

export function useFollowing(projectId: string | null): { following: (file: string) => boolean; toggle: (file: string) => Promise<void> } {
  useEffect(() => {
    if (projectId) load(projectId);
  }, [projectId]);
  const snapshot = useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => files,
  );
  return {
    following: (file) => snapshot.has(file),
    toggle: async (file) => {
      if (!projectId) return;
      const was = files.has(file);
      await (was ? hub.unfollow(projectId, file) : hub.follow(projectId, file));
      files = new Set(files);
      if (was) files.delete(file);
      else files.add(file);
      emit();
    },
  };
}

// After the followed panel changes the list (unfollow), keep the citations in sync.
export function forgetFollow(file: string) {
  if (!files.has(file)) return;
  files = new Set(files);
  files.delete(file);
  emit();
}
