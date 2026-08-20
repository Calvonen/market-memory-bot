import { useState } from 'react';
import { ActivityIndicator, Pressable, Text, TextInput, View } from 'react-native';
import { ScreenShell, shared } from '@/components/screen-shell';
import { apiGet, MarketMemoryResult } from '@/services/api';

export default function MemoryScreen() {
  const [ticker, setTicker] = useState('AAPL'); const [data, setData] = useState<MarketMemoryResult | null>(null);
  const [loading, setLoading] = useState(false); const [error, setError] = useState<string | null>(null);
  async function analyze() { setLoading(true); setError(null); try { setData(await apiGet(`/api/v1/market-memory/${encodeURIComponent(ticker.trim())}`)); } catch (e) { setError(e instanceof Error ? e.message : 'Analyysi epäonnistui'); } finally { setLoading(false); } }
  return <ScreenShell title="Market Memory" subtitle="Vertaa nykyhetkeä historiallisiin käännekohtiin">
    <TextInput accessibilityLabel="Ticker" autoCapitalize="characters" value={ticker} onChangeText={setTicker} style={shared.input} placeholder="Ticker, esim. VALMT.HE" />
    <Pressable accessibilityRole="button" onPress={analyze} style={shared.button}><Text style={shared.buttonText}>Analysoi</Text></Pressable>
    {loading && <ActivityIndicator />}{error && <Text style={{ color: '#ff8d8d' }}>{error}</Text>}
    {!loading && !error && !data && <Text style={shared.text}>Syötä ticker aloittaaksesi analyysin.</Text>}
    {data && <><View style={shared.card}><Text style={shared.heading}>{data.ticker} · {data.price.toFixed(2)}</Text><Text style={shared.text}>Suunta: {data.result.direction} · 15 pv: {data.result.average_return_15d ?? '–'} %</Text><Text style={shared.text}>{data.trend}</Text><Text style={shared.text}>{data.momentum}</Text></View>
      <Text style={shared.heading}>Tärkeimmät analogiat</Text>{data.analog_matches.slice(0, 5).map(item => <View key={`${item.date}-${item.type}`} style={shared.card}><Text style={shared.heading}>{item.date} · {item.type}</Text><Text style={shared.text}>Samankaltaisuus {(item.score * 100).toFixed(1)} %</Text><Text style={shared.text}>Hinta {(item.scores.price * 100).toFixed(0)} % · Trendi {(item.scores.trend * 100).toFixed(0)} % · RSI {(item.scores.rsi * 100).toFixed(0)} %</Text></View>)}</>}
  </ScreenShell>;
}
