import { Link } from 'expo-router';
import { useCallback, useEffect, useState } from 'react';
import {
  ActivityIndicator,
  FlatList,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';

import { BackButton } from '@/components/back-button';
import {
  AssistantProposal,
  getAssistantProposals,
} from '@/services/assistant-proposals';

function statusLabel(status: AssistantProposal['status']): string {
  switch (status) {
    case 'ready_for_review':
      return 'Valmis tarkistettavaksi';
    case 'approved_for_materialization':
      return 'Valmistelu hyväksytty';
    case 'materialized':
      return 'Valmisteltu';
    case 'draft':
      return 'Luonnos';
    case 'rejected':
      return 'Hylätty';
  }
}

export default function AssistantProposalListScreen() {
  const [proposals, setProposals] = useState<AssistantProposal[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  const load = useCallback(async () => {
    try {
      setError(null);
      setProposals(await getAssistantProposals());
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Ehdotusten lataus epäonnistui');
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  const onRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await load();
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={styles.content}
      data={proposals ?? []}
      keyExtractor={(item) => item.id}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor="#8a96a8" />
      }
      ListHeaderComponent={
        <View style={styles.header}>
          <BackButton label="Takaisin" />
          <Text style={styles.title}>Valmisteltavat ehdotukset</Text>
          <Text style={styles.subtitle}>
            Tarkista assistentin valmistelema tapahtuma ja strategia ennen kuin se lisätään MarketAI:n canonical seurantaan.
          </Text>
          <View style={styles.modeRow}>
            <View style={[styles.modeChip, styles.demoChip]}>
              <Text style={styles.demoText}>● DEMO</Text>
            </View>
            <View style={[styles.modeChip, styles.liveChip]}>
              <Text style={styles.liveText}>🔒 LIVE</Text>
            </View>
          </View>
          {proposals === null && !error ? <ActivityIndicator color="#8a96a8" /> : null}
          {error ? <Text style={styles.error}>{error}</Text> : null}
        </View>
      }
      ListEmptyComponent={
        proposals !== null && !error ? (
          <View style={styles.emptyCard}>
            <Text style={styles.emptyText}>Ei tarkistettavia ehdotuksia.</Text>
          </View>
        ) : null
      }
      renderItem={({ item }) => (
        <Link
          href={{ pathname: '/proposals/[proposalId]', params: { proposalId: item.id } }}
          asChild
        >
          <Pressable style={styles.card}>
            <View style={styles.rowBetween}>
              <View style={styles.titleBlock}>
                <Text style={styles.company}>{item.payload.company_name}</Text>
                <Text style={styles.symbol}>
                  {item.payload.instrument} · {item.payload.market}
                </Text>
              </View>
              <Text style={styles.date}>{item.payload.scheduled_date}</Text>
            </View>
            <Text style={styles.status}>{statusLabel(item.status)}</Text>
            <Text style={styles.strategy}>Strategia: Post-release confirmation</Text>
          </Pressable>
        </Link>
      )}
    />
  );
}

const styles = StyleSheet.create({
  screen: { flex: 1, backgroundColor: '#0b0e13' },
  content: { paddingHorizontal: 18, paddingTop: 58, paddingBottom: 48, gap: 12 },
  header: { gap: 10, marginBottom: 8 },
  title: { color: '#f4f7fb', fontSize: 24, fontWeight: '800' },
  subtitle: { color: '#8590a1', fontSize: 13, lineHeight: 19 },
  modeRow: { flexDirection: 'row', gap: 8, marginTop: 4, marginBottom: 6 },
  modeChip: { borderWidth: 1, borderRadius: 18, paddingHorizontal: 12, paddingVertical: 7 },
  demoChip: { backgroundColor: '#15281f', borderColor: '#28553d' },
  liveChip: { backgroundColor: '#17191d', borderColor: '#30343b' },
  demoText: { color: '#65c98b', fontSize: 12, fontWeight: '800' },
  liveText: { color: '#727985', fontSize: 12, fontWeight: '800' },
  error: { color: '#e17878', fontSize: 13 },
  emptyCard: { backgroundColor: '#131821', borderRadius: 16, padding: 18, borderWidth: 1, borderColor: '#202734' },
  emptyText: { color: '#8994a6' },
  card: { backgroundColor: '#131821', borderRadius: 16, padding: 16, borderWidth: 1, borderColor: '#202734', gap: 8 },
  rowBetween: { flexDirection: 'row', justifyContent: 'space-between', gap: 12 },
  titleBlock: { flex: 1 },
  company: { color: '#f4f7fb', fontSize: 16, fontWeight: '700' },
  symbol: { color: '#8994a6', marginTop: 3, fontSize: 12 },
  date: { color: '#aeb7c5', fontSize: 12 },
  status: { color: '#72b8db', fontSize: 12, fontWeight: '700' },
  strategy: { color: '#8994a6', fontSize: 12 },
});
