"use client";

import {
  createContext,
  useCallback,
  useContext,
  useRef,
  useState,
} from "react";

interface LoadingContextType {
  start: () => void;
  done: () => void;
  isLoading: boolean;
}

const LoadingContext = createContext<LoadingContextType>({
  start: () => {},
  done: () => {},
  isLoading: false,
});

export function useLoading() {
  return useContext(LoadingContext);
}

export function LoadingProvider({ children }: { children: React.ReactNode }) {
  const [isLoading, setIsLoading] = useState(false);
  const countRef = useRef(0);

  const start = useCallback(() => {
    countRef.current += 1;
    setIsLoading(true);
  }, []);

  const done = useCallback(() => {
    countRef.current = Math.max(0, countRef.current - 1);
    if (countRef.current === 0) {
      setIsLoading(false);
    }
  }, []);

  return (
    <LoadingContext.Provider value={{ start, done, isLoading }}>
      {children}
      {isLoading && <LoadingBar />}
    </LoadingContext.Provider>
  );
}

function LoadingBar() {
  return (
    <div className="fixed top-0 left-0 right-0 z-[9999] h-0.5">
      <div className="h-full bg-primary animate-loading-bar" />
    </div>
  );
}
