import { useCallback, useEffect, useRef, useState } from 'react';
import { playgroundApi } from '@/lib/api';
import { isSeedanceModel, parsePlaygroundCapabilities, type PlaygroundBlockReason } from '@/lib/playgroundCapabilities';
import { getRuntimeEnvironment, isCurrentRuntimeEnvironment, subscribeRuntimeEnvironment } from '@/lib/runtimeEnvironment';

type State = { owner: string; loading: boolean; reason: PlaygroundBlockReason | null };
export function useSeedanceCapability(modelId: string) {
  const [state, setState] = useState<State | null>(null);
  const revision = useRef(0);
  const enabled = isSeedanceModel(modelId);
  const environment = getRuntimeEnvironment();
  const owner = JSON.stringify([modelId, environment?.key, environment?.generation]);
  const recheck = useCallback(async () => {
    const seq = ++revision.current;
    const captured = getRuntimeEnvironment();
    const capturedOwner = JSON.stringify([modelId, captured?.key, captured?.generation]);
    if (!isSeedanceModel(modelId)) return;
    setState({ owner: capturedOwner, loading: true, reason: null });
    const live = () => seq === revision.current && (!captured ? !getRuntimeEnvironment() : isCurrentRuntimeEnvironment(captured.key, captured.generation));
    try {
      if (!captured) throw new Error('No runtime');
      const capabilities = parsePlaygroundCapabilities(await playgroundApi.getCapabilities());
      if (live()) setState({ owner: capturedOwner, loading: false, reason: capabilities.seedance.reason });
    } catch {
      if (live()) setState({ owner: capturedOwner, loading: false, reason: 'seedance_capability_check_failed' });
    }
  }, [modelId]);
  useEffect(() => {
    void recheck();
    const unsubscribe = subscribeRuntimeEnvironment(() => { void recheck(); });
    return () => { revision.current += 1; unsubscribe(); };
  }, [recheck]);
  const loading = enabled && (state?.owner !== owner || state.loading);
  const reason = enabled && state?.owner === owner ? state.reason : null;
  return { loading, reason, available: !enabled || (!loading && !reason), recheck };
}
