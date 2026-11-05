import { useEffect, useRef } from "react";
import { useBlocker } from "react-router";

export function useUnsavedChanges(dirty: boolean) {
  const bypass = useRef(false);
  const blocker = useBlocker(({ currentLocation, nextLocation }) => {
    if (bypass.current) { bypass.current = false; return false; }
    return dirty && (currentLocation.pathname !== nextLocation.pathname || currentLocation.search !== nextLocation.search);
  });
  useEffect(() => {
    const guard = (event: BeforeUnloadEvent) => { if (dirty) event.preventDefault(); };
    window.addEventListener("beforeunload", guard);
    return () => window.removeEventListener("beforeunload", guard);
  }, [dirty]);
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    if (window.confirm("Tienes cambios sin guardar. ¿Quieres salir y descartarlos?")) blocker.proceed();
    else blocker.reset();
  }, [blocker]);
  return () => { bypass.current = true; };
}
