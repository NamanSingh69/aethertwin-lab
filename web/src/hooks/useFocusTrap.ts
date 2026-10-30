import { type RefObject, useEffect, useRef } from "react";

const FOCUSABLE =
  "button:not(:disabled),input:not(:disabled),select:not(:disabled),textarea:not(:disabled),a[href],[tabindex]:not([tabindex='-1'])";

/**
 * Modal focus handling: focus the first control on open, keep Tab and
 * Shift+Tab inside the container, close on Escape and restore the previously
 * focused element on close.
 */
export function useFocusTrap(
  container: RefObject<HTMLElement | null>,
  onClose: () => void,
) {
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const items = () =>
      Array.from(
        container.current?.querySelectorAll<HTMLElement>(FOCUSABLE) ?? [],
      );
    items()[0]?.focus();
    const key = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        closeRef.current();
      }
      if (event.key === "Tab") {
        const focusable = items();
        if (!focusable.length) return;
        const first = focusable[0],
          last = focusable.at(-1)!;
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("keydown", key);
      previous?.focus();
    };
  }, [container]);
}
