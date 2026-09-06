import { useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ActivityIndicator,
  Alert,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';

import { BackButton } from '@/components/back-button';
import {
  approveAssistantProposalPreparation,
  AssistantPreparationApprovalResult,
  AssistantProposal,
  getAssistantProposal,
} from '@/services/assistant-proposals';
import {
  approveTrackedEventPaperPermission,
  getTrackedEventPaperPermission,
  type TrackedEventPaperPermission,
} from '@/services/tracked-events';

type StrategyView = {
  summary?: string;
  important_kpis?: string[];
  bull_case?: string[];
  base_case?: string[];
  bear_case?: string[];
  invalidation_conditions?: string[];
  triggers?: Record<string, unknown>;
};

const DEFAULT_DEMO_POSITION_CAP_USD = '500';

function parsePositiveUsd(value: string): number | null {
  const parsed = Number(value.trim().replace(',', '.'));
  return Number.isFinite(parsed) && parsed > 0 ? parsed : null;
}

function DetailList({ title, items }: { title: string; items?: string[] }) {
  if (!items?.length) return null;
  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>{title}</Text>
      {items.map((item, index) => (
        <Text key={`${title}:${index}`} style={styles.bodyText}>• {item}</Text>
      ))}
    </View>
  );
}

function showLiveLocked() {
  Alert.alert(
    'LIVE lukittu',
    'Live-kaupankäynti ei ole vielä käytettävissä – eToron live-kaupankäyntiyhteys on lukittu.',
  );
}

export default function AssistantProposalDetailScreen() {
  const params = useLocalSearchParams<{ proposalId?: string }>();
  const proposalId = typeof params.proposalId === 'string' ? params.proposalId : '';
  const [proposal, setProposal] = useState<AssistantProposal | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approving, setApproving] = useState(false);
  const [approval, setApproval] = useState<AssistantPreparationApprovalResult | null>(null);
  const [reviewer, setReviewer] = useState('');
  const [paperPermission, setPaperPermission] = useState<TrackedEventPaperPermission | null>(null);
  const [permissionError, setPermissionError] = useState<string | null>(null);
  const [permissionLoading, setPermissionLoading] = useState(false);
  const [approvingDemo, setApprovingDemo] = useState(false);
  const [demoActor, setDemoActor] = useState('');
  const [maxPositionUsd, setMaxPositionUsd] = useState(DEFAULT_DEMO_POSITION_CAP_USD);
  const proposalIdRef = useRef(proposalId);
  const trackedEventIdRef = useRef<string | null>(null);
  const demoApprovalRequestRef = useRef(0);

  useEffect(() => {
    proposalIdRef.current = proposalId;
    demoApprovalRequestRef.current += 1;
    trackedEventIdRef.current = null;
    setApprovingDemo(false);
    setProposal(null);
    setPaperPermission(null);
    setPermissionError(null);
    setPermissionLoading(false);
  }, [proposalId]);

  const load = useCallback(async () => {
    if (!proposalId) {
      setError('Proposal-ID puuttuu.');
      return;
    }
    try {
      setError(null);
      const loaded = await getAssistantProposal(proposalId);
      if (proposalIdRef.current !== proposalId) return;
      setProposal(loaded);
    } catch (err) {
      if (proposalIdRef.current !== proposalId) return;
      setError(err instanceof Error ? err.message : 'Ehdotuksen lataus epäonnistui');
    }
  }, [proposalId]);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    let cancelled = false;
    const materialization = proposal?.materialization;
    const trackedEventId =
      proposal?.status === 'materialized' && materialization
        ? materialization.tracked_event_id
        : null;

    if (trackedEventIdRef.current !== trackedEventId) {
      demoApprovalRequestRef.current += 1;
      setApprovingDemo(false);
    }
    trackedEventIdRef.current = trackedEventId;
    setPaperPermission(null);
    setPermissionError(null);

    if (!trackedEventId) {
      setPermissionLoading(false);
      return () => {
        cancelled = true;
      };
    }

    setPermissionLoading(true);
    void getTrackedEventPaperPermission(trackedEventId)
      .then((permission) => {
        if (cancelled || trackedEventIdRef.current !== trackedEventId) return;
        if (permission.event_id !== trackedEventId) {
          setPaperPermission(null);
          setPermissionError('DEMO-luvan canonical tracked event ei vastaa proposalin materialisointia.');
          return;
        }
        setPaperPermission(permission);
        if (permission.max_position_value_usd !== null) {
          setMaxPositionUsd(String(permission.max_position_value_usd));
        }
      })
      .catch((err) => {
        if (cancelled || trackedEventIdRef.current !== trackedEventId) return;
        setPaperPermission(null);
        setPermissionError(err instanceof Error ? err.message : 'DEMO-luvan lataus epäonnistui.');
      })
      .finally(() => {
        if (!cancelled && trackedEventIdRef.current === trackedEventId) {
          setPermissionLoading(false);
        }
      });

    return () => {
      cancelled = true;
    };
  }, [proposal?.id, proposal?.materialization?.tracked_event_id, proposal?.status]);

  const strategy = useMemo(
    () => (proposal?.payload.strategy ?? {}) as StrategyView,
    [proposal],
  );
  const exactStrategyPayload = useMemo(
    () => JSON.stringify(proposal?.payload.strategy ?? {}, null, 2),
    [proposal],
  );

  const approve = useCallback(async () => {
    if (!proposal || proposal.id !== proposalId || !reviewer.trim()) return;
    setApproving(true);
    try {
      setError(null);
      const result = await approveAssistantProposalPreparation(
        proposal.id,
        reviewer.trim(),
        proposal.review_round,
      );
      if (proposalIdRef.current !== result.proposal_id) return;
      setApproval(result);
      setProposal((current) => current && current.id === result.proposal_id ? {
        ...current,
        status: 'materialized',
        reviewed_by: reviewer.trim(),
        review_round: result.review_round,
        materialization: {
          event_id: result.event_id,
          tracked_event_id: result.tracked_event_id,
          expectation_version: result.expectation_version,
        },
      } : current);
      void load();
    } catch (err) {
      if (proposalIdRef.current !== proposal.id) return;
      setError(err instanceof Error ? err.message : 'Valmistelun hyväksyntä epäonnistui');
    } finally {
      if (proposalIdRef.current === proposal.id) setApproving(false);
    }
  }, [load, proposal, proposalId, reviewer]);

  const materialization = proposal?.materialization ?? null;
  const requestedDemo = proposal?.requested_execution_mode === 'demo';
  const requestedLive = proposal?.requested_execution_mode === 'live';
  const proposalMatchesRoute = Boolean(proposal && proposal.id === proposalId);
  const canApprove = proposalMatchesRoute
    && proposal
    && proposal.status !== 'materialized'
    && requestedDemo
    && reviewer.trim().length > 0
    && reviewer.trim().length <= 135;
  const parsedMaxPositionUsd = parsePositiveUsd(maxPositionUsd);
  const permissionMatchesTrackedEvent = Boolean(
    materialization
    && paperPermission
    && paperPermission.event_id === materialization.tracked_event_id,
  );
  const expectationMatchesMaterialization = Boolean(
    permissionMatchesTrackedEvent
    && materialization
    && paperPermission
    && paperPermission.current_expectation_version === materialization.expectation_version,
  );
  const demoAuthorityCurrent = Boolean(
    permissionMatchesTrackedEvent
    && paperPermission?.approval_current
    && materialization
    && paperPermission.approved_expectation_version === materialization.expectation_version,
  );
  const canApproveDemo = Boolean(
    proposalMatchesRoute
    && proposal
    && proposal.status === 'materialized'
    && requestedDemo
    && materialization
    && paperPermission
    && permissionMatchesTrackedEvent
    && expectationMatchesMaterialization
    && !demoAuthorityCurrent
    && demoActor.trim()
    && parsedMaxPositionUsd !== null
    && !approvingDemo,
  );

  const confirmDemoAuthority = useCallback(() => {
    if (
      !proposal
      || proposal.id !== proposalId
      || !materialization
      || !paperPermission
      || !canApproveDemo
      || parsedMaxPositionUsd === null
    ) return;
    const actor = demoActor.trim();
    const expectedVersion = materialization.expectation_version;
    const trackedEventId = materialization.tracked_event_id;
    const submittedProposalId = proposal.id;
    Alert.alert(
      'Hyväksy DEMO-kaupankäynti',
      `Annat MarketAI:lle kertaluonteisen luvan tehdä ${proposal.payload.instrument}-demokaupan tämän yhden tapahtuman perusteella. Expectation v${expectedVersion}. Enimmäispositio ${parsedMaxPositionUsd} USD. Strategy ja Risk Engine voivat silti estää kaupan tai pienentää positiota. LIVE-kaupankäyntiä tämä ei mahdollista.`,
      [
        { text: 'Peruuta', style: 'cancel' },
        {
          text: 'Hyväksy DEMO',
          onPress: () => {
            if (
              proposalIdRef.current !== submittedProposalId
              || trackedEventIdRef.current !== trackedEventId
            ) return;
            const requestToken = demoApprovalRequestRef.current + 1;
            demoApprovalRequestRef.current = requestToken;
            setApprovingDemo(true);
            setPermissionError(null);
            void approveTrackedEventPaperPermission(
              trackedEventId,
              actor,
              {
                expected_expectation_version: expectedVersion,
                max_position_value_usd: parsedMaxPositionUsd,
              },
            )
              .then((permission) => {
                if (
                  demoApprovalRequestRef.current !== requestToken
                  || proposalIdRef.current !== submittedProposalId
                  || trackedEventIdRef.current !== trackedEventId
                ) return;
                if (permission.event_id !== trackedEventId) {
                  setPaperPermission(null);
                  setPermissionError('DEMO-luvan canonical tracked event ei vastaa proposalin materialisointia.');
                  return;
                }
                setPaperPermission(permission);
                if (permission.max_position_value_usd !== null) {
                  setMaxPositionUsd(String(permission.max_position_value_usd));
                }
              })
              .catch(async (err) => {
                if (
                  demoApprovalRequestRef.current !== requestToken
                  || proposalIdRef.current !== submittedProposalId
                  || trackedEventIdRef.current !== trackedEventId
                ) return;
                const writeError = err instanceof Error ? err.message : 'DEMO-luvan hyväksyntä epäonnistui.';
                setPermissionError(writeError);
                try {
                  const current = await getTrackedEventPaperPermission(trackedEventId);
                  if (
                    demoApprovalRequestRef.current !== requestToken
                    || proposalIdRef.current !== submittedProposalId
                    || trackedEventIdRef.current !== trackedEventId
                  ) return;
                  if (current.event_id !== trackedEventId) {
                    setPaperPermission(null);
                    setPermissionError('DEMO-luvan canonical tracked event ei vastaa proposalin materialisointia.');
                    return;
                  }
                  setPaperPermission(current);
                  if (current.max_position_value_usd !== null) {
                    setMaxPositionUsd(String(current.max_position_value_usd));
                  }
                  setPermissionError(writeError);
                } catch {
                  if (demoApprovalRequestRef.current !== requestToken) return;
                  setPaperPermission(null);
                  setPermissionError(writeError);
                }
              })
              .finally(() => {
                if (demoApprovalRequestRef.current === requestToken) {
                  setApprovingDemo(false);
                }
              });
          },
        },
      ],
    );
  }, [canApproveDemo, demoActor, materialization, paperPermission, parsedMaxPositionUsd, proposal, proposalId]);

  return (
    <ScrollView style={styles.screen} contentContainerStyle={styles.content}>
      <BackButton label="Ehdotuksiin" />
      {!proposal && !error ? <ActivityIndicator color="#8a96a8" style={styles.loader} /> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}

      {proposal ? (
        <>
          <Text style={styles.title}>{proposal.payload.company_name}</Text>
          <Text style={styles.subtitle}>
            {proposal.payload.instrument} · {proposal.payload.market} · {proposal.payload.scheduled_date}
          </Text>

          <View style={styles.modeRow}>
            <View style={[styles.modeChip, requestedDemo ? styles.demoChip : styles.inactiveChip]}>
              <Text style={requestedDemo ? styles.demoText : styles.inactiveText}>
                {requestedDemo ? '● DEMO' : 'DEMO'}
              </Text>
            </View>
            <Pressable
              style={[styles.modeChip, requestedLive ? styles.liveRequestedChip : styles.liveChip]}
              onPress={showLiveLocked}
            >
              <Text style={requestedLive ? styles.liveRequestedText : styles.liveText}>
                {requestedLive ? '🔒 LIVE · pyydetty' : '🔒 LIVE'}
              </Text>
            </Pressable>
          </View>
          {requestedLive ? (
            <Text style={styles.lockedNotice}>
              Proposal pyytää LIVE-tilaa. LIVE on lukittu eikä tätä proposal-ehdotusta voi hyväksyä valmisteluun.
            </Text>
          ) : null}

          <View style={styles.card}>
            <Text style={styles.cardTitle}>{proposal.payload.title}</Text>
            <Text style={styles.meta}>Julkaisuaika: {proposal.payload.event_time_status}</Text>
            <Text style={styles.meta}>Review-kierros: {proposal.review_round}</Text>
            <Text style={styles.meta}>Reviewattu expectation-versio: {proposal.payload.base_expectation_version}</Text>
            <Text style={styles.meta}>Lähde: {proposal.payload.official_source.source_url}</Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>Strategia</Text>
            {strategy.summary ? <Text style={styles.bodyText}>{strategy.summary}</Text> : null}
            <DetailList title="Tärkeät KPI:t" items={strategy.important_kpis} />
            <DetailList title="Bull case" items={strategy.bull_case} />
            <DetailList title="Base case" items={strategy.base_case} />
            <DetailList title="Bear case" items={strategy.bear_case} />
            {strategy.triggers && Object.keys(strategy.triggers).length ? (
              <View style={styles.section}>
                <Text style={styles.sectionTitle}>Triggerit</Text>
                {Object.entries(strategy.triggers).map(([key, value]) => (
                  <Text key={key} style={styles.bodyText}>• {key}: {String(value)}</Text>
                ))}
              </View>
            ) : null}
            <DetailList title="Invalidointi" items={strategy.invalidation_conditions} />
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Tallennettu strategiapayload kokonaisuudessaan</Text>
              <Text selectable style={styles.payloadText}>{exactStrategyPayload}</Text>
            </View>
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>Valmisteluhyväksyntä</Text>
            <Text style={styles.bodyText}>
              Tämä hyväksyntä lisää tapahtuman MarketAI:n canonical seurantaan. Se ei anna DEMO/PAPER- eikä LIVE-kaupankäyntilupaa.
            </Text>
            <Text style={styles.inputLabel}>HYVÄKSYJÄ</Text>
            <TextInput
              style={styles.input}
              value={reviewer}
              onChangeText={setReviewer}
              maxLength={135}
              autoCapitalize="none"
              autoCorrect={false}
              placeholder="Kirjoita oma hyväksyjäidentiteettisi"
              placeholderTextColor="#596476"
              editable={proposal.status !== 'materialized'}
            />
            {proposal.status === 'materialized' ? (
              <View style={styles.doneBox}>
                <Text style={styles.doneText}>✓ Valmistelu on hyväksytty ja materialisoitu.</Text>
              </View>
            ) : (
              <Pressable
                style={[styles.approveButton, !canApprove && styles.disabledButton]}
                disabled={!canApprove || approving}
                onPress={() => void approve()}
              >
                <Text style={styles.approveText}>
                  {approving ? 'Hyväksytään…' : 'Hyväksy valmistelu'}
                </Text>
              </Pressable>
            )}
            {approval ? (
              <Text style={styles.successText}>
                Canonical event: {approval.event_id} · expectation v{approval.expectation_version}. Kaupankäyntilupaa ei myönnetty.
              </Text>
            ) : null}
          </View>

          {proposal.status === 'materialized' && requestedDemo ? (
            <View style={styles.card}>
              <Text style={styles.cardTitle}>DEMO-kaupankäyntilupa</Text>
              <Text style={styles.bodyText}>
                Tämä on erillinen kertaluonteinen kaupankäyntivaltuus. Strategy ja Risk Engine ovat edelleen pakollisia. LIVE pysyy lukittuna.
              </Text>
              {materialization ? (
                <>
                  <Text style={styles.meta}>Tracked event: {materialization.tracked_event_id}</Text>
                  <Text style={styles.meta}>Materialisoitu expectation: v{materialization.expectation_version}</Text>
                </>
              ) : (
                <Text style={styles.error}>Canonical materialization-lineage puuttuu. DEMO-lupaa ei voi antaa.</Text>
              )}
              {permissionLoading ? <ActivityIndicator color="#8a96a8" /> : null}
              {permissionError ? <Text style={styles.error}>{permissionError}</Text> : null}
              {paperPermission && materialization && permissionMatchesTrackedEvent && !expectationMatchesMaterialization ? (
                <Text style={styles.error}>
                  Canonical expectation on muuttunut versioon v{paperPermission.current_expectation_version}. Tämä proposal materialisoitiin versiolle v{materialization.expectation_version}; DEMO-lupa vaatii uuden reviewn.
                </Text>
              ) : null}
              {demoAuthorityCurrent ? (
                <View style={styles.doneBox}>
                  <Text style={styles.doneText}>
                    ✓ DEMO-kaupankäyntilupa on voimassa expectation-versiolle v{paperPermission?.approved_expectation_version}.
                  </Text>
                </View>
              ) : (
                <>
                  <Text style={styles.inputLabel}>DEMO-LUVAN HYVÄKSYJÄ</Text>
                  <TextInput
                    style={styles.input}
                    value={demoActor}
                    onChangeText={setDemoActor}
                    autoCapitalize="none"
                    autoCorrect={false}
                    placeholder="Kirjoita oma hyväksyjäidentiteettisi"
                    placeholderTextColor="#596476"
                  />
                  <Text style={styles.inputLabel}>ENIMMÄISPOSITIO USD</Text>
                  <TextInput
                    style={styles.input}
                    value={maxPositionUsd}
                    onChangeText={setMaxPositionUsd}
                    keyboardType="decimal-pad"
                    placeholder="500"
                    placeholderTextColor="#596476"
                  />
                  <Pressable
                    style={[styles.approveButton, !canApproveDemo && styles.disabledButton]}
                    disabled={!canApproveDemo}
                    onPress={confirmDemoAuthority}
                  >
                    <Text style={styles.approveText}>
                      {approvingDemo ? 'Hyväksytään…' : 'Hyväksy DEMO-kaupankäynti'}
                    </Text>
                  </Pressable>
                </>
              )}
            </View>
          ) : null}
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0b0e13' },
  content: { paddingHorizontal: 18, paddingTop: 58, paddingBottom: 48, gap: 14 },
  loader: { marginTop: 24 },
  title: { color: '#f4f7fb', fontSize: 24, fontWeight: '800' },
  subtitle: { color: '#8590a1', fontSize: 13, marginTop: -6 },
  error: { color: '#e17878', fontSize: 13 },
  modeRow: { flexDirection: 'row', gap: 8 },
  modeChip: { borderWidth: 1, borderRadius: 18, paddingHorizontal: 12, paddingVertical: 7 },
  demoChip: { backgroundColor: '#15281f', borderColor: '#28553d' },
  inactiveChip: { backgroundColor: '#17191d', borderColor: '#30343b' },
  liveChip: { backgroundColor: '#17191d', borderColor: '#30343b' },
  liveRequestedChip: { backgroundColor: '#2a1d1d', borderColor: '#704040' },
  demoText: { color: '#65c98b', fontSize: 12, fontWeight: '800' },
  inactiveText: { color: '#727985', fontSize: 12, fontWeight: '800' },
  liveText: { color: '#727985', fontSize: 12, fontWeight: '800' },
  liveRequestedText: { color: '#e09a9a', fontSize: 12, fontWeight: '800' },
  lockedNotice: { color: '#e09a9a', fontSize: 12, lineHeight: 18 },
  card: { backgroundColor: '#131821', borderWidth: 1, borderColor: '#202734', borderRadius: 16, padding: 16, gap: 8 },
  cardTitle: { color: '#f4f7fb', fontSize: 17, fontWeight: '800' },
  meta: { color: '#8994a6', fontSize: 12, lineHeight: 18 },
  bodyText: { color: '#c2cad6', fontSize: 13, lineHeight: 19 },
  payloadText: { color: '#aeb8c7', fontSize: 11, lineHeight: 16, fontFamily: 'monospace' },
  section: { marginTop: 6, gap: 3 },
  sectionTitle: { color: '#72b8db', fontSize: 12, fontWeight: '800', marginBottom: 2 },
  inputLabel: { color: '#687386', fontSize: 11, fontWeight: '800', letterSpacing: 1.1, marginTop: 6 },
  input: { backgroundColor: '#0e131b', borderWidth: 1, borderColor: '#2a3342', borderRadius: 10, padding: 12, color: '#f4f7fb' },
  approveButton: { backgroundColor: '#206a45', borderRadius: 10, padding: 14, alignItems: 'center', marginTop: 4 },
  disabledButton: { opacity: 0.4 },
  approveText: { color: '#fff', fontWeight: '800' },
  doneBox: { backgroundColor: '#15281f', borderWidth: 1, borderColor: '#28553d', borderRadius: 10, padding: 12 },
  doneText: { color: '#65c98b', fontWeight: '700' },
  successText: { color: '#65c98b', fontSize: 12, lineHeight: 18 },
});