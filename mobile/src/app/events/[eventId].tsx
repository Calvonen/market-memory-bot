import { Link, useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import {
  EventExpectation,
  getEvent,
  getPaperStatus,
  PaperRun,
} from '@/services/api';

export default function EventDetailScreen() {
  const { eventId } = useLocalSearchParams<{ eventId: string }>();
  const [event, setEvent] = useState<EventExpectation | null>(null);
  const [run, setRun] = useState<PaperRun | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const latestLoadId = useRef(0);

  const load = useCallback(async () => {
    if (!eventId) return;
    // Guard against overlapping load() calls (e.g. pull-to-refresh fired
    // again while the previous call's getPaperStatus() is still pending):
    // an older response resolving after a newer one must never overwrite
    // it, or a run could silently regress (e.g. paper_executed back to
    // waiting_confirmation) even though it's the same expectation version,
    // so the stale-run warning above wouldn't catch it either.
    const loadId = ++latestLoadId.current;
    setError(null);
    try {
      const eventDetail = await getEvent(eventId);
      if (loadId !== latestLoadId.current) return;
      setEvent(eventDetail);
    } catch (err) {
      if (loadId !== latestLoadId.current) return;
      setError(err instanceof Error ? err.message : 'Tuntematon virhe');
      return;
    }
    // The paper run is a separate, optional overlay: an event whose release
    // hasn't happened yet (or whose paper-status lookup fails) must still
    // show the pre-release expectation data fetched above. A fetch failure
    // here is reported distinctly (statusError) from "not released yet"
    // (run === null with no error) so the analysis section can say so
    // explicitly instead of silently looking like a pre-release event.
    try {
      const status = await getPaperStatus(eventId);
      if (loadId !== latestLoadId.current) return;
      setRun(status.paper_run);
      setStatusError(null);
    } catch (err) {
      if (loadId !== latestLoadId.current) return;
      setRun(null);
      setStatusError(err instanceof Error ? err.message : 'Tuntematon virhe');
    }
  }, [eventId]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void load();
    }, 0);
    return () => clearTimeout(timer);
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await load();
    setRefreshing(false);
  }, [load]);

  if (!event && !error) {
    return (
      <View style={styles.loadingScreen}>
        <ActivityIndicator color="#8a96a8" />
      </View>
    );
  }

  if (error && !event) {
    return (
      <View style={styles.loadingScreen}>
        <Text style={styles.errorText}>{error}</Text>
      </View>
    );
  }

  if (!event) return null;

  const strategy = run?.strategy ?? null;
  const risk = run?.risk ?? null;
  const order = run?.paper_order ?? null;
  const scores = strategy?.scores ?? null;
  const completed = run?.completed_components ?? null;
  const fundamentalScore = completed?.fundamental?.score ?? scores?.fundamental;
  const catalystScore = completed?.catalyst?.score ?? scores?.catalyst;
  const confirmationReason =
    run?.status === 'waiting_confirmation' ? run.message : null;
  const isReleased = Boolean(run);
  // The run's analysis/strategy/risk/paper-order decision was computed
  // against a specific expectation version. If the expectation has since
  // been edited (event.version moved on), that decision no longer reflects
  // the consensus/KPIs/triggers shown above - flag it instead of silently
  // presenting a stale dashboard next to the current expectation.
  const isStaleRun =
    isReleased &&
    run?.expectation_version !== undefined &&
    run.expectation_version !== event.version;

  const statusText = describeStatus(run, statusError);

  const scheduled = new Date(`${event.scheduled_date}T12:00:00`);
  const dateLabel = Number.isNaN(scheduled.getTime())
    ? event.scheduled_date
    : scheduled.toLocaleDateString('fi-FI');

  const riskStatus =
    risk?.status === 'PASS'
      ? 'HYVÄKSYTTY'
      : risk?.status === 'REJECT'
        ? 'HYLÄTTY'
        : 'ODOTTAA';
  const riskReasonText =
    risk?.status === 'REJECT' && risk.reasons?.length
      ? `${risk.reasons.join(' • ')} • `
      : '';

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={styles.content}
      showsVerticalScrollIndicator={false}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#8a96a8" />
      }
    >
      <View style={styles.eventCard}>
        <View style={styles.rowBetween}>
          <View>
            <Text style={styles.company}>{event.event_name}</Text>
            <Text style={styles.symbol}>{event.instrument}</Text>
          </View>
          <Text style={styles.dateText}>{dateLabel}</Text>
        </View>

        <View style={styles.waitingBox}>
          <Text style={styles.waitingLabel}>NYKYTILA</Text>
          <Text style={styles.waitingText}>{statusText}</Text>
          {confirmationReason ? (
            <Text style={styles.confirmationReason}>{confirmationReason}</Text>
          ) : null}
          {error ? <Text style={styles.errorText}>{error}</Text> : null}
        </View>
      </View>

      <Link
        href={{ pathname: '/events/[eventId]/edit', params: { eventId: event.event_id } }}
        asChild
      >
        <Pressable style={styles.editButton}>
          <Text style={styles.editButtonText}>Muokkaa asetuksia</Text>
        </Pressable>
      </Link>

      <Text style={styles.sectionTitle}>ODOTUKSET</Text>

      <View style={styles.card}>
        <Text style={styles.cardTitle}>KONSENSUS</Text>
        {Object.keys(event.consensus).length === 0 ? (
          <Text style={styles.muted}>Ei konsensuslukuja.</Text>
        ) : (
          Object.entries(event.consensus).map(([metric, value]) => (
            <View key={metric} style={styles.scoreRow}>
              <Text style={styles.muted}>{metric}</Text>
              <Text style={styles.score}>{value ?? '--'}</Text>
            </View>
          ))
        )}
      </View>

      <ListCard title="TÄRKEIMMÄT KPI:T" items={event.important_kpis} />
      <ListCard title="BULL-SKENAARIO" items={event.bull_case} />
      <ListCard title="BASE-SKENAARIO" items={event.base_case} />
      <ListCard title="BEAR-SKENAARIO" items={event.bear_case} />

      <View style={styles.card}>
        <Text style={styles.cardTitle}>TRIGGERIT</Text>
        {Object.keys(event.triggers).length === 0 ? (
          <Text style={styles.muted}>Ei määriteltyjä triggereitä.</Text>
        ) : (
          Object.entries(event.triggers).map(([trigger, value]) => (
            <View key={trigger} style={styles.scoreRow}>
              <Text style={styles.muted}>{trigger}</Text>
              <Text style={styles.score}>{value}</Text>
            </View>
          ))
        )}
      </View>

      <ListCard title="MITÄTÖINTIEHDOT" items={event.invalidation_conditions} />

      <View style={styles.card}>
        <Text style={styles.cardTitle}>LÄHDE</Text>
        <Text style={styles.muted}>{event.source_name ?? 'Ei lähdettä merkitty'}</Text>
        {event.source_url ? <Text style={styles.muted}>{event.source_url}</Text> : null}
        {event.source_as_of ? (
          <Text style={styles.muted}>Päivitetty: {event.source_as_of}</Text>
        ) : null}
      </View>

      {isReleased ? (
        <>
          <Text style={styles.sectionTitle}>ANALYYSI</Text>

          {isStaleRun ? (
            <View style={styles.staleRunNotice}>
              <Text style={styles.staleRunNoticeTitle}>VANHENTUNUT ANALYYSI</Text>
              <Text style={styles.staleRunNoticeText}>
                Alla oleva analyysi, riski ja paperitoimeksianto perustuvat
                odotusversioon {run?.expectation_version}, mutta odotukset on
                sittemmin päivitetty versioon {event.version}. Tulokset eivät
                välttämättä vastaa yllä näkyvää konsensusta.
              </Text>
            </View>
          ) : null}

          <View style={styles.grid}>
            <MetricCard
              title="Perustekijät"
              value={`${fundamentalScore ?? '--'} / 35`}
              state={fundamentalScore !== undefined ? 'Valmis' : 'Odottaa'}
            />
            <MetricCard
              title="Kurssiajuri"
              value={`${catalystScore ?? '--'} / 25`}
              state={catalystScore !== undefined ? 'Valmis' : 'Odottaa'}
            />
            <MetricCard
              title="Tekninen"
              value={`${scores?.technical ?? '--'} / 20`}
              state={scores ? 'Valmis' : 'Odottaa'}
            />
            <MetricCard
              title="Markkinamuisti"
              value={`${scores?.market_memory ?? '--'} / 10`}
              state={scores ? 'Valmis' : 'Odottaa'}
            />
          </View>

          <View style={styles.card}>
            <View style={styles.rowBetween}>
              <Text style={styles.cardTitle}>STRATEGIA</Text>
              <Text style={styles.neutralBadge}>{localizeDirection(strategy?.direction)}</Text>
            </View>

            <Text style={styles.bigValue}>{strategy?.confidence ?? '--'}</Text>
            <Text style={styles.muted}>Luottamus</Text>

            <View style={styles.scoreRow}>
              <Text style={styles.muted}>OSTO-näyttö</Text>
              <Text style={styles.score}>{strategy?.long_evidence ?? '--'}</Text>
            </View>
            <View style={styles.scoreRow}>
              <Text style={styles.muted}>MYYNTI-näyttö</Text>
              <Text style={styles.score}>{strategy?.short_evidence ?? '--'}</Text>
            </View>
          </View>

          <View style={styles.card}>
            <View style={styles.rowBetween}>
              <Text style={styles.cardTitle}>RISKINHALLINTA</Text>
              <Text style={styles.pendingBadge}>{riskStatus}</Text>
            </View>

            <Text style={styles.riskMessage}>
              {risk
                ? `${riskReasonText}Riskiarvio ${risk.status === 'PASS' ? 'hyväksytty' : 'hylätty'} • Enimmäismäärä ${risk.max_quantity ?? '--'} • Tuotto/riski ${risk.reward_risk ?? '--'}`
                : 'Riskiarvio alkaa hyväksyttävän strategiapäätöksen jälkeen.'}
            </Text>
            {order ? (
              <Text style={styles.orderText}>
                Paperitoimeksianto: {localizeDirection(order.direction)} {order.quantity} @{' '}
                {order.reference_price} •{' '}
                {order.status === 'FILLED_SIMULATED' ? 'SIMULOITU TÄYTTÖ' : 'KÄSITELLÄÄN'}
              </Text>
            ) : null}
          </View>
        </>
      ) : statusError ? (
        <>
          <Text style={styles.sectionTitle}>ANALYYSI</Text>
          <View style={styles.card}>
            <Text style={styles.cardTitle}>ANALYYSIN TILA</Text>
            <Text style={styles.errorText}>
              Tila ei saatavilla: analyysin ja paperikaupan tietoja ei juuri nyt saatu
              haettua ({statusError}). Odotukset yllä ovat silti ajan tasalla.
            </Text>
          </View>
        </>
      ) : null}

      <Text style={styles.footer}>MarketAI • vain PAPER-kaupankäynti</Text>
    </ScrollView>
  );
}

function ListCard({ title, items }: { title: string; items: string[] }) {
  return (
    <View style={styles.card}>
      <Text style={styles.cardTitle}>{title}</Text>
      {items.length === 0 ? (
        <Text style={styles.muted}>Ei merkintöjä.</Text>
      ) : (
        items.map((item, index) => (
          <Text key={index} style={styles.listItem}>
            • {item}
          </Text>
        ))
      )}
    </View>
  );
}

function MetricCard({ title, value, state }: { title: string; value: string; state: string }) {
  return (
    <View style={styles.metricCard}>
      <Text style={styles.metricTitle}>{title}</Text>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.metricState}>{state}</Text>
    </View>
  );
}

function describeStatus(run: PaperRun | null, statusError: string | null): string {
  if (statusError) return 'Tila ei saatavilla';
  if (!run) return 'Odottaa virallista tulosjulkistusta';
  switch (run.status) {
    case 'waiting_confirmation':
      return 'Odottaa vahvistusta';
    case 'expired_no_trade':
      return 'Vanhentui – ei kauppaa';
    case 'paper_executed':
      return 'Paperikauppa toteutettu';
    default:
      return 'Käsitellään';
  }
}

function localizeDirection(direction?: string) {
  if (direction === 'LONG') return 'OSTO';
  if (direction === 'SHORT') return 'MYYNTI';
  return 'EI KAUPPAA';
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: '#0b0e13',
  },
  loadingScreen: {
    flex: 1,
    backgroundColor: '#0b0e13',
    alignItems: 'center',
    justifyContent: 'center',
  },
  content: {
    paddingHorizontal: 18,
    paddingTop: 58,
    paddingBottom: 48,
  },
  eventCard: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 20,
    padding: 20,
    marginBottom: 16,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  company: {
    color: '#f4f7fb',
    fontSize: 22,
    fontWeight: '700',
  },
  symbol: {
    color: '#8590a1',
    fontSize: 14,
    marginTop: 3,
  },
  dateText: {
    color: '#aab3c2',
    fontSize: 14,
    fontWeight: '600',
  },
  waitingBox: {
    marginTop: 18,
    backgroundColor: '#10151c',
    borderRadius: 14,
    padding: 15,
  },
  waitingLabel: {
    color: '#667183',
    fontSize: 10,
    fontWeight: '700',
    letterSpacing: 1.2,
  },
  waitingText: {
    color: '#d9dee7',
    fontSize: 16,
    fontWeight: '600',
    marginTop: 5,
  },
  confirmationReason: {
    color: '#8994a6',
    fontSize: 13,
    lineHeight: 19,
    marginTop: 7,
  },
  editButton: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 14,
    paddingVertical: 13,
    alignItems: 'center',
    marginBottom: 26,
  },
  editButtonText: {
    color: '#72b8db',
    fontSize: 14,
    fontWeight: '700',
  },
  staleRunNotice: {
    backgroundColor: '#2a1717',
    borderWidth: 1,
    borderColor: '#4a2323',
    borderRadius: 14,
    padding: 15,
    marginBottom: 16,
  },
  staleRunNoticeTitle: {
    color: '#e17878',
    fontSize: 10,
    fontWeight: '800',
    letterSpacing: 1.2,
  },
  staleRunNoticeText: {
    color: '#d1a3a3',
    fontSize: 13,
    lineHeight: 19,
    marginTop: 6,
  },
  sectionTitle: {
    color: '#687386',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.4,
    marginBottom: 11,
    marginLeft: 3,
  },
  grid: {
    flexDirection: 'row',
    flexWrap: 'wrap',
    justifyContent: 'space-between',
    gap: 10,
    marginBottom: 26,
  },
  metricCard: {
    width: '48.4%',
    minHeight: 116,
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 17,
    padding: 15,
  },
  metricTitle: {
    color: '#8994a6',
    fontSize: 12,
    fontWeight: '600',
  },
  metricValue: {
    color: '#f1f4f8',
    fontSize: 21,
    fontWeight: '700',
    marginTop: 13,
  },
  metricState: {
    color: '#626d7e',
    fontSize: 12,
    marginTop: 5,
  },
  card: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 18,
    padding: 18,
    marginBottom: 18,
  },
  cardTitle: {
    color: '#8a96a8',
    fontSize: 11,
    fontWeight: '800',
    letterSpacing: 1.2,
    marginBottom: 8,
  },
  neutralBadge: {
    color: '#adb6c4',
    backgroundColor: '#202631',
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderRadius: 8,
    overflow: 'hidden',
    fontSize: 10,
    fontWeight: '800',
  },
  pendingBadge: {
    color: '#d7ad5f',
    backgroundColor: '#2a2317',
    paddingHorizontal: 9,
    paddingVertical: 5,
    borderRadius: 8,
    overflow: 'hidden',
    fontSize: 10,
    fontWeight: '800',
  },
  bigValue: {
    color: '#f4f7fb',
    fontSize: 36,
    fontWeight: '800',
    marginTop: 12,
  },
  muted: {
    color: '#6f7a8b',
    fontSize: 12,
    lineHeight: 18,
  },
  listItem: {
    color: '#b7c2d7',
    fontSize: 13,
    lineHeight: 20,
  },
  scoreRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 4,
  },
  score: {
    color: '#c7ced8',
    fontWeight: '700',
  },
  errorText: {
    color: '#e17878',
    fontSize: 12,
    marginTop: 8,
  },
  orderText: {
    color: '#9aa5b7',
    fontSize: 12,
    lineHeight: 18,
    marginTop: 12,
  },
  riskMessage: {
    color: '#747f91',
    fontSize: 13,
    lineHeight: 20,
    marginTop: 6,
  },
  footer: {
    color: '#4e5868',
    fontSize: 11,
    textAlign: 'center',
    marginTop: 28,
  },
});
