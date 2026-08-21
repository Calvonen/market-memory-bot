import { useEffect, useRef, useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';

import { ScreenShell, shared } from '@/components/screen-shell';
import { apiGet, MarketMemoryResult, SymbolSuggestion } from '@/services/api';

export default function MemoryScreen() {
  const [ticker, setTicker] = useState('AAPL');
  const [hasEditedTicker, setHasEditedTicker] = useState(false);
  const [suggestions, setSuggestions] = useState<SymbolSuggestion[]>([]);
  const [data, setData] = useState<MarketMemoryResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const latestRequestId = useRef(0);
  const latestSuggestionRequestId = useRef(0);

  const query = ticker.trim();
  const showSuggestions =
    hasEditedTicker && !loading && query.length >= 2 && data?.ticker !== query.toUpperCase();

  useEffect(() => {
    if (!showSuggestions) {
      return;
    }

    const requestId = ++latestSuggestionRequestId.current;
    const timer = setTimeout(async () => {
      try {
        const result = await apiGet<SymbolSuggestion[]>(
          `/api/v1/symbols?q=${encodeURIComponent(query)}&limit=6`,
        );
        if (requestId === latestSuggestionRequestId.current) {
          setSuggestions(result);
        }
      } catch {
        if (requestId === latestSuggestionRequestId.current) {
          setSuggestions([]);
        }
      }
    }, 250);

    return () => clearTimeout(timer);
  }, [showSuggestions, query]);

  async function analyze(selectedTicker?: string) {
    const requestId = ++latestRequestId.current;
    const requestedTicker = (selectedTicker ?? ticker).trim();
    if (selectedTicker) setTicker(selectedTicker);

    // Choosing a suggestion (or submitting directly) ends suggestion mode
    // right away: stop treating the field as "being edited" so the effect
    // below does not restart a suggestion search for the ticker we just
    // committed to, and invalidate/clear whatever suggestion state exists.
    setHasEditedTicker(false);
    latestSuggestionRequestId.current += 1;
    setSuggestions([]);
    setData(null);
    setLoading(true);
    setError(null);

    try {
      const result = await apiGet<MarketMemoryResult>(
        `/api/v1/market-memory/${encodeURIComponent(requestedTicker)}`,
      );

      if (requestId === latestRequestId.current) {
        setData(result);
      }
    } catch (requestError) {
      if (requestId === latestRequestId.current) {
        const message = requestError instanceof Error ? requestError.message : '';
        setError(
          message.includes('No data returned') || message.includes('Invalid ticker')
            ? 'Osaketta ei löytynyt. Valitse ehdotus tai tarkista pörssitunnus.'
            : message || 'Analyysi epäonnistui',
        );
      }
    } finally {
      if (requestId === latestRequestId.current) {
        setLoading(false);
      }
    }
  }

  return (
    <ScreenShell
      title="Market Memory"
      subtitle="Vertaa nykyhetkeä historiallisiin käännekohtiin">
      <TextInput
        accessibilityLabel="Osake tai ticker"
        autoCapitalize="characters"
        value={ticker}
        onChangeText={(value) => {
          setTicker(value);
          setHasEditedTicker(true);
          setError(null);
          // Drop stale suggestions and invalidate any in-flight suggestion
          // request immediately, instead of waiting for the next debounced
          // fetch to resolve — a late response for the old query must not
          // be able to bring old suggestions back.
          latestSuggestionRequestId.current += 1;
          setSuggestions([]);
        }}
        style={shared.input}
        placeholder="Yritys tai ticker, esim. Apple / AAPL"
      />

      {showSuggestions &&
        suggestions.map((item) => (
          <Pressable
            key={item.ticker}
            accessibilityRole="button"
            onPress={() => void analyze(item.ticker)}
            style={[shared.card, { marginTop: 0, paddingVertical: 12 }]}>
            <Text style={shared.heading}>{item.name}</Text>
            <Text style={shared.text}>
              {item.ticker}{item.exchange ? ` · ${item.exchange}` : ''}
            </Text>
          </Pressable>
        ))}

      <Pressable accessibilityRole="button" onPress={() => void analyze()} style={shared.button}>
        <Text style={shared.buttonText}>Analysoi</Text>
      </Pressable>
      {loading && <ActivityIndicator />}
      {error && <Text style={{ color: '#ff8d8d' }}>{error}</Text>}
      {!loading && !error && !data && (
        <Text style={shared.text}>Kirjoita yrityksen nimi tai pörssitunnus.</Text>
      )}
      {!loading && !error && data && (
        <>
          <View style={shared.card}>
            <Text style={shared.heading}>
              {data.ticker} · {data.price.toFixed(2)}
            </Text>
            <Text style={shared.text}>
              Suunta: {data.result.direction} · 15 pv:{' '}
              {data.result.average_return_15d ?? '–'} %
            </Text>
            <Text style={shared.text}>{data.trend}</Text>
            <Text style={shared.text}>{data.momentum}</Text>
          </View>
          <Text style={shared.heading}>Tärkeimmät analogiat</Text>
          {data.analog_matches.slice(0, 5).map((item) => (
            <View key={`${item.date}-${item.type}`} style={shared.card}>
              <Text style={shared.heading}>
                {item.date} · {item.type}
              </Text>
              <Text style={shared.text}>
                Samankaltaisuus {(item.score * 100).toFixed(1)} %
              </Text>
              <Text style={shared.text}>
                Hinta {(item.scores.price * 100).toFixed(0)} % · Trendi{' '}
                {(item.scores.trend * 100).toFixed(0)} % · RSI{' '}
                {(item.scores.rsi * 100).toFixed(0)} %
              </Text>
            </View>
          ))}
        </>
      )}
    </ScreenShell>
  );
}
