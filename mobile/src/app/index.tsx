import { useCallback, useEffect, useState } from 'react';
import {
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View,
} from 'react-native';

type PaperRun = {
  status?: string;
  message?: string;
  completed_components?: {
    fundamental?: CompletedComponent;
    catalyst?: CompletedComponent;
  } | null;
  confirmation_deadline_at?: string | null;
  expired_at?: string | null;
  strategy?: {
    direction?: string;
    confidence?: number;
    scores?: {
      fundamental?: number;
      catalyst?: number;
      technical?: number;
      market_memory?: number;
      news_sentiment?: number;
      total?: number;
    };
    long_evidence?: number;
    short_evidence?: number;
  } | null;
  risk?: {
    status?: string;
    reasons?: string[];
    max_risk_amount?: number;
    max_position_value?: number;
    max_quantity?: number;
    reward_risk?: number;
  } | null;
  paper_order?: {
    direction?: string;
    quantity?: number;
    reference_price?: number;
    status?: string;
  } | null;
};

type CompletedComponent = {
  direction?: string;
  score?: number;
  max_score?: number;
};

type PaperStatus = {
  event_id: string;
  instrument: string;
  event_name: string;
  scheduled_date: string;
  expectation_version: number;
  paper_run: PaperRun | null;
  trading_mode: string;
};

const API_URL =
  process.env.EXPO_PUBLIC_MARKETAI_API_URL ?? 'http://127.0.0.1:8001';
const READ_API_KEY = process.env.EXPO_PUBLIC_MARKETAI_READ_API_KEY ?? '';

export default function HomeScreen() {
  const [data, setData] = useState<PaperStatus | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadStatus = useCallback(async () => {
    try {
      setError(null);
      const response = await fetch(
        `${API_URL}/api/v1/events/hays-fy2026-results/paper-status`,
        {
          headers: {
            'X-MarketAI-Key': READ_API_KEY,
          },
        },
      );
      if (!response.ok) {
        throw new Error(`HTTP ${response.status}`);
      }
      setData(await response.json());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Tuntematon virhe');
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadStatus();
    }, 0);

    return () => clearTimeout(timer);
  }, [loadStatus]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    await loadStatus();
    setRefreshing(false);
  }, [loadStatus]);

  const run = data?.paper_run ?? null;
  const strategy = run?.strategy ?? null;
  const risk = run?.risk ?? null;
  const order = run?.paper_order ?? null;
  const scores = strategy?.scores ?? null;
  const completed = run?.completed_components ?? null;
  const fundamentalScore = completed?.fundamental?.score ?? scores?.fundamental;
  const catalystScore = completed?.catalyst?.score ?? scores?.catalyst;
  const confirmationReason =
    run?.status === 'waiting_confirmation' ? run.message : null;

  const statusText =
    error
      ? 'Palvelimeen ei saada yhteyttä'
      : !data
        ? 'Ladataan tilaa...'
        : !run
          ? 'Odottaa virallista tulosjulkistusta'
          : run.status === 'waiting_confirmation'
            ? 'Odottaa vahvistusta'
            : run.status === 'expired_no_trade'
              ? 'Vanhentui – ei kauppaa'
              : run.status === 'paper_executed'
                ? 'Paperikauppa toteutettu'
                : 'Käsitellään';

  const scheduled = data?.scheduled_date
    ? new Date(`${data.scheduled_date}T12:00:00`)
    : null;
  const day = scheduled ? String(scheduled.getDate()).padStart(2, '0') : '--';
  const month = scheduled
    ? scheduled.toLocaleString('fi-FI', { month: 'short' }).toUpperCase()
    : '---';

  const strategyDirection = localizeDirection(strategy?.direction);
  const confidence = strategy?.confidence;
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
        <RefreshControl
          refreshing={refreshing}
          onRefresh={onRefresh}
          tintColor="#8a96a8"
        />
      }
    >
      <View style={styles.header}>
        <View>
          <Text style={styles.brand}>MarketAI</Text>
          <Text style={styles.subtitle}>Kaupankäynnin tilannekuva</Text>
        </View>

        <View style={styles.paperBadge}>
          <View style={styles.statusDot} />
          <Text style={styles.paperText}>{data?.trading_mode ?? 'PAPER'}</Text>
        </View>
      </View>

      <View style={styles.eventCard}>
        <View style={styles.rowBetween}>
          <View>
            <Text style={styles.company}>
              {data?.event_name?.replace(' FY2026 results', '') ?? 'Hays plc'}
            </Text>
            <Text style={styles.symbol}>{data?.instrument ?? 'HAS.L'}</Text>
          </View>

          <View style={styles.dateBox}>
            <Text style={styles.dateDay}>{day}</Text>
            <Text style={styles.dateMonth}>{month}</Text>
          </View>
        </View>

        <Text style={styles.eventTitle}>FY2026-tulokset</Text>

        <View style={styles.waitingBox}>
          <Text style={styles.waitingLabel}>NYKYTILA</Text>
          <Text style={styles.waitingText}>{statusText}</Text>
          {confirmationReason ? (
            <Text style={styles.confirmationReason}>{confirmationReason}</Text>
          ) : null}
          {error ? (
            <Text style={styles.errorText}>{error}</Text>
          ) : null}
        </View>
      </View>

      <Text style={styles.sectionTitle}>ANALYYSI</Text>

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
          <Text style={styles.neutralBadge}>{strategyDirection}</Text>
        </View>

        <Text style={styles.bigValue}>{confidence ?? '--'}</Text>
        <Text style={styles.muted}>Luottamus</Text>

        <View style={styles.progressTrack}>
          <View style={styles.progressEmpty} />
        </View>

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
            {order.reference_price} • {order.status === 'FILLED_SIMULATED' ? 'SIMULOITU TÄYTTÖ' : 'KÄSITELLÄÄN'}
          </Text>
        ) : null}
      </View>

      <Text style={styles.sectionTitle}>TAPAHTUMAN ETENEMINEN</Text>

      <View style={styles.timelineCard}>
        <TimelineItem done label="Odotukset lukittu" />
        <TimelineItem done={Boolean(run)} label="Virallinen tulosjulkistus" />
        <TimelineItem done={Boolean(run)} label="Tekoälyanalyysi" />
        <TimelineItem done={Boolean(strategy)} label="Markkinareaktio" />
        <TimelineItem done={Boolean(strategy)} label="Tekninen vahvistus" />
        <TimelineItem done={Boolean(strategy)} label="Markkinamuisti" />
        <TimelineItem done={risk?.status === 'PASS'} label="Riskihyväksyntä" />
        <TimelineItem done={Boolean(order)} label="Paperitoimeksianto" last />
      </View>

      <Text style={styles.footer}>
        MarketAI • vain PAPER-kaupankäynti
      </Text>
    </ScrollView>
  );
}

function localizeDirection(direction?: string) {
  if (direction === 'LONG') return 'OSTO';
  if (direction === 'SHORT') return 'MYYNTI';
  return 'EI KAUPPAA';
}

function MetricCard({
  title,
  value,
  state,
}: {
  title: string;
  value: string;
  state: string;
}) {
  return (
    <View style={styles.metricCard}>
      <Text style={styles.metricTitle}>{title}</Text>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.metricState}>{state}</Text>
    </View>
  );
}

function TimelineItem({
  label,
  done = false,
  last = false,
}: {
  label: string;
  done?: boolean;
  last?: boolean;
}) {
  return (
    <View style={styles.timelineRow}>
      <View style={styles.timelineLeft}>
        <View style={[styles.timelineDot, done && styles.timelineDotDone]}>
          <Text style={styles.timelineCheck}>{done ? '✓' : ''}</Text>
        </View>
        {!last && <View style={styles.timelineLine} />}
      </View>

      <Text style={[styles.timelineText, done && styles.timelineTextDone]}>
        {label}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: '#0b0e13',
  },
  content: {
    paddingHorizontal: 18,
    paddingTop: 58,
    paddingBottom: 48,
  },
  header: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: 26,
  },
  brand: {
    color: '#f4f7fb',
    fontSize: 30,
    fontWeight: '800',
    letterSpacing: -0.8,
  },
  subtitle: {
    color: '#727b8b',
    fontSize: 13,
    marginTop: 2,
  },
  paperBadge: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: '#172219',
    borderWidth: 1,
    borderColor: '#28492f',
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderRadius: 20,
    gap: 7,
  },
  statusDot: {
    width: 7,
    height: 7,
    borderRadius: 4,
    backgroundColor: '#48c76a',
  },
  paperText: {
    color: '#72db8b',
    fontSize: 12,
    fontWeight: '800',
    letterSpacing: 1,
  },
  eventCard: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 20,
    padding: 20,
    marginBottom: 28,
  },
  rowBetween: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
  },
  company: {
    color: '#f4f7fb',
    fontSize: 24,
    fontWeight: '700',
  },
  symbol: {
    color: '#8590a1',
    fontSize: 14,
    marginTop: 3,
  },
  dateBox: {
    width: 54,
    height: 58,
    borderRadius: 14,
    backgroundColor: '#1b2230',
    alignItems: 'center',
    justifyContent: 'center',
  },
  dateDay: {
    color: '#f4f7fb',
    fontWeight: '800',
    fontSize: 21,
    lineHeight: 23,
  },
  dateMonth: {
    color: '#7f8b9e',
    fontWeight: '700',
    fontSize: 10,
    letterSpacing: 1,
  },
  eventTitle: {
    color: '#aab3c2',
    fontSize: 15,
    marginTop: 18,
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
    marginTop: 20,
  },
  muted: {
    color: '#6f7a8b',
    fontSize: 12,
  },
  progressTrack: {
    height: 6,
    backgroundColor: '#252c37',
    borderRadius: 4,
    marginVertical: 18,
    overflow: 'hidden',
  },
  progressEmpty: {
    width: 0,
    height: '100%',
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
    marginTop: 18,
  },
  timelineCard: {
    backgroundColor: '#131821',
    borderWidth: 1,
    borderColor: '#202734',
    borderRadius: 18,
    paddingHorizontal: 18,
    paddingVertical: 20,
  },
  timelineRow: {
    minHeight: 46,
    flexDirection: 'row',
  },
  timelineLeft: {
    width: 34,
    alignItems: 'center',
  },
  timelineDot: {
    width: 22,
    height: 22,
    borderRadius: 11,
    borderWidth: 2,
    borderColor: '#353e4c',
    backgroundColor: '#131821',
    alignItems: 'center',
    justifyContent: 'center',
    zIndex: 2,
  },
  timelineDotDone: {
    borderColor: '#3dbb60',
    backgroundColor: '#17351f',
  },
  timelineCheck: {
    color: '#61d77e',
    fontSize: 12,
    fontWeight: '900',
  },
  timelineLine: {
    width: 2,
    flex: 1,
    backgroundColor: '#29313d',
  },
  timelineText: {
    color: '#788496',
    fontSize: 14,
    paddingTop: 2,
    paddingLeft: 7,
  },
  timelineTextDone: {
    color: '#d1d7df',
  },
  footer: {
    color: '#4e5868',
    fontSize: 11,
    textAlign: 'center',
    marginTop: 28,
  },
});
