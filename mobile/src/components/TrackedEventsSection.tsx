import { Link, useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import {
  getTrackedEventLatestReaction,
  getTrackedEvents,
  getTrackedEventWorkflow,
  TrackedEventLatestReaction,
  TrackedEventWorkflowResponse,
  TrackedEventWorkflowStep,
  TrackedMarketEvent,
} from '@/services/tracked-events';

type Snapshot = {
  count: number;
  eventIds: string[];
  calendarEventIds: string[];
  statusByCalendarEventId: Record<string, string>;
  eventByCalendarEventId: Record<string, TrackedMarketEvent>;
};

type Props = {
  onSnapshot?: (snapshot: Snapshot) => void;
  excludeCalendarEventIds?: ReadonlySet<string>;
  refreshToken?: number;
  onRefreshSettled?: (token: number) => void;
};

type LatestReactionState =
  | { status: 'loading' }
  | { status: 'ready'; reaction: TrackedEventLatestReaction | null }
  | { status: 'error' };

type WorkflowState =
  | { status: 'loading' }
  | { status: 'ready'; workflow: TrackedEventWorkflowResponse }
  | { status: 'error' };

export function TrackedEventsSection({
  onSnapshot,
  excludeCalendarEventIds,
  refreshToken = 0,
  onRefreshSettled,
}: Props) {
  const [events, setEvents] = useState<TrackedMarketEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latestLoadId = useRef(0);

  const load = useCallback(() => {
    const loadId = ++latestLoadId.current;
    setError(null);
    // Home needs the complete active snapshot up to the backend's explicit
    // list cap, not the service helper's small display default. Otherwise an
    // active workflow can lose both its canonical card and its shell simply
    // because it happened to rank after the first 20 rows.
    return getTrackedEvents('active', 100)
      .then((list) => {
        if (loadId !== latestLoadId.current) return;
        setEvents(list);
        onSnapshot?.({
          count: list.length,
          eventIds: list.map((event) => event.event_id),
          calendarEventIds: list
            .map((event) => event.calendar_event_id)
            .filter((value): value is string => Boolean(value)),
          statusByCalendarEventId: Object.fromEntries(
            list
              .filter((event) => Boolean(event.calendar_event_id))
              .map((event) => [event.calendar_event_id as string, describeTrackedEvent(event).status]),
          ),
          eventByCalendarEventId: Object.fromEntries(
            list
              .filter((event) => Boolean(event.calendar_event_id))
              .map((event) => [event.calendar_event_id as string, event]),
          ),
        });
      })
      .catch((err) => {
        if (loadId !== latestLoadId.current) return;
        setError(err instanceof Error ? err.message : 'Seurantatietoja ei juuri nyt saatu haettua.');
      })
      .finally(() => {
        if (loadId !== latestLoadId.current) return;
        if (refreshToken > 0) onRefreshSettled?.(refreshToken);
      });
  }, [onRefreshSettled, onSnapshot, refreshToken]);

  useFocusEffect(
    useCallback(() => {
      const timer = setTimeout(() => {
        void load();
      }, 0);
      return () => clearTimeout(timer);
    }, [load]),
  );

  return (
    <>
      {error ? (
        <View style={styles.errorCard}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.retryButton} onPress={() => void load()}>
            <Text style={styles.retryText}>Yritä uudelleen</Text>
          </Pressable>
        </View>
      ) : null}

      {events
        ?.filter(
          (event) =>
            !event.calendar_event_id || !excludeCalendarEventIds?.has(event.calendar_event_id),
        )
        .map((event) => (
          <TrackedEventCard key={`${event.event_id}:${refreshToken}`} event={event} />
        ))}
    </>
  );
}

export function TrackedEventCard({ event }: { event: TrackedMarketEvent }) {
  const scheduleText = formatTrackedEventSchedule(event);
  // Release-shell expectations are currently created only for earnings.
  // Calendar-backed earnings keep the calendar identity; calendar-less
  // earnings use the tracked identity. Other tracked kinds own no expectation
  // and therefore must not get a fabricated /events/... link that 404s.
  const expectationEventId =
    event.kind === 'earnings'
      ? event.calendar_event_id
        ? `calendar:${event.calendar_event_id}`
        : `tracked:${event.event_id}`
      : null;

  return (
    <View style={styles.eventCard}>
      <View style={styles.rowBetween}>
        <View style={styles.titleBlock}>
          <Text style={styles.company} numberOfLines={1}>
            {event.company_name || event.title}
          </Text>
          <Text style={styles.symbol}>{event.instrument}</Text>
        </View>
        <Text style={styles.dateText}>{scheduleText}</Text>
      </View>

      {expectationEventId ? (
        <Link
          href={{
            pathname: '/events/[eventId]',
            params: { eventId: expectationEventId },
          }}
          style={styles.expectationLink}
        >
          Odotukset ja strategia →
        </Link>
      ) : null}

      <TrackedEventDetails event={event} />
    </View>
  );
}

export function TrackedEventDetails({
  event,
  refreshToken = 0,
  showSchedule = false,
}: {
  event: TrackedMarketEvent;
  refreshToken?: number;
  showSchedule?: boolean;
}) {
  const presentation = describeTrackedEvent(event);
  const configText = formatTrackingConfigSnapshot(event.tracking_config_snapshot);
  const [latestReactionState, setLatestReactionState] = useState<LatestReactionState>({
    status: 'loading',
  });
  const [workflowState, setWorkflowState] = useState<WorkflowState>({ status: 'loading' });

  useFocusEffect(
    useCallback(() => {
      let active = true;
      setLatestReactionState({ status: 'loading' });
      setWorkflowState({ status: 'loading' });

      void getTrackedEventLatestReaction(event.event_id)
        .then((response) => {
          if (!active) return;
          if (response.event_id !== event.event_id) {
            setLatestReactionState({ status: 'error' });
            return;
          }
          setLatestReactionState({ status: 'ready', reaction: response.latest_reaction });
        })
        .catch(() => {
          if (!active) return;
          setLatestReactionState({ status: 'error' });
        });

      void getTrackedEventWorkflow(event.event_id)
        .then((workflow) => {
          if (!active) return;
          if (workflow.event_id !== event.event_id) {
            setWorkflowState({ status: 'error' });
            return;
          }
          setWorkflowState({ status: 'ready', workflow });
        })
        .catch(() => {
          if (!active) return;
          setWorkflowState({ status: 'error' });
        });

      return () => {
        active = false;
      };
    }, [event.event_id, refreshToken]),
  );

  const scheduleText = showSchedule ? formatTrackedEventSchedule(event) : null;

  return (
    <>
      {scheduleText ? <Text style={styles.detailText}>Runtime {scheduleText}</Text> : null}
      <View style={styles.statusBlock}>
        <Text style={styles.statusText}>{presentation.status}</Text>
        {presentation.detail ? <Text style={styles.detailText}>{presentation.detail}</Text> : null}
      </View>

      <TrackedEventWorkflow state={workflowState} eventId={event.event_id} />

      <View style={styles.configBlock}>
        <Text style={styles.configTitle}>Seuranta-asetukset</Text>
        <Text style={styles.configText}>{configText}</Text>
      </View>

      <TrackedEventResult state={latestReactionState} />
    </>
  );
}

function TrackedEventWorkflow({ state, eventId }: { state: WorkflowState; eventId: string }) {
  const router = useRouter();

  if (state.status === 'loading') {
    return (
      <View style={styles.workflowBlock}>
        <Text style={styles.configTitle}>Workflow</Text>
        <Text style={styles.configText}>Haetaan workflow-tilaa…</Text>
      </View>
    );
  }
  if (state.status === 'error') {
    return (
      <View style={styles.workflowBlock}>
        <Text style={styles.configTitle}>Workflow</Text>
        <Text style={styles.configText}>Workflow-tilaa ei juuri nyt saatu haettua.</Text>
      </View>
    );
  }

  return (
    <View style={styles.workflowBlock}>
      <Text style={styles.configTitle}>Workflow</Text>
      {state.workflow.steps.map((step) => (
        <View key={step.key}>
          <View style={styles.workflowRow}>
            <Text style={styles.workflowStep}>{workflowStepLabel(step.key)}</Text>
            <Text
              style={[
                styles.workflowStatus,
                step.status === 'action_required' ? styles.workflowActionRequired : null,
                step.status === 'failed' ? styles.workflowFailed : null,
              ]}
            >
              {workflowStatusLabel(step)}
            </Text>
          </View>
          {step.status === 'action_required' && step.action_reason ? (
            <Text style={styles.configText} numberOfLines={3}>
              {step.action_reason}
            </Text>
          ) : null}
          {step.status === 'action_required' && step.action_target === 'release' ? (
            <Pressable
              style={styles.workflowActionButton}
              onPress={() =>
                router.push({
                  pathname: '/tracked-events/[eventId]/release',
                  params: { eventId },
                })
              }
            >
              <Text style={styles.workflowActionButtonText}>Tarkista julkaisu</Text>
            </Pressable>
          ) : null}
        </View>
      ))}
    </View>
  );
}

const WORKFLOW_STEP_LABELS: Record<string, string> = {
  tracking: 'Seuranta',
  event_identified: 'Tapahtuma tunnistettu',
  release: 'Julkaisu',
  analysis: 'Analyysi',
  market_reaction: 'Markkinareaktio',
  strategy: 'Strategia',
  risk: 'Riski',
  paper: 'Paperikauppa',
  live: 'Live-kauppa',
};

const WORKFLOW_STATUS_LABELS: Record<TrackedEventWorkflowStep['status'], string> = {
  pending: 'Odottaa',
  running: 'Käynnissä',
  completed: 'Valmis',
  skipped: 'Ohitettu',
  failed: 'Epäonnistui',
  action_required: 'Toimia tarvitaan',
};

function workflowStepLabel(key: string): string {
  return WORKFLOW_STEP_LABELS[key] ?? key.replaceAll('_', ' ');
}

function workflowStatusLabel(step: TrackedEventWorkflowStep): string {
  return WORKFLOW_STATUS_LABELS[step.status];
}

function TrackedEventResult({ state }: { state: LatestReactionState }) {
  if (state.status === 'loading') {
    return (
      <View style={styles.resultBlock}>
        <Text style={styles.configTitle}>Tulos</Text>
        <Text style={styles.configText}>Haetaan viimeisintä havaintoa…</Text>
      </View>
    );
  }
  if (state.status === 'error') {
    return (
      <View style={styles.resultBlock}>
        <Text style={styles.configTitle}>Tulos</Text>
        <Text style={styles.configText}>Tulosta ei juuri nyt saatu haettua.</Text>
      </View>
    );
  }
  if (!state.reaction) {
    return (
      <View style={styles.resultBlock}>
        <Text style={styles.configTitle}>Tulos</Text>
        <Text style={styles.configText}>Ei vielä tallennettua markkinareaktiota.</Text>
      </View>
    );
  }

  const reaction = state.reaction;
  return (
    <View style={styles.resultBlock}>
      <Text style={styles.configTitle}>Tulos</Text>
      <Text style={styles.resultText}>
        Viimeisin havainto · {reaction.interval_minutes} min · Muutos {reaction.return_pct} %
      </Text>
      <Text style={styles.resultDetail}>
        Reference {reaction.reference_price} · Close {reaction.close_price}
      </Text>
      <Text style={styles.resultDetail}>
        Suunta {reaction.direction} · Kehitys {reaction.evolution}
      </Text>
    </View>
  );
}

function formatTrackingConfigSnapshot(
  snapshot: TrackedMarketEvent['tracking_config_snapshot'],
): string {
  if (!snapshot || snapshot.schema_version !== 1) {
    return 'Seuranta-asetuksia ei tallennettu';
  }
  const monitor = formatPersistedNumber(snapshot.monitor_hours);
  const reference = formatPersistedNumber(snapshot.reference_lead_seconds);
  const marketWait = formatPersistedNumber(snapshot.max_wait_for_market_hours);
  const stages = snapshot.reaction_stages
    .map(
      (stage) =>
        `${formatPersistedNumber(stage.start_after_minutes)}m→${formatPersistedNumber(stage.interval_minutes)}m`,
    )
    .join(' · ');
  return `Seuranta ${monitor} h · Reference ${reference} s ennen tapahtumaa · Markkinan odotus enintään ${marketWait} h · Aikavälit ${stages}`;
}

function formatPersistedNumber(value: number): string {
  return String(value);
}

const EVENT_TIME_STATUS_LABELS: Record<TrackedMarketEvent['event_time_status'], string> = {
  confirmed: 'vahvistettu',
  estimated: 'arvioitu',
  unknown: 'aika epävarma',
};

function formatTrackedEventSchedule(event: TrackedMarketEvent): string {
  const timeStatusLabel = EVENT_TIME_STATUS_LABELS[event.event_time_status] ?? 'aika epävarma';
  const eventAt = new Date(event.event_at);
  if (Number.isNaN(eventAt.getTime())) {
    return `${event.event_at} · ${timeStatusLabel}`;
  }
  const datePart = eventAt.toLocaleDateString('fi-FI');
  const hours = String(eventAt.getHours()).padStart(2, '0');
  const minutes = String(eventAt.getMinutes()).padStart(2, '0');
  return `${datePart} klo ${hours}.${minutes} · ${timeStatusLabel}`;
}

const MAX_FAILURE_REASON_LENGTH = 180;

function formatFailureReason(lastError: string | null): string {
  const trimmed = (lastError ?? '').trim();
  if (!trimmed) return 'Syytä ei ole tiedossa.';
  if (trimmed.length <= MAX_FAILURE_REASON_LENGTH) return trimmed;
  return `${trimmed.slice(0, MAX_FAILURE_REASON_LENGTH).trimEnd()}…`;
}

function describeTrackedEvent(event: TrackedMarketEvent): { status: string; detail: string | null } {
  if (event.status === 'failed') {
    return { status: 'Seuranta epäonnistui', detail: formatFailureReason(event.last_error) };
  }
  if (event.status === 'cancelled') {
    return { status: 'Seuranta peruttu', detail: null };
  }
  if (event.status === 'completed') {
    return {
      status: 'Seuranta valmis',
      detail: event.reference_price ? `Reference ${event.reference_price}` : null,
    };
  }
  if (event.status === 'monitoring') {
    return {
      status: event.reaction_anchor_at
        ? 'Markkinareaktiota seurataan'
        : 'Odottaa markkinan avautumista',
      detail: event.reference_price ? `Reference ${event.reference_price}` : null,
    };
  }
  if (event.reference_price) {
    return { status: 'Reference tallennettu', detail: `Reference ${event.reference_price}` };
  }
  if (event.resolved_etoro_instrument_id !== null) {
    return { status: 'Seuranta valmiina', detail: 'eToro ✓ · Odottaa referencea' };
  }
  return { status: 'Seuranta valmiina', detail: 'Odottaa eToro-tunnistusta' };
}

const styles = StyleSheet.create({
  eventCard: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'flex-start',
    justifyContent: 'space-between',
  },
  titleBlock: {
    flex: 1,
    paddingRight: 12,
  },
  company: {
    color: '#f4f7fb',
    fontSize: 17,
    fontWeight: '700',
  },
  symbol: {
    color: '#8590a1',
    fontSize: 13,
    marginTop: 2,
  },
  dateText: {
    color: '#aab3c2',
    fontSize: 13,
    fontWeight: '600',
  },
  expectationLink: {
    color: '#72b8db',
    fontSize: 12,
    fontWeight: '700',
    marginTop: 10,
  },
  statusBlock: {
    marginTop: 12,
  },
  statusText: {
    color: '#d7ad5f',
    fontSize: 13,
    fontWeight: '700',
  },
  detailText: {
    color: '#8994a6',
    fontSize: 12,
    marginTop: 4,
  },
  workflowBlock: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: '#202734',
  },
  workflowRow: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    gap: 12,
    marginTop: 5,
  },
  workflowStep: {
    color: '#b8c1ce',
    fontSize: 12,
    flex: 1,
  },
  workflowStatus: {
    color: '#8994a6',
    fontSize: 12,
    fontWeight: '700',
  },
  workflowActionRequired: {
    color: '#d7ad5f',
  },
  workflowFailed: {
    color: '#e17878',
  },
  workflowActionButton: {
    alignSelf: 'flex-start',
    marginTop: 8,
    borderWidth: 1,
    borderColor: '#4c4028',
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 7,
    backgroundColor: '#221e16',
  },
  workflowActionButtonText: {
    color: '#d7ad5f',
    fontSize: 12,
    fontWeight: '800',
  },
  configBlock: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: '#202734',
  },
  configTitle: {
    color: '#8590a1',
    fontSize: 11,
    fontWeight: '700',
    textTransform: 'uppercase',
    letterSpacing: 0.3,
  },
  configText: {
    color: '#8994a6',
    fontSize: 12,
    marginTop: 2,
  },
  resultBlock: {
    marginTop: 10,
    paddingTop: 10,
    borderTopWidth: 1,
    borderTopColor: '#202734',
  },
  resultText: {
    color: '#d8dee8',
    fontSize: 12,
    marginTop: 2,
    fontWeight: '600',
  },
  resultDetail: {
    color: '#8994a6',
    fontSize: 12,
    marginTop: 2,
  },
  errorCard: {
    backgroundColor: '#1c1417',
    borderWidth: 1,
    borderColor: '#3a2226',
    borderRadius: 16,
    padding: 16,
    marginBottom: 12,
  },
  errorText: {
    color: '#e17878',
    fontSize: 13,
  },
  retryButton: {
    alignSelf: 'flex-start',
    backgroundColor: '#2a1b1e',
    borderWidth: 1,
    borderColor: '#4a2b30',
    borderRadius: 12,
    paddingHorizontal: 14,
    paddingVertical: 8,
    marginTop: 12,
  },
  retryText: {
    color: '#e17878',
    fontSize: 12,
    fontWeight: '800',
  },
});