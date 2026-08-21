import { Stack, useLocalSearchParams } from 'expo-router';
import { Dispatch, SetStateAction, createContext, useContext, useMemo, useState } from 'react';

import { EventExpectation, StrategyDraftPreview } from '@/services/api';
import { DraftFormState } from '@/utils/strategy-draft-format';
import { useResetOnKeyChange } from '@/utils/use-reset-on-key-change';

// Shared draft/preview state across strategy/index (summary), strategy/edit
// (structured draft editor) and strategy/confirm (the explicit-confirmation
// approval screen). Kept in a Context provided by this layout, rather than
// route params, because the draft is a full structured object and Expo
// Router params are string-only - and because it must survive navigating
// edit -> index -> confirm within one visit without a round trip to the
// backend for the parts that only exist client-side (the free-text
// summary/assumptions/unresolved-questions fields) until preview is called.

export type { DraftFormState };

type StrategyDraftContextValue = {
  event: EventExpectation | null;
  setEvent: Dispatch<SetStateAction<EventExpectation | null>>;
  draft: DraftFormState | null;
  setDraft: Dispatch<SetStateAction<DraftFormState | null>>;
  preview: StrategyDraftPreview | null;
  setPreview: Dispatch<SetStateAction<StrategyDraftPreview | null>>;
};

const StrategyDraftContext = createContext<StrategyDraftContextValue | null>(null);

export function useStrategyDraft(): StrategyDraftContextValue {
  const context = useContext(StrategyDraftContext);
  if (!context) {
    throw new Error('useStrategyDraft must be used within events/[eventId]/strategy');
  }
  return context;
}

export default function StrategyLayout() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const [event, setEvent] = useState<EventExpectation | null>(null);
  const [draft, setDraft] = useState<DraftFormState | null>(null);
  const [preview, setPreview] = useState<StrategyDraftPreview | null>(null);

  // A different event's draft/preview must never be shown or approvable
  // under this event's route - reset all three the moment eventId changes,
  // before any child screen renders with the previous event's state.
  useResetOnKeyChange(eventId, () => {
    setEvent(null);
    setDraft(null);
    setPreview(null);
  });

  const value = useMemo<StrategyDraftContextValue>(
    () => ({ event, setEvent, draft, setDraft, preview, setPreview }),
    [event, draft, preview],
  );

  return (
    <StrategyDraftContext.Provider value={value}>
      <Stack screenOptions={{ headerShown: false }} />
    </StrategyDraftContext.Provider>
  );
}
