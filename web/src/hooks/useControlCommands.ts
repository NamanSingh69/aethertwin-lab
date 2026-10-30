import { useCallback, useRef, useState } from "react";
import { sendCommand } from "../lib/api";
import { canControl } from "../lib/status";
import type { Bootstrap } from "../lib/types";
import type { Notice } from "./useStudioSession";

const ACK_REFRESH_MS = 700;

/**
 * Sends revisioned control requests. An accepted request is only queued; the
 * applied/failed outcome arrives later through the control history.
 */
export function useControlCommands({
  bootstrap,
  setBootstrap,
  setNotice,
  refresh,
  expectAcknowledgement,
}: {
  bootstrap: Bootstrap | null;
  setBootstrap: (update: (b: Bootstrap | null) => Bootstrap | null) => void;
  setNotice: (notice: Notice) => void;
  refresh: () => void;
  expectAcknowledgement: (requestId: string) => void;
}) {
  const [busy, setBusy] = useState(false);
  const current = useRef(bootstrap);
  current.current = bootstrap;
  const command = useCallback(
    async (action: string, payload: unknown = {}, revision?: number) => {
      const session = current.current;
      if (!session || !canControl(session, action)) {
        setNotice({
          text: "This session or source does not support that control.",
          bad: true,
        });
        return false;
      }
      setBusy(true);
      try {
        const ack = await sendCommand(
          session,
          action,
          payload,
          revision ?? session.control_revision,
        );
        setBootstrap((b) => (b ? { ...b, control_revision: ack.revision } : b));
        const accepted = ack.status === "accepted";
        setNotice({
          text: accepted
            ? `Queued ${action.replaceAll("_", " ")}. Waiting for the processing boundary.`
            : (ack.reason ?? "Request rejected."),
          bad: !accepted,
        });
        if (accepted) {
          expectAcknowledgement(ack.request_id);
          setTimeout(refresh, ACK_REFRESH_MS);
        }
        return accepted;
      } catch (cause) {
        setNotice({ text: String(cause), bad: true });
        return false;
      } finally {
        setBusy(false);
      }
    },
    [setBootstrap, setNotice, refresh, expectAcknowledgement],
  );
  return { busy, command };
}
