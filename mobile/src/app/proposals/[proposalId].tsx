import { useLocalSearchParams } from 'expo-router';
import { useCallback, useEffect, useMemo, useState } from 'react';
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

type StrategyView = {
  summary?: string;
  important_kpis?: string[];
  bull_case?: string[];
  base_case?: string[];
  bear_case?: string[];
  invalidation_conditions?: string[];
  triggers?: Record<string, unknown>;
};

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
  const [reviewer, setReviewer] = useState('marko');

  const load = useCallback(async () => {
    if (!proposalId) {
      setError('Proposal-ID puuttuu.');
      return;
    }
    try {
      setError(null);
      setProposal(await getAssistantProposal(proposalId));
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ehdotuksen lataus epäonnistui');
    }
  }, [proposalId]);

  useEffect(() => {
    void load();
  }, [load]);

  const strategy = useMemo(
    () => (proposal?.payload.strategy ?? {}) as StrategyView,
    [proposal],
  );

  const approve = useCallback(async () => {
    if (!proposal || !reviewer.trim()) return;
    setApproving(true);
    try {
      setError(null);
      const result = await approveAssistantProposalPreparation(proposal.id, reviewer.trim());
      setApproval(result);
      setProposal((current) => current ? { ...current, status: 'materialized', reviewed_by: reviewer.trim() } : current);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Valmistelun hyväksyntä epäonnistui');
    } finally {
      setApproving(false);
    }
  }, [proposal, reviewer]);

  const canApprove = proposal
    && proposal.status !== 'materialized'
    && proposal.requested_execution_mode === 'demo'
    && reviewer.trim().length > 0
    && reviewer.trim().length <= 135;

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
            <View style={[styles.modeChip, styles.demoChip]}>
              <Text style={styles.demoText}>● DEMO</Text>
            </View>
            <Pressable style={[styles.modeChip, styles.liveChip]} onPress={showLiveLocked}>
              <Text style={styles.liveText}>🔒 LIVE</Text>
            </Pressable>
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>{proposal.payload.title}</Text>
            <Text style={styles.meta}>Julkaisuaika: {proposal.payload.event_time_status}</Text>
            <Text style={styles.meta}>Reviewattu expectation-versio: {proposal.payload.base_expectation_version}</Text>
            <Text style={styles.meta}>Lähde: {proposal.payload.official_source.source_url}</Text>
          </View>

          <View style={styles.card}>
            <Text style={styles.cardTitle}>Strategia</Text>
            <Text style={styles.meta}>Post-release confirmation</Text>
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
  liveChip: { backgroundColor: '#17191d', borderColor: '#30343b' },
  demoText: { color: '#65c98b', fontSize: 12, fontWeight: '800' },
  liveText: { color: '#727985', fontSize: 12, fontWeight: '800' },
  card: { backgroundColor: '#131821', borderWidth: 1, borderColor: '#202734', borderRadius: 16, padding: 16, gap: 8 },
  cardTitle: { color: '#f4f7fb', fontSize: 17, fontWeight: '800' },
  meta: { color: '#8994a6', fontSize: 12, lineHeight: 18 },
  bodyText: { color: '#c2cad6', fontSize: 13, lineHeight: 19 },
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
