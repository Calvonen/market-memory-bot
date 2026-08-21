import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';

import { ScreenShell, shared } from '@/components/screen-shell';
import { apiGet, ScannerResult } from '@/services/api';

const COUNTRIES = [
  { label: 'Suomi', value: 'Finland' },
  { label: 'Ruotsi', value: 'Sweden' },
  { label: 'Saksa', value: 'Germany' },
  { label: 'USA', value: 'USA' },
];

const SCOPES = ['Top', 'Full'] as const;
const SCOPE_LIMITS: Record<(typeof SCOPES)[number], number> = { Top: 10, Full: 25 };

export default function ScannerScreen() {
  const [country, setCountry] = useState('Finland');
  const [scope, setScope] = useState<(typeof SCOPES)[number]>('Top');
  const [data, setData] = useState<ScannerResult | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const latestRequestId = useRef(0);

  const market = `${country} ${scope}`;
  const limit = SCOPE_LIMITS[scope];

  // Invalidate any in-flight scanner request and drop the previous
  // market's data synchronously, right when the selection changes - before
  // the debounced loadScanner effect below even runs. A late response for
  // the old market can then never update state again, and the old results
  // can never render under the new "Valittu" label.
  function invalidateScan() {
    latestRequestId.current += 1;
    setData(null);
    setLoading(true);
    setError('');
  }

  const loadScanner = useCallback(async () => {
    const requestId = ++latestRequestId.current;
    setLoading(true);
    setError('');

    try {
      const result = await apiGet<ScannerResult>(
        `/api/v1/scanner?market=${encodeURIComponent(market)}&limit=${limit}`,
      );
      if (requestId === latestRequestId.current) {
        setData(result);
      }
    } catch (requestError) {
      if (requestId === latestRequestId.current) {
        setError(requestError instanceof Error ? requestError.message : 'Scanneri epäonnistui');
      }
    } finally {
      if (requestId === latestRequestId.current) {
        setLoading(false);
      }
    }
  }, [market, limit]);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadScanner();
    }, 0);

    return () => clearTimeout(timer);
  }, [loadScanner]);

  return (
    <ScreenShell
      title="Scanneri"
      subtitle="Valitse markkina ja etsi kiinnostavat osakkeet">
      <Text style={shared.heading}>Markkina</Text>
      <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: 8 }}>
        {COUNTRIES.map((item) => (
          <Pressable
            key={item.value}
            accessibilityRole="button"
            onPress={() => {
              // Re-tapping the already-selected country must be a no-op:
              // market/limit would not change, so the debounced load
              // effect below would never re-run and clear a loading state
              // set here - the spinner would be stuck on forever.
              if (item.value === country) return;
              invalidateScan();
              setCountry(item.value);
            }}
            style={[
              shared.card,
              { marginTop: 0, paddingVertical: 10, paddingHorizontal: 14 },
              country === item.value ? { borderColor: '#2997ff', borderWidth: 2 } : null,
            ]}>
            <Text style={shared.text}>{item.label}</Text>
          </Pressable>
        ))}
      </View>

      <Text style={shared.heading}>Laajuus</Text>
      <View style={{ flexDirection: 'row', gap: 8 }}>
        {SCOPES.map((item) => (
          <Pressable
            key={item}
            accessibilityRole="button"
            onPress={() => {
              // Same guard for scope: re-tapping the already-selected
              // scope must not touch loading/data/requestId either.
              if (item === scope) return;
              invalidateScan();
              setScope(item);
            }}
            style={[
              shared.card,
              { marginTop: 0, paddingVertical: 10, paddingHorizontal: 18 },
              scope === item ? { borderColor: '#2997ff', borderWidth: 2 } : null,
            ]}>
            <Text style={shared.text}>{item === 'Top' ? 'Top' : 'Full'}</Text>
          </Pressable>
        ))}
      </View>

      <Text style={shared.text}>Valittu: {market}</Text>
      {loading && <ActivityIndicator />}

      {!loading && error && (
        <>
          <Text style={{ color: '#ff8d8d' }}>{error}</Text>
          <Pressable
            accessibilityRole="button"
            onPress={loadScanner}
            style={shared.button}>
            <Text style={shared.buttonText}>Yritä uudelleen</Text>
          </Pressable>
        </>
      )}

      {!loading &&
        !error &&
        data?.results.map((row) => (
          <View key={row.ticker} style={shared.card}>
            <Text style={shared.heading}>
              {row.ticker} · {row.price.toFixed(2)}
            </Text>
            <Text style={shared.text}>
              {row.direction} · samankaltaisuus {row.best_similarity ?? '–'}
            </Text>
          </View>
        ))}

      {!loading && !error && data?.partial && (
        <Text style={shared.text}>
          Scanneri näyttää tässä vaiheessa {limit} ensimmäistä analysoitua osaketta.
        </Text>
      )}
    </ScreenShell>
  );
}
