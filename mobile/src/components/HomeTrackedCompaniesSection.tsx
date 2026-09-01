import { useFocusEffect } from 'expo-router';
import { useCallback, useMemo, useRef, useState } from 'react';
import { ActivityIndicator, Alert, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';

import { TrackingProfileEditor } from '@/components/tracking-profile-editor';
import { apiGet } from '@/services/api';
import {
  deactivateTrackedInstrument,
  getTrackedInstruments,
  trackInstrument,
  TrackedInstrument,
} from '@/services/tracked-instruments';

const MANAGEMENT_ACTOR = 'mobile-home-tracking-management';
const SEARCH_TRACKING_ACTOR = 'mobile-home-company-search';

type SymbolSearchResult = {
  ticker: string;
  name: string;
  exchange: string;
};

type Props = { refreshToken?: number };

function canonicalMarketForSearchResult(result: SymbolSearchResult): string {
  const ticker = result.ticker.trim().toUpperCase();
  if (ticker.endsWith('.HE')) return 'Finland';
  if (ticker.endsWith('.ST')) return 'Sweden';
  if (ticker.endsWith('.DE')) return 'Germany';
  if (!ticker.includes('.')) return 'USA';
  return result.exchange.trim();
}

export function HomeTrackedCompaniesSection({ refreshToken = 0 }: Props) {
  const [instruments, setInstruments] = useState<TrackedInstrument[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [removingId, setRemovingId] = useState<string | null>(null);
  const [managementError, setManagementError] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<SymbolSearchResult[]>([]);
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState<string | null>(null);
  const [addingTicker, setAddingTicker] = useState<string | null>(null);
  const latestLoadId = useRef(0);
  const latestSearchId = useRef(0);

  const load = useCallback(() => {
    const loadId = ++latestLoadId.current;
    setError(null);
    setInstruments(null);
    return getTrackedInstruments()
      .then((list) => {
        if (loadId !== latestLoadId.current) return;
        setInstruments(list);
      })
      .catch((err) => {
        if (loadId !== latestLoadId.current) return;
        setInstruments(null);
        setExpandedId(null);
        setError(err instanceof Error ? err.message : 'Seurattuja yhtiöitä ei juuri nyt saatu haettua.');
      });
  }, []);

  useFocusEffect(
    useCallback(() => {
      const timer = setTimeout(() => void load(), 0);
      return () => clearTimeout(timer);
    }, [load, refreshToken]),
  );

  const activeInstruments = useMemo(
    () => (instruments ?? []).filter((item) => item.active).sort((a, b) => a.instrument.localeCompare(b.instrument)),
    [instruments],
  );
  const trackedStateReady = instruments !== null && error === null;

  function isTickerTracked(ticker: string): boolean {
    const normalizedTicker = ticker.trim().toUpperCase();
    return activeInstruments.some((item) => item.instrument.trim().toUpperCase() === normalizedTicker);
  }

  function changeSearchQuery(value: string) {
    latestSearchId.current += 1;
    setSearchQuery(value);
    setSearchResults([]);
    setSearchError(null);
    setSearching(false);
  }

  async function searchCompanies() {
    const query = searchQuery.trim();
    const searchId = ++latestSearchId.current;
    setSearchError(null);
    setSearchResults([]);
    if (!query) return;

    setSearching(true);
    try {
      const results = await apiGet<SymbolSearchResult[]>(
        `/api/v1/symbols?q=${encodeURIComponent(query)}&limit=8`,
      );
      if (searchId !== latestSearchId.current) return;
      setSearchResults(results);
    } catch (err) {
      if (searchId !== latestSearchId.current) return;
      setSearchError(err instanceof Error ? err.message : 'Yhtiöhaku epäonnistui.');
    } finally {
      if (searchId === latestSearchId.current) setSearching(false);
    }
  }

  async function addSearchResult(result: SymbolSearchResult) {
    if (!trackedStateReady || addingTicker || isTickerTracked(result.ticker)) return;
    const ticker = result.ticker.trim().toUpperCase();
    setAddingTicker(ticker);
    setSearchError(null);
    try {
      const saved = await trackInstrument(
        {
          instrument: ticker,
          company_name: result.name,
          market: canonicalMarketForSearchResult(result),
          source: 'manual',
        },
        SEARCH_TRACKING_ACTOR,
      );
      // Reconcile from the canonical registry instead of constructing a local
      // list from a snapshot that may have been invalidated by a concurrent refresh.
      latestLoadId.current += 1;
      await load();
      setExpandedId(saved.id);
    } catch (err) {
      setSearchError(err instanceof Error ? err.message : 'Yhtiön lisääminen seurantaan epäonnistui.');
    } finally {
      setAddingTicker(null);
    }
  }

  async function remove(instrument: TrackedInstrument) {
    if (removingId) return;
    setRemovingId(instrument.id);
    setManagementError(null);
    try {
      const saved = await deactivateTrackedInstrument(instrument.id, MANAGEMENT_ACTOR);
      if (saved.active) throw new Error('Seurannan poistaminen ei vahvistunut palvelimelta.');
      // Invalidate any GET that started before the canonical mutation completed.
      // Otherwise a stale active snapshot could arrive after this response and
      // resurrect the removed card until the next refresh.
      latestLoadId.current += 1;
      setInstruments((current) => (current ?? []).map((item) => item.id === saved.id ? saved : item));
      setExpandedId(null);
    } catch (err) {
      setManagementError(err instanceof Error ? err.message : 'Seurannasta poistaminen epäonnistui.');
    } finally {
      setRemovingId(null);
    }
  }

  function confirmRemove(instrument: TrackedInstrument) {
    Alert.alert(
      'Poista seurannasta?',
      `${instrument.company_name || instrument.instrument} poistetaan aktiivisesta seurannasta. Historia säilyy.`,
      [
        { text: 'Peruuta', style: 'cancel' },
        { text: 'Poista seurannasta', style: 'destructive', onPress: () => void remove(instrument) },
      ],
    );
  }

  return (
    <View style={styles.section}>
      <Text style={styles.sectionTitle}>SEURATUT YHTIÖT</Text>

      <View style={styles.searchCard}>
        <Text style={styles.searchHeading}>Lisää yhtiö</Text>
        <TextInput
          value={searchQuery}
          onChangeText={changeSearchQuery}
          onSubmitEditing={() => void searchCompanies()}
          placeholder="Hae nimellä tai tickerillä"
          placeholderTextColor="#687386"
          autoCapitalize="none"
          returnKeyType="search"
          style={styles.searchInput}
        />
        <Pressable
          accessibilityRole="button"
          disabled={searching || !searchQuery.trim()}
          onPress={() => void searchCompanies()}
          style={styles.searchButton}>
          <Text style={styles.searchButtonText}>{searching ? 'Haetaan…' : 'Hae yhtiö'}</Text>
        </Pressable>
        {searching ? <ActivityIndicator color="#8a96a8" style={styles.searchLoader} /> : null}
        {searchError ? <Text style={styles.errorText}>{searchError}</Text> : null}
        {!searching && !searchError && searchResults.length === 0 && searchQuery.trim() ? (
          <Text style={styles.searchHint}>Kirjoita nimi tai ticker ja paina Hae yhtiö.</Text>
        ) : null}
        {searchResults.map((result) => {
          const tracked = isTickerTracked(result.ticker);
          const adding = addingTicker === result.ticker.trim().toUpperCase();
          return (
            <View key={`${result.ticker}:${result.exchange}`} style={styles.searchResult}>
              <View style={styles.titleBlock}>
                <Text style={styles.company} numberOfLines={1}>{result.name || result.ticker}</Text>
                <Text style={styles.symbol}>
                  {result.ticker}{result.exchange ? ` · ${result.exchange}` : ''}
                </Text>
              </View>
              <Pressable
                accessibilityRole="button"
                disabled={!trackedStateReady || adding || tracked}
                onPress={() => void addSearchResult(result)}
                style={styles.addButton}>
                <Text style={styles.addButtonText}>
                  {!trackedStateReady ? 'Odotetaan…' : adding ? 'Lisätään…' : tracked ? 'Seurannassa' : 'Lisää'}
                </Text>
              </Pressable>
            </View>
          );
        })}
      </View>

      {!instruments && !error ? <ActivityIndicator color="#8a96a8" style={styles.loader} /> : null}
      {error ? (
        <View style={styles.errorCard}>
          <Text style={styles.errorText}>{error}</Text>
          <Pressable style={styles.retryButton} onPress={() => void load()}>
            <Text style={styles.retryButtonText}>Yritä uudelleen</Text>
          </Pressable>
        </View>
      ) : null}
      {managementError ? <Text style={styles.errorText}>{managementError}</Text> : null}
      {instruments && activeInstruments.length === 0 && !error ? (
        <View style={styles.emptyCard}><Text style={styles.emptyText}>Ei vielä seurattuja yhtiöitä.</Text></View>
      ) : null}
      {activeInstruments.map((instrument) => {
        const expanded = expandedId === instrument.id;
        return (
          <View key={instrument.id} style={styles.companyCard}>
            <Pressable style={styles.companyHeader} onPress={() => setExpandedId(expanded ? null : instrument.id)}>
              <View style={styles.titleBlock}>
                <Text style={styles.company} numberOfLines={1}>{instrument.company_name || instrument.instrument}</Text>
                <Text style={styles.symbol}>{instrument.instrument}</Text>
              </View>
              <View style={styles.rightBlock}>
                {instrument.market ? <Text style={styles.market}>{instrument.market}</Text> : null}
                <Text style={styles.manageText}>{expanded ? 'Sulje' : 'Hallitse'}</Text>
              </View>
            </Pressable>
            {expanded ? (
              <View style={styles.managementBlock}>
                <TrackingProfileEditor trackedInstrumentId={instrument.id} />
                <Pressable disabled={removingId === instrument.id} onPress={() => confirmRemove(instrument)} style={styles.removeButton}>
                  <Text style={styles.removeButtonText}>{removingId === instrument.id ? 'Poistetaan…' : 'Poista seurannasta'}</Text>
                </Pressable>
              </View>
            ) : null}
          </View>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  section: { marginBottom: 24 },
  sectionTitle: { color: '#687386', fontSize: 11, fontWeight: '800', letterSpacing: 1.4, marginBottom: 11, marginLeft: 3 },
  loader: { marginVertical: 16 },
  searchCard: { backgroundColor: '#10151d', borderWidth: 1, borderColor: '#202734', borderRadius: 16, padding: 14, marginBottom: 12 },
  searchHeading: { color: '#f4f7fb', fontSize: 15, fontWeight: '700', marginBottom: 9 },
  searchInput: { color: '#f4f7fb', backgroundColor: '#131821', borderWidth: 1, borderColor: '#293140', borderRadius: 12, paddingHorizontal: 13, paddingVertical: 11, fontSize: 15 },
  searchButton: { backgroundColor: '#1e5f86', borderRadius: 12, paddingVertical: 10, alignItems: 'center', marginTop: 9 },
  searchButtonText: { color: '#eef8ff', fontSize: 13, fontWeight: '800' },
  searchLoader: { marginTop: 10 },
  searchHint: { color: '#687386', fontSize: 12, marginTop: 10 },
  searchResult: { flexDirection: 'row', alignItems: 'center', gap: 10, borderTopWidth: 1, borderTopColor: '#202734', paddingTop: 11, marginTop: 11 },
  addButton: { backgroundColor: '#172a36', borderWidth: 1, borderColor: '#28546b', borderRadius: 10, paddingHorizontal: 12, paddingVertical: 8 },
  addButtonText: { color: '#72b8db', fontSize: 12, fontWeight: '800' },
  companyCard: { backgroundColor: '#131821', borderWidth: 1, borderColor: '#202734', borderRadius: 16, padding: 16, marginBottom: 10 },
  companyHeader: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  titleBlock: { flex: 1, paddingRight: 12 },
  rightBlock: { alignItems: 'flex-end', gap: 5 },
  company: { color: '#f4f7fb', fontSize: 17, fontWeight: '700' },
  symbol: { color: '#8590a1', fontSize: 13, marginTop: 2 },
  market: { color: '#aab3c2', fontSize: 12, fontWeight: '600' },
  manageText: { color: '#72b8db', fontSize: 12, fontWeight: '700' },
  managementBlock: { borderTopWidth: 1, borderTopColor: '#202734', marginTop: 14, paddingTop: 10 },
  removeButton: { borderWidth: 1, borderColor: '#5a2b31', backgroundColor: '#241519', borderRadius: 12, paddingVertical: 11, paddingHorizontal: 14, marginTop: 14, alignItems: 'center' },
  removeButtonText: { color: '#ef8c96', fontSize: 13, fontWeight: '800' },
  emptyCard: { backgroundColor: '#131821', borderWidth: 1, borderColor: '#202734', borderRadius: 16, padding: 18 },
  emptyText: { color: '#8994a6', fontSize: 14 },
  errorCard: { backgroundColor: '#1c1417', borderWidth: 1, borderColor: '#3a2226', borderRadius: 16, padding: 16 },
  errorText: { color: '#e17878', fontSize: 13, marginTop: 10 },
  retryButton: { alignSelf: 'flex-start', backgroundColor: '#2a1b1e', borderWidth: 1, borderColor: '#4a2b30', borderRadius: 12, paddingHorizontal: 14, paddingVertical: 8, marginTop: 12 },
  retryButtonText: { color: '#e17878', fontSize: 12, fontWeight: '800' },
});
