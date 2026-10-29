import { useEffect, useRef, useState } from "react";
import { requestJSON } from "../lib/api";
import {
  commissioningBlock,
  commissioningChecks,
  commissioningPayload,
  commissioningSteps,
  type CommissioningInventory,
  type CommissioningJob,
  type JobKind,
} from "../lib/commissioning";
import { Icon } from "./Icon";

export function CommissioningResults({ job }: { job: CommissioningJob }) {
  const checks = commissioningChecks(job.summary);
  return (
    <section className="commission-job" aria-label={`${job.label} result`}>
      <div className="commission-job-heading">
        <strong>{job.label}</strong>
        <span className={`job-status ${job.status}`}>{job.status}</span>
      </div>
      <p className="caption">
        {job.source_id ? `Source: ${job.source_id} · ` : ""}
        {job.twin_id ? `Reference: ${job.twin_id} · ` : ""}
        {new Date(job.created_at_unix * 1000).toLocaleString()}
      </p>
      {job.reason && <p className="commission-error">{job.reason}</p>}
      {checks.length > 0 && (
        <dl className="commission-checks">
          {checks.map((row, i) => (
            <div key={`${row.label}-${i}`}>
              <dt>{row.label}</dt>
              <dd>{row.value.replaceAll("_", " ")}</dd>
            </div>
          ))}
        </dl>
      )}
      {typeof job.summary?.limit === "string" && (
        <p className="caption">{job.summary.limit}</p>
      )}
      {job.status === "completed" && (
        <p className="caption">
          Job completion means the report was produced. Review each check
          separately.
        </p>
      )}
      {job.logs.length > 0 && (
        <details>
          <summary>Progress log ({job.logs.length})</summary>
          <ol className="commission-log">
            {job.logs.map((entry, i) => (
              <li key={i}>{entry.message}</li>
            ))}
          </ol>
        </details>
      )}
      {job.artifacts.length > 0 && (
        <details>
          <summary>Download evidence ({job.artifacts.length})</summary>
          <ul className="commission-artifacts">
            {job.artifacts.map((item) => (
              <li key={item.name}>
                <a href={item.url} download>
                  {item.name}
                </a>
                <small>{(item.bytes / 1024).toFixed(1)} KiB</small>
              </li>
            ))}
          </ul>
        </details>
      )}
      <span className="commission-candidate">
        <Icon name="lock" size={12} /> Evidence only · No active map, pose or
        calibration promotion
      </span>
    </section>
  );
}

export function CommissioningPanel({
  role,
  csrfToken,
  calibration,
  mapRevision,
  hardwareStatus,
  onClose,
}: {
  role: string | undefined;
  csrfToken?: string;
  calibration: string | null | undefined;
  mapRevision?: string;
  hardwareStatus?: string;
  onClose: () => void;
}) {
  const [inventory, setInventory] = useState<CommissioningInventory | null>(
      null,
    ),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false);
  const [kind, setKind] = useState<JobKind>("discovery"),
    [sourceId, setSource] = useState(""),
    [twinId, setTwin] = useState(""),
    [correspondenceId, setCorrespondence] = useState(""),
    [frames, setFrames] = useState(8);
  const [selectedJob, setSelectedJob] = useState<string | null>(null);
  const mounted = useRef(false);
  const refresh = async (signal?: AbortSignal) => {
    const value = await requestJSON<CommissioningInventory>(
      "/api/v2/commissioning",
      { signal },
    );
    if (mounted.current && !signal?.aborted) {
      setInventory(value);
      setError("");
    }
  };
  useEffect(() => {
    mounted.current = true;
    const abort = new AbortController();
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      try {
        await refresh(abort.signal);
      } catch (cause) {
        if (!abort.signal.aborted) setError(String(cause));
      }
      if (!abort.signal.aborted) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => {
      mounted.current = false;
      abort.abort();
      clearTimeout(timer);
    };
  }, []);
  const blocked = commissioningBlock(
    inventory,
    role,
    kind,
    sourceId,
    twinId,
    correspondenceId,
    frames,
  );
  const selected =
    inventory?.jobs.find((job) => job.id === selectedJob) ?? inventory?.jobs[0];
  const source = inventory?.sources.find((item) => item.id === sourceId);
  const correspondence = inventory?.correspondence_files.find(
    (item) => item.id === correspondenceId,
  );
  const mutate = async (url: string, payload?: unknown) => {
    setBusy(true);
    setError("");
    try {
      const job = await requestJSON<CommissioningJob>(url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
        },
        body: JSON.stringify(payload ?? {}),
      });
      if (!mounted.current) return;
      setSelectedJob(job.id);
      await refresh();
    } catch (cause) {
      if (mounted.current) setError(String(cause));
    } finally {
      if (mounted.current) setBusy(false);
    }
  };
  return (
    <section
      className="commission-workspace glass"
      aria-label="Guided commissioning"
    >
      <header className="commission-heading">
        <div>
          <span className="eyebrow">COMMISSION / REVIEW</span>
          <h2>Build confidence from evidence.</h2>
        </div>
        <button
          className="icon-button"
          aria-label="Close commissioning"
          onClick={onClose}
        >
          <Icon name="close" size={18} />
        </button>
      </header>
      <p className="commission-notice">
        Candidate outputs remain separate. This workspace never activates
        calibration, maps or poses. Physical device validation is{" "}
        {hardwareStatus ?? "not reported"}.
      </p>
      <div className="commission-context">
        <span>
          Active map{" "}
          <b className="mono">{mapRevision?.slice(0, 12) ?? "not reported"}</b>
        </span>
        <span>
          Calibration <b className="mono">{calibration ?? "not reported"}</b>
        </span>
      </div>
      <div className="commission-layout">
        <div className="commission-setup">
          <nav className="commission-steps" aria-label="Commissioning stages">
            {commissioningSteps.map((step, i) => (
              <button
                key={step.kind}
                aria-pressed={kind === step.kind}
                className={kind === step.kind ? "active" : ""}
                onClick={() => setKind(step.kind)}
              >
                <span>{String(i + 1).padStart(2, "0")}</span>
                {step.title}
              </button>
            ))}
          </nav>
          <p className="caption">
            {commissioningSteps.find((step) => step.kind === kind)?.description}
          </p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              if (!blocked && !busy)
                void mutate(
                  "/api/v2/commissioning/jobs",
                  commissioningPayload(
                    kind,
                    sourceId,
                    twinId,
                    correspondenceId,
                    frames,
                    crypto.randomUUID(),
                  ),
                );
            }}
          >
            {["diagnostics", "build_twin", "relocalization"].includes(kind) && (
              <label>
                Registered observations
                <select
                  value={sourceId}
                  onChange={(event) => setSource(event.target.value)}
                >
                  <option value="">Choose a source</option>
                  {inventory?.sources.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label} · {item.mode}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {source && (
              <p className="caption">
                {source.mode === "sim"
                  ? "Synthetic observations"
                  : `${source.mode} observations`}{" "}
                ·{" "}
                {source.static_scene
                  ? "Declared static"
                  : "Not declared static"}{" "}
                ·{" "}
                {source.fixed_position
                  ? "Declared fixed-position"
                  : "Not declared fixed-position"}
              </p>
            )}
            {["diagnostics", "coverage", "relocalization"].includes(kind) && (
              <label>
                Reference twin
                <select
                  value={twinId}
                  onChange={(event) => setTwin(event.target.value)}
                >
                  <option value="">Choose a reference</option>
                  {inventory?.twins.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </select>
              </label>
            )}
            {kind === "calibration_candidate" && (
              <>
                <label>
                  Registered 3D/2D targets
                  <select
                    value={correspondenceId}
                    onChange={(event) => setCorrespondence(event.target.value)}
                  >
                    <option value="">Choose a correspondence file</option>
                    {inventory?.correspondence_files.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
                <p className="caption">
                  {correspondence
                    ? `${correspondence.measured_declared ? "Measured provenance declared" : "Synthetic / unmeasured provenance"}; ${correspondence.independent_validation_declared ? "independent validation declared" : "independent validation not declared"}. The archive is checked again by the worker.`
                    : "Configure a trusted correspondence file on the server. Browser-supplied paths are not accepted."}
                </p>
              </>
            )}
            {["diagnostics", "build_twin", "relocalization"].includes(kind) && (
              <label>
                Maximum observations
                <input
                  type="number"
                  min="1"
                  max={inventory?.limits.max_frames ?? 128}
                  step="1"
                  value={frames}
                  onChange={(event) => setFrames(Number(event.target.value))}
                />
              </label>
            )}
            <button
              className="button primary"
              disabled={!!blocked || busy}
              title={blocked ?? undefined}
              type="submit"
            >
              <Icon name="scan" size={15} />
              {busy ? "Submitting…" : "Start bounded job"}
            </button>
            {blocked && (
              <p className="caption" role="note">
                {blocked}
              </p>
            )}
          </form>
          {inventory && (
            <p className="commission-budget">
              One worker · {inventory.limits.max_job_seconds}s time limit ·{" "}
              {inventory.limits.queue_capacity} queued maximum
              <br />
              {(inventory.limits.measured_output_bytes / 1024 ** 2).toFixed(
                1,
              )}{" "}
              /{" "}
              {(inventory.limits.aggregate_output_bytes / 1024 ** 2).toFixed(0)}{" "}
              MiB retained evidence
            </p>
          )}
          <p className="caption">
            Live jobs require exclusive rig ownership. Close active acquisition
            and use the console workflow before physical diagnostics; pausing
            alone does not release sensors.
          </p>
        </div>
        <div className="commission-evidence">
          <div className="commission-evidence-title">
            <h3>Job evidence</h3>
            <span>{inventory?.jobs.length ?? 0} retained in history</span>
          </div>
          {error && (
            <p className="commission-error" role="alert">
              {error}
            </p>
          )}
          {!inventory && !error && (
            <p role="status">Loading configured commissioning resources…</p>
          )}
          {inventory && inventory.jobs.length === 0 && (
            <p className="commission-empty">
              Start with a read-only resource discovery. Completed reports
              appear here with individual check outcomes and downloads.
            </p>
          )}
          {inventory && inventory.jobs.length > 0 && (
            <label>
              Review job
              <select
                value={selected?.id ?? ""}
                onChange={(event) => setSelectedJob(event.target.value)}
              >
                {inventory.jobs.map((job) => (
                  <option key={job.id} value={job.id}>
                    {job.label} · {job.status} · {job.id.slice(0, 8)}
                  </option>
                ))}
              </select>
            </label>
          )}
          {selected && (
            <>
              <CommissioningResults job={selected} />
              {["queued", "running"].includes(selected.status) && (
                <button
                  className="button ghost"
                  disabled={role !== "operator" || busy}
                  onClick={() =>
                    void mutate(
                      `/api/v2/commissioning/jobs/${selected.id}/cancel`,
                    )
                  }
                >
                  Cancel this job
                </button>
              )}
            </>
          )}
        </div>
      </div>
    </section>
  );
}
