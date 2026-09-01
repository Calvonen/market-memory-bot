import { useCallback, useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';

import { ScreenShell, shared } from '@/components/screen-shell';
import { apiGet, ScannerResult } from '@/services/api';
import {
  getTrackedInstruments,
  TrackedInstrument,
  trackInstrument,
} from '@/services/tracked-instruments';

const COUNTRIES = [
  { label: 'Suomi', value: 'Finland' },
  { label: 'Ruotsi', value: 'Sweden' },
  { label: 'Saksa', value: 'Germany' },
  { label: 'USA', value: 'USA' },
];

const SCOPES = ['Top', 'Full'] as const;
const SCOPE_LIMITS: Record<(typeof SCOPES)[number], number> = { Top: 10, Full: 25 };
const TRACKING_ACTOR = 'mobile-scanner';

type TrackingStatus = 'saving' | 'error';

function matchesTrackedInstrument(
  item: TrackedInstrument,
  ticker: string,
  country: string,
): boolean {
  return (
    item.active &&
    item.instrument.trim().toUpperCase() === ticker.trim().toUpperCase() &&
    item.market.trim().toLowerCase() === country.trim().toLowerCase()
  );
}

export default function ScannerScreen() {
  const [country, setCountry] = useState('Finland');
  const [scope, setScope] = useState<(typeof SCOPES)[number]>('Top');
  const [data, setData] = useState<ScannerResult | null>(null);
  const [trackedInstruments, setTrackedInstruments] = useState<TrackedInstrument[]>([]);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);
  const [trackingStatus, setTrackingStatus] = useState<Record<string, TrackingStatus>>({});
  const latestRequestId = useRef(0);
  const trackedMutationVersion = useRef(0);

  const market = `${country} ${scope}`;
  const limit = SCOPE_LIMITS[scope];

  function invalidateScan() {
    latestRequestId.current += 1;
    setData(null);
    setLoading(true);
    setError('');
  }

  const loadScanner = useCallback(async () => {
    const requestId = ++latestRequestId.current;
    const trackedVersionAtStart = trackedMutationVersion.current;
    setLoading(true);
    setError('');

    void getTrackedInstruments()
      .then((canonicalTrackedInstruments) => {
        if (
          requestId === latestRequestId.current &&
          trackedVersionAtStart === trackedMutationVersion.current
        ) {
          setTrackedInstruments(canonicalTrackedInstruments);
        }
      })
      .catch(() => {
        // Keep the last known canonical annotations; scanner results remain usable.
      });

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

  async function addScannerResultToTracking(row: ScannerResult['results'][number]) {
    const trackingKey = `${country}:${row.ticker}`;
    trackedMutationVersion.current += 1;
    setTrackingStatus((current) => ({ ...current, [trackingKey]: 'saving' }));

    try {
      const saved = await trackInstrument(
        {
          instrument: row.ticker,
          company_name: '',
          market: country,
          source: 'scanner',
        },
        TRACKING_ACTOR,
      );
      trackedMutationVersion.current += 1;
      setTrackedInstruments((current) => [
        ...current.filter((item) => item.id !== saved.id),
        saved,
      ]);
      setTrackingStatus((current) => {
        const next = { ...current };
        delete next[trackingKey];
        return next;
      });
    } catch {
      setTrackingStatus((current) => ({ ...current, [trackingKey]: 'error' }));
    }
  }

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
          <Pressable accessibilityRole="button" onPress={loadScanner} style={shared.button}>
            <Text style={shared.buttonText}>Yritä uudelleen</Text>
          </Pressable>
        </>
      )}

      {!loading &&
        !error &&
        data?.results.map((row) => {
          const status = trackingStatus[`${country}:${row.ticker}`];
          const trackedInstrument = trackedInstruments.find((item) =>
            matchesTrackedInstrument(item, row.ticker, country),
          );
          const isTracked = Boolean(trackedInstrument);

          return (
            <View key={row.ticker} style={shared.card}>
              <Text style={shared.heading}>
                {row.ticker} · {row.price.toFixed(2)}
              </Text>
              <Text style={shared.text}>
                {row.direction} · samankaltaisuus {row.best_similarity ?? '–'}
              </Text>
              <Pressable
                accessibilityRole="button"
                disabled={status === 'saving' || isTracked}
                onPress={() => void addScannerResultToTracking(row)}
                style={shared.button}>
                <Text style={shared.buttonText}>
                  {status === 'saving'
                    ? 'Lisätään…'
                    : isTracked
                      ? 'Seurannassa'
                      : status === 'error'
                        ? 'Yritä uudelleen'
                        : 'Lisää seurantaan'}
                </Text>
              </Pressable>
            </View>
          );
        })}

      {!loading && !error && data?.partial && (
        <Text style={shared.text}>
          Scanneri näyttää tässä vaiheessa {limit} ensimmäistä analysoitua osaketta.
        </Text>
      )}
    </ScreenShell>
  );
}
