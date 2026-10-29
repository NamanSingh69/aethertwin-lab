import type { Bootstrap } from "./types";

export interface SourceOption {
  id: string;
  source: string;
  label: string;
  recording_id?: string;
}

/** Normalize the service's string or object source entries into stable options. */
export function sourceOptions(bootstrap: Bootstrap | null): SourceOption[] {
  return (bootstrap?.available_sources ?? []).map((value, index) =>
    typeof value === "string"
      ? { id: `source-${index}`, source: value, label: value }
      : {
          ...value,
          id: value.id ?? `source-${index}`,
          label: value.label ?? value.source,
        },
  );
}

export function sourceAvailable(bootstrap: Bootstrap | null, target: string) {
  return (bootstrap?.available_sources ?? []).some(
    (s) => (typeof s === "string" ? s : s.source) === target,
  );
}

/** The `select_source` payload for an option; recordings carry their id. */
export function sourcePayload(option: SourceOption) {
  return {
    source: option.source,
    ...(option.recording_id ? { recording_id: option.recording_id } : {}),
  };
}
