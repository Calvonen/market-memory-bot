import { useFocusEffect } from 'expo-router';
import { useCallback, useRef, useState } from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';

import { getTrackedEvents, TrackedMarketEvent } from '@/services/tracked-events';

type Snapshot = {
  count: number;
  calendarEventIds: string[];
};

type Props = {
  onSnapshot?: (snapshot: Snapshot) => void;
};

export function TrackedEventsSection({ onSnapshot }: Props) {
  const [events, setEvents] = useState<TrackedMarketEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const latestLoadId = useRef(0);

  const load = useCallback(() => {
    const loadId = ++latestLoadId.current;
    setError(null);
    return getTrackedEvents()
      .then((list) => {
        if (loadId !== latestLoadId.current) return;
        setEvents(list);
        onSnapshot?.({
          count: list.length,
          calendarEventIds: list
            .map((event) => event.calendar_event_id)
            .filter((value): value is string => Boolean(value)),
        });
      })
      .catch((err) => {
        if (loadId !== latestLoadId.current) return;
        setError(err instanceof Error ? err.message : 'Seurantatietoja ei juuri nyt saatu haettua.');
      });
  }, [onSnapshot]);

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

      {events?.map((event) => (
        <TrackedEventCard key={event.event_id} event={event} />
      ))}
    </>
  );
}

export function TrackedEventCard({ event }: { event: TrackedMarketEvent }) {
  const scheduleText = formatTrackedEventSchedule(event);
  const presentation = describeTrackedEvent(event);

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

      <View style={styles.statusBlock}>
        <Text style={styles.statusText}>{presentation.status}</Text>
        {presentation.detail ? <Text style={styles.detailText}>{presentation.detail}</Text> : null}
      </View>
    </View>
  );
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
