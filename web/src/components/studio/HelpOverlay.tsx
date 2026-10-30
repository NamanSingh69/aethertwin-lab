import { useRef } from "react";
import { useFocusTrap } from "../../hooks/useFocusTrap";
import { SHORTCUTS } from "../../hooks/useKeyboardShortcuts";
import { Icon } from "../Icon";

export function HelpOverlay({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLElement>(null);
  useFocusTrap(dialog, onClose);
  return (
    <div className="modal-scrim" onClick={(e) => e.target === e.currentTarget && onClose()}>
      <section
        ref={dialog}
        className="modal help-modal"
        role="dialog"
        aria-modal="true"
        aria-labelledby="help-heading"
      >
        <div className="modal-head">
          <div>
            <span className="eyebrow">KEYBOARD</span>
            <h2 id="help-heading">Shortcuts</h2>
          </div>
          <button className="icon-button" onClick={onClose} aria-label="Close shortcuts">
            <Icon name="close" size={16} />
          </button>
        </div>
        <dl className="shortcut-list">
          {SHORTCUTS.map(({ keys, action }) => (
            <div key={action}>
              <dt>
                {keys.map((key) =>
                  key === "–" ? (
                    <span key={key}>–</span>
                  ) : (
                    <kbd key={key}>{key}</kbd>
                  ),
                )}
              </dt>
              <dd>{action}</dd>
            </div>
          ))}
        </dl>
        <p className="caption">
          Shortcuts pause while you type in a field or while a dialog is open.
          Controls still follow your role: viewers cannot pause processing.
        </p>
      </section>
    </div>
  );
}
