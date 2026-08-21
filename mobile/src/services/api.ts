export const API_URL = process.env.EXPO_PUBLIC_MARKETAI_API_URL ?? 'http://127.0.0.1:8001';
const READ_API_KEY = process.env.EXPO_PUBLIC_MARKETAI_READ_API_KEY ?? '';

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, { headers: { 'X-MarketAI-Key': READ_API_KEY } });
  if (!response.ok) {
    let message = `HTTP ${response.status}`;
    try {
      const payload = await response.json() as { detail?: string };
      if (payload.detail) message = payload.detail;
    } catch {
      // Keep the HTTP status fallback when the response is not JSON.
    }
    throw new Error(message);
  }
  return response.json() as Promise<T>;
}

export type SymbolSuggestion = { ticker: string; name: string; exchange: string };

export type MarketMemoryResult = {
  ticker: string; price: number; trend: string; momentum: string;
  result: { direction: string; average_return_15d: number | null };
  analog_matches: { date: string; type: string; score: number; scores: Record<string, number> }[];
  series: { date: string; close: number }[];
};

export type ScannerResult = { market: string; markets: string[]; partial: boolean; results: {
  ticker: string; price: number; direction: string; best_similarity: number | null; trend: string;
}[] };
