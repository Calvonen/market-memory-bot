import { useState } from 'react';

// Resets local/context state synchronously whenever `key` changes - e.g.
// the route's eventId param. Uses React's documented "adjusting state when
// a prop changes" pattern (a guarded conditional setState call during
// render), not an effect hook, so there is no one-frame window where state
// from the previous key (a different event's draft/preview/approval UI)
// can still render under the new key. Safe against infinite loops: once
// onChange() runs, seenKey is updated to key, so the condition is false on
// the very next render.
export function useResetOnKeyChange(key: string | undefined, onChange: () => void): void {
  const [seenKey, setSeenKey] = useState(key);
  if (key !== seenKey) {
    setSeenKey(key);
    onChange();
  }
}
