"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ApiError } from "./api/client";
import { AttemptKeys } from "./idempotency";
import { ERROR_LABEL } from "./labels";

export interface Failure {
  code: string;
  message: string;
}

function toFailure(error: unknown): Failure {
  if (error instanceof ApiError) {
    return { code: error.code, message: ERROR_LABEL[error.code] ?? error.message };
  }
  return { code: "INTERNAL_ERROR", message: "Something went wrong." };
}

interface Settled<T> {
  key: string;
  attempt: number;
  data: T | null;
  failure: Failure | null;
}

export interface AsyncResult<T> {
  data: T | null;
  failure: Failure | null;
  loading: boolean;
  reload: () => void;
  setData: (data: T) => void;
}

export function useAsync<T>(load: () => Promise<T>, deps: unknown[]): AsyncResult<T> {
  const key = JSON.stringify(deps);

  const [settled, setSettled] = useState<Settled<T> | null>(null);
  const [attempt, setAttempt] = useState(0);

  const alive = useRef(true);
  const latest = useRef(load);
  const generation = useRef(0);

  useEffect(() => {
    latest.current = load;
  });

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const run = useCallback(async (forKey: string, forAttempt: number) => {
    const mine = ++generation.current;
    try {
      const result = await latest.current();
      if (alive.current && mine === generation.current) {
        setSettled({ key: forKey, attempt: forAttempt, data: result, failure: null });
      }
    } catch (error) {
      if (alive.current && mine === generation.current) {
        setSettled({ key: forKey, attempt: forAttempt, data: null, failure: toFailure(error) });
      }
    }
  }, []);

  useEffect(() => {
    void run(key, attempt);
  }, [run, key, attempt]);

  const reload = useCallback(() => setAttempt((count) => count + 1), []);

  const setData = useCallback(
    (data: T) => setSettled({ key, attempt, data, failure: null }),
    [key, attempt],
  );

  return {
    data: settled?.data ?? null,
    failure: settled?.failure ?? null,
    loading: settled === null || settled.key !== key || settled.attempt !== attempt,
    reload,
    setData,
  };
}

export function useAction<A extends unknown[], T>(
  action: (key: string, ...args: A) => Promise<T>,
  identity: string = "",
) {
  const [pending, setPending] = useState(false);
  const [failure, setFailure] = useState<Failure | null>(null);
  const attempts = useRef(new AttemptKeys());

  const run = useCallback(
    async (...args: A): Promise<T | null> => {
      const key = attempts.current.take(identity);

      setPending(true);
      setFailure(null);
      try {
        const result = await action(key, ...args);
        attempts.current.settle();
        return result;
      } catch (error) {
        if (!unanswered(error)) attempts.current.settle();
        setFailure(toFailure(error));
        return null;
      } finally {
        setPending(false);
      }
    },
    [action, identity],
  );

  return { run, pending, failure, clear: () => setFailure(null) };
}

function unanswered(error: unknown): boolean {
  return error instanceof ApiError && error.status === 0;
}
