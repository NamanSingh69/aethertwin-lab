import { useEffect, useRef } from "react";

/** Shown in the help dialog; the handler in App.tsx implements each one. */
export const SHORTCUTS: Array<{ keys: string[]; action: string }> = [
  { keys: ["Space"], action: "Pause or resume processing (operators)" },
  { keys: ["1", "–", "9"], action: "Show or hide a layer, in dock order" },
  { keys: ["C"], action: "Toggle the map cutaway" },
  { keys: ["F"], action: "Fly to the selected track" },
  { keys: ["I"], action: "Isometric camera" },
  { keys: ["T"], action: "Top camera" },
  { keys: ["S"], action: "Sensor camera" },
  { keys: ["Q"], action: "Cycle render quality" },
  { keys: ["R"], action: "Open or close route planning" },
  { keys: ["E"], action: "Open or close session events" },
  { keys: ["Esc"], action: "Cancel picking or close the top panel" },
  { keys: ["?"], action: "Show keyboard shortcuts" },
];

function typing(target: EventTarget | null) {
  return (
    target instanceof HTMLElement &&
    (target.isContentEditable ||
      target.closest("input, select, textarea, [contenteditable]") !== null)
  );
}

/**
 * Global single-key shortcuts. Ignored while typing, while a modal dialog is
 * open (it owns the keyboard) and with Ctrl/Alt/Meta held. Space is left to a
 * focused button or link so keyboard activation keeps working. The handler
 * returns true when it used the key.
 */
export function useKeyboardShortcuts(
  handler: (key: string, event: KeyboardEvent) => boolean,
) {
  const ref = useRef(handler);
  ref.current = handler;
  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if (
        event.defaultPrevented ||
        event.repeat ||
        event.ctrlKey ||
        event.altKey ||
        event.metaKey ||
        event.isComposing ||
        typing(event.target) ||
        document.querySelector('[aria-modal="true"]')
      )
        return;
      if (
        event.key === " " &&
        event.target instanceof HTMLElement &&
        event.target.closest("button, a[href], summary, [role='button'], [role='switch']")
      )
        return;
      if (ref.current(event.key, event)) event.preventDefault();
    };
    document.addEventListener("keydown", listener);
    return () => document.removeEventListener("keydown", listener);
  }, []);
}
