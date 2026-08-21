import { useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { BackButton } from '@/components/back-button';
import { getEvent, previewStrategyDraft } from '@/services/api';
import { draftFormFromEvent, draftFormToInput, textToList } from '@/utils/strategy-draft-format';
import { useResetOnKeyChange } from '@/utils/use-reset-on-key-change';
import { useStrategyDraft } from './_layout';

// Primary strategy UX: a clear, human-readable summary of the current
// draft (initially the currently-approved expectation) with exactly two
// actions - edit the draft, or start approving it. This replaces the raw
// field editor as the default entry point; the raw editor still exists at
// /events/[eventId]/edit as an advanced/debug view.

export default function StrategySummaryScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const router = useRouter();
  const { event, setEvent, draft, setDraft, preview, setPreview } = useStrategyDraft();
  const [error, setError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const latestLoadId = useRef(0);
  const latestPreviewRequestId = useRef(0);

  // A previous event's in-flight load, preview error, and in-flight state
  // must not linger under - or repopulate - a different event's route.
  useResetOnKeyChange(eventId, () => {
    // Invalidate any load() still in flight for the previous event FIRST,
    // synchronously, before anything else below. load() itself is only
    // (re)invoked from the effect further down via a deferred
    // setTimeout(0) - there would otherwise be a window, between eventId
    // changing here and that deferred call actually starting, where a
    // slow response for the *old* event could still resolve, find
    // latestLoadId.current unchanged, pass the staleness check, and
    // repopulate this (freshly cleared) screen with the wrong event's
    // data. Bumping the ref here - not inside load() - closes that window
    // entirely: it happens in the same synchronous render pass that
    // detects the eventId change.
    latestLoadId.current += 1;
    // Same reasoning for an in-flight previewStrategyDraft() call: a
    // resolved preview for the *previous* event must never call
    // setPreview() or navigate to the confirmation screen under the new
    // event's route.
    latestPreviewRequestId.current += 1;
    setError(null);
    setPreviewing(false);
    setPreviewError(null);
  });

  // Same invalidation on unmount: navigating away entirely (not just to a
  // different eventId) must also stop a still-in-flight preview response
  // from calling setState on an unmounted screen or firing a navigation
  // nobody asked for anymore.
  useEffect(() => {
    return () => {
      latestPreviewRequestId.current += 1;
    };
  }, []);

  const load = useCallback(async () => {
    if (!eventId) return;
    const loadId = ++latestLoadId.current;
    try {
      setError(null);
      const detail = await getEvent(eventId);
      if (loadId !== latestLoadId.current) return;
      setEvent(detail);
      // Only seed the draft from the current expectation the first time
      // this screen is visited for this event - once the user has started
      // editing (or a preview has already run), the current expectation
      // fetched here must never silently overwrite their in-progress draft.
      setDraft((existing) => existing ?? draftFormFromEvent(detail));
    } catch (err) {
      if (loadId !== latestLoadId.current) return;
      setError(err instanceof Error ? err.message : 'Tuntematon virhe');
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [eventId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void load();
    }, 0);
    return () => clearTimeout(timer);
  }, [load]);

  const onApprovePress = useCallback(async () => {
    if (!eventId || !draft) return;
    const requestId = ++latestPreviewRequestId.current;
    setPreviewError(null);
    setPreviewing(true);
    try {
      const result = await previewStrategyDraft(eventId, draftFormToInput(draft));
      // A stale response for an eventId change or an unmount that happened
      // while this request was in flight must never call setPreview() or
      // navigate - both would apply/act on the wrong event.
      if (requestId !== latestPreviewRequestId.current) return;
      setPreview(result);
      router.push({ pathname: '/events/[eventId]/strategy/confirm', params: { eventId } });
    } catch (err) {
      if (requestId !== latestPreviewRequestId.current) return;
      setPreviewError(err instanceof Error ? err.message : 'Esikatselu epäonnistui');
    } finally {
      if (requestId === latestPreviewRequestId.current) setPreviewing(false);
    }
  }, [eventId, draft, router, setPreview]);

  if (!event && !error) {
    return (
      <View style={styles.loadingScreen}>
        <BackButton label="Takaisin" />
        <ActivityIndicator color="#8a96a8" />
      </View>
    );
  }

  if (error && !event) {
    return (
      <View style={styles.loadingScreen}>
        <BackButton label="Takaisin" />
        <Text style={styles.errorText}>{error}</Text>
        <Pressable style={styles.retryButton} onPress={() => void load()}>
          <Text style={styles.retryButtonText}>Yritä uudelleen</Text>
        </Pressable>
      </View>
    );
  }

  if (!event || !draft) return null;

  const warnings = preview?.warnings ?? [];
  const unresolvedQuestions = textToList(draft.unresolvedText);
  const draftState = preview ? 'ESIKATSELTU' : 'LUONNOS';

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <BackButton label="Takaisin" />

      <View style={styles.headerCard}>
        <View style={styles.rowBetween}>
          <Text style={styles.company}>{event.event_name}</Text>
          <Text style={styles.stateBadge}>{draftState}</Text>
        </View>
        <Text style={styles.symbol}>{event.instrument}</Text>
        <Text style={styles.muted}>
          Nykyinen hyväksytty versio: {event.version}
        </Text>
      </View>

      <Text style={styles.sectionTitle}>YHTEENVETO</Text>
      <View style={styles.card}>
        <Text style={styles.summaryText}>
          {draft.summary.trim() || 'Ei vielä yhteenvetoa - muokkaa luonnosta.'}
        </Text>
      </View>

      <Text style={styles.sectionTitle}>KONSENSUS</Text>
      <TextBlockCard text={draft.consensusText} empty="Ei konsensuslukuja." />

      <Text style={styles.sectionTitle}>TÄRKEIMMÄT KPI:T</Text>
      <TextBlockCard text={draft.kpiText} empty="Ei merkintöjä." />

      <Text style={styles.sectionTitle}>BULL-SKENAARIO</Text>
      <TextBlockCard text={draft.bullText} empty="Ei merkintöjä." />

      <Text style={styles.sectionTitle}>BASE-SKENAARIO</Text>
      <TextBlockCard text={draft.baseText} empty="Ei merkintöjä." />

      <Text style={styles.sectionTitle}>BEAR-SKENAARIO</Text>
      <TextBlockCard text={draft.bearText} empty="Ei merkintöjä." />

      <Text style={styles.sectionTitle}>TRIGGERIT</Text>
      <TextBlockCard text={draft.triggersText} empty="Ei triggereitä." />

      <Text style={styles.sectionTitle}>NO TRADE / MITÄTÖINTIEHDOT</Text>
      <TextBlockCard text={draft.invalidationText} empty="Ei mitätöintiehtoja." />

      <Text style={styles.sectionTitle}>LÄHTEET</Text>
      <View style={styles.card}>
        <Text style={styles.muted}>{draft.sourceName || 'Ei lähdettä merkitty'}</Text>
        {draft.sourceUrl ? <Text style={styles.muted}>{draft.sourceUrl}</Text> : null}
        {draft.sourceAsOf ? <Text style={styles.muted}>Päivitetty: {draft.sourceAsOf}</Text> : null}
      </View>

      {warnings.length > 0 || unresolvedQuestions.length > 0 ? (
        <>
          <Text style={styles.sectionTitle}>VAROITUKSET JA AVOIMET KYSYMYKSET</Text>
          <View style={styles.warningCard}>
            {warnings.map((warning, index) => (
              <Text key={`warning-${index}`} style={styles.warningText}>
                ⚠ {warning}
              </Text>
            ))}
            {unresolvedQuestions.map((question, index) => (
              <Text key={`question-${index}`} style={styles.warningText}>
                ? {question}
              </Text>
            ))}
          </View>
        </>
      ) : null}

      {previewError ? <Text style={styles.errorText}>{previewError}</Text> : null}

      <Pressable
        style={styles.editButton}
        onPress={() =>
          router.push({ pathname: '/events/[eventId]/strategy/edit', params: { eventId } })
        }
      >
        <Text style={styles.editButtonText}>Muokkaa luonnosta</Text>
      </Pressable>

      <Pressable
        style={[styles.approveButton, previewing && styles.approveButtonDisabled]}
        onPress={() => void onApprovePress()}
        disabled={previewing}
      >
        {previewing ? (
          <ActivityIndicator color="#0b0e13" />
        ) : (
          <Text style={styles.approveButtonText}>Hyväksy strategia</Text>
        )}
      </Pressable>

      <Pressable
        style={styles.advancedLink}
        onPress={() =>
          router.push({ pathname: '/events/[eventId]/edit', params: { eventId } })
        }
      >
        <Text style={styles.advancedLinkText}>Lisäasetukset (raakakenttäeditori / debug)</Text>
      </Pressable>
    </ScrollView>
  );
}

function TextBlockCard({ text, empty }: { text: string; empty: string }) {
  const lines = textToList(text);
  return (
    <View style={styles.card}>
      {lines.length === 0 ? (
        <Text style={styles.muted}>{empty}</Text>
      ) : (
        lines.map((line, index) => (
          <Text key={index} style={styles.listItem}>
            • {line}
          </Text>
        ))
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0b0e13' },
  loadingScreen: {
    flex: 1,
    backgroundColor: '#0b0e13',
    alignItems: 'center',
    justifyContent: 'center',
    paddingHorizontal: 24,
  },
  retryButton: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 14,
    paddingVertical: 13,
    paddingHorizontal: 24,
    marginTop: 16,
  },
  retryButtonText: { color: '#72b8db', fontSize: 14, fontWeight: '700' },
  content: { paddingHorizontal: 18, paddingTop: 58, paddingBottom: 48 },
  headerCard: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 20,
    padding: 20,
    marginBottom: 20,
  },
  rowBetween: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  company: { color: '#f4f7fb', fontSize: 20, fontWeight: '700', flexShrink: 1 },
  symbol: { color: '#8590a1', fontSize: 14, marginTop: 3 },
  stateBadge: {
    color: '#d7ad5f',
    backgroundColor: '#2a2317',
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderRadius: 8,
    overflow: 'hidden',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1,
  },
  sectionTitle: {
    color: '#687386',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.4,
    marginBottom: 8,
    marginTop: 4,
    marginLeft: 3,
  },
  card: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 16,
    padding: 16,
    marginBottom: 14,
  },
  summaryText: { color: '#d9dee7', fontSize: 14, lineHeight: 21 },
  muted: { color: '#6f7a8b', fontSize: 12, lineHeight: 18 },
  listItem: { color: '#b7c2d7', fontSize: 13, lineHeight: 20 },
  warningCard: {
    backgroundColor: '#241d10',
    borderWidth: 1,
    borderColor: '#4a3a1a',
    borderRadius: 16,
    padding: 16,
    marginBottom: 18,
    gap: 6,
  },
  warningText: { color: '#c7b385', fontSize: 13, lineHeight: 19 },
  editButton: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 14,
    paddingVertical: 15,
    alignItems: 'center',
    marginTop: 8,
    marginBottom: 12,
  },
  editButtonText: { color: '#72b8db', fontSize: 15, fontWeight: '700' },
  approveButton: {
    backgroundColor: '#4fae7a',
    borderRadius: 14,
    paddingVertical: 16,
    alignItems: 'center',
    marginBottom: 12,
  },
  approveButtonDisabled: { opacity: 0.6 },
  approveButtonText: { color: '#0b0e13', fontSize: 15, fontWeight: '800' },
  advancedLink: { alignItems: 'center', paddingVertical: 10, marginBottom: 24 },
  advancedLinkText: { color: '#4e5868', fontSize: 12 },
  errorText: { color: '#e17878', fontSize: 12, marginBottom: 10 },
});
