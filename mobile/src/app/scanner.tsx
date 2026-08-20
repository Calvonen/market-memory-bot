import { useCallback, useEffect, useState } from 'react';
import { ActivityIndicator, Pressable, Text, View } from 'react-native';

import { ScreenShell, shared } from '@/components/screen-shell';
import { apiGet, ScannerResult } from '@/services/api';

export default function ScannerScreen() {
  const [data, setData] = useState<ScannerResult | null>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  const loadScanner = useCallback(async () => {
    setLoading(true);
    setError('');

    try {
      const result = await apiGet<ScannerResult>(
        '/api/v1/scanner?market=Finland%20Top&limit=10',
      );
      setData(result);
    } catch (requestError) {
      setError(String(requestError));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      void loadScanner();
    }, 0);

    return () => clearTimeout(timer);
  }, [loadScanner]);

  return (
    <ScreenShell
      title="Scanneri"
      subtitle="Ensimmäinen read-only markkinataulukko">
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
              {row.direction} · score {row.best_similarity ?? '–'}
            </Text>
          </View>
        ))}

      {!loading && !error && data?.partial && (
        <Text style={shared.text}>
          Täysi scanner-feature parity toteutetaan PR #74:ssä.
        </Text>
      )}
    </ScreenShell>
  );
}
